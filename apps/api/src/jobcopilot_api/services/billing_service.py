"""余额账本与扣费(P1)。

三条计费链路(文本生成 / rerank / embedding)全部经过本模块:

- 调用前 `assert_can_spend(user_id)` 检查余额 > 0,不足直接抛
  `InsufficientBalanceError`。
- 调用后 `charge(...)` 按实际 `cost_cny` 原子扣减并写一条流水。

成本后验:token 用量只有调用返回后才知道,不做预授权。允许的透支上界
是"该用户同时在飞的调用数 × 单次调用成本",不做任务级预检查,也不回滚
已经产生的业务结果。

扣费写的是**独立 session**(`get_sessionmaker()`),与业务事务解耦 —
业务侧回滚不能把已发生的上游成本一起抹掉。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.errors import JobCopilotError
from jobcopilot_api.infra.db import get_sessionmaker
from jobcopilot_api.models.balance import (
    ADJUST,
    CHARGE,
    TOPUP,
    BalanceTransaction,
    UserBalance,
)

log = structlog.get_logger(__name__)

CHANNEL_GENERATION = "generation"
CHANNEL_RERANK = "rerank"
CHANNEL_EMBEDDING = "embedding"

ZERO = Decimal("0")


class InsufficientBalanceError(JobCopilotError):
    """余额耗尽。402 = 需要付费才能继续,语义比 403 更贴。

    `balance_cny` 放进 errors,前端据此直接展示当前余额并引导充值。
    """

    status_code = 402
    code = "insufficient_balance"
    title = "余额不足"

    def __init__(self, detail: str = "", *, balance_cny: Decimal = ZERO) -> None:
        super().__init__(
            detail or "账户余额已耗尽,请充值后继续",
            errors=[{"balance_cny": str(balance_cny)}],
        )
        self.balance_cny = balance_cny


@dataclass(frozen=True)
class BalanceSnapshot:
    user_id: int
    balance_cny: Decimal
    total_topup_cny: Decimal
    total_spent_cny: Decimal


# ---------- 读 ----------


async def ensure_balance_row(session: AsyncSession, user_id: int) -> None:
    """幂等建行。注册时调用;老用户首次扣费时也会兜底。"""
    await session.execute(
        pg_insert(UserBalance)
        .values(user_id=user_id, balance_cny=ZERO)
        .on_conflict_do_nothing(index_elements=[UserBalance.user_id])
    )


async def get_balance(session: AsyncSession, user_id: int) -> BalanceSnapshot:
    row = (
        await session.execute(
            sa.select(UserBalance).where(UserBalance.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return BalanceSnapshot(user_id, ZERO, ZERO, ZERO)
    return BalanceSnapshot(
        user_id=user_id,
        balance_cny=Decimal(row.balance_cny),
        total_topup_cny=Decimal(row.total_topup_cny),
        total_spent_cny=Decimal(row.total_spent_cny),
    )


async def current_balance(user_id: int) -> Decimal:
    """独立 session 读余额,供闸门在请求事务之外调用。"""
    async with get_sessionmaker()() as session:
        return (await get_balance(session, user_id)).balance_cny


async def assert_can_spend(user_id: int | None) -> None:
    """调用前闸门。余额 <= 0 直接拒绝新调用。

    `user_id` 为 None 表示系统内部调用(评测脚本 / 无归属任务),不计费
    也不拦截 — 这类调用不经过在线接口,不属于用户消费。
    """
    if user_id is None:
        return
    balance = await current_balance(user_id)
    if balance <= ZERO:
        raise InsufficientBalanceError(balance_cny=balance)


# ---------- 写 ----------


async def topup(
    session: AsyncSession,
    *,
    user_id: int,
    amount_cny: Decimal | str | int,
    note: str | None = None,
) -> BalanceSnapshot:
    """模拟充值 — 直接改余额,不接支付。"""
    amount = Decimal(str(amount_cny))
    if amount <= ZERO:
        raise JobCopilotError("充值金额必须为正数")
    await ensure_balance_row(session, user_id)
    after = await _apply_delta(
        session,
        user_id=user_id,
        delta=amount,
        topup_delta=amount,
        spent_delta=ZERO,
    )
    session.add(
        BalanceTransaction(
            user_id=user_id,
            kind=TOPUP,
            amount_cny=amount,
            balance_after_cny=after,
            note=note,
        )
    )
    await session.flush()
    return await get_balance(session, user_id)


async def adjust(
    session: AsyncSession,
    *,
    user_id: int,
    amount_cny: Decimal,
    note: str,
) -> BalanceSnapshot:
    """运营侧手工调账(可正可负),流水与充值 / 扣费区分开。"""
    await ensure_balance_row(session, user_id)
    after = await _apply_delta(
        session,
        user_id=user_id,
        delta=amount_cny,
        topup_delta=ZERO,
        spent_delta=ZERO,
    )
    session.add(
        BalanceTransaction(
            user_id=user_id,
            kind=ADJUST,
            amount_cny=amount_cny,
            balance_after_cny=after,
            note=note,
        )
    )
    await session.flush()
    return await get_balance(session, user_id)


async def charge(
    *,
    user_id: int | None,
    cost_cny: Decimal,
    channel: str,
    feature: str | None = None,
    llm_call_id: int | None = None,
    job_id: int | None = None,
) -> None:
    """按实际成本扣费。独立 session,失败只记 WARNING 不阻断业务。

    零成本(缓存命中 / 未知模型)不写流水 — 用户天然受益于缓存。
    """
    if user_id is None or cost_cny is None or Decimal(cost_cny) <= ZERO:
        return
    amount = Decimal(cost_cny)
    try:
        async with get_sessionmaker()() as session:
            await ensure_balance_row(session, user_id)
            after = await _apply_delta(
                session,
                user_id=user_id,
                delta=-amount,
                topup_delta=ZERO,
                spent_delta=amount,
            )
            session.add(
                BalanceTransaction(
                    user_id=user_id,
                    kind=CHARGE,
                    amount_cny=-amount,
                    balance_after_cny=after,
                    feature=feature,
                    channel=channel,
                    llm_call_id=llm_call_id,
                    job_id=job_id,
                )
            )
            await session.commit()
    except Exception as exc:  # 计费失败不能把成功的业务调用带崩
        log.warning(
            "balance_charge_failed",
            user_id=user_id,
            channel=channel,
            feature=feature,
            cost_cny=str(amount),
            error=str(exc),
        )


async def _apply_delta(
    session: AsyncSession,
    *,
    user_id: int,
    delta: Decimal,
    topup_delta: Decimal,
    spent_delta: Decimal,
) -> Decimal:
    """原子加减,返回变动后的余额。不做读-改-写,避免并发覆盖。"""
    result = await session.execute(
        sa.update(UserBalance)
        .where(UserBalance.user_id == user_id)
        .values(
            balance_cny=UserBalance.balance_cny + delta,
            total_topup_cny=UserBalance.total_topup_cny + topup_delta,
            total_spent_cny=UserBalance.total_spent_cny + spent_delta,
            updated_at=sa.func.now(),
        )
        .returning(UserBalance.balance_cny)
    )
    row = result.scalar_one_or_none()
    return Decimal(row) if row is not None else ZERO


async def list_transactions(
    session: AsyncSession,
    *,
    user_id: int,
    cursor: int | None = None,
    limit: int = 50,
) -> list[BalanceTransaction]:
    limit = max(1, min(limit, 200))
    stmt = (
        sa.select(BalanceTransaction)
        .where(BalanceTransaction.user_id == user_id)
        .order_by(BalanceTransaction.id.desc())
        .limit(limit)
    )
    if cursor is not None:
        stmt = stmt.where(BalanceTransaction.id < cursor)
    return list((await session.execute(stmt)).scalars().all())


async def spend_by_feature(
    session: AsyncSession,
    *,
    user_id: int,
) -> list[tuple[str, str, Decimal]]:
    """按 (channel, feature) 聚合消费,支撑成本归因的两个查询维度。"""
    rows = (
        await session.execute(
            sa.select(
                sa.func.coalesce(BalanceTransaction.channel, "unknown"),
                sa.func.coalesce(BalanceTransaction.feature, "unknown"),
                sa.func.sum(-BalanceTransaction.amount_cny),
            )
            .where(BalanceTransaction.user_id == user_id)
            .where(BalanceTransaction.kind == CHARGE)
            .group_by(BalanceTransaction.channel, BalanceTransaction.feature)
        )
    ).all()
    return [(str(a), str(b), Decimal(c or 0)) for a, b, c in rows]
