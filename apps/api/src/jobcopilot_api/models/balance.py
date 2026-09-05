"""余额账本(P1)。

两张表:

- `user_balances` — 每用户一行的当前余额,`user_id` 是主键。所有扣费走
  `UPDATE ... SET balance_cny = balance_cny - :amount` 的原子写,不做
  读-改-写,避免并发调用互相覆盖。
- `balance_transactions` — 流水。每一条对应一次余额变动,`kind` 区分
  `topup`(模拟充值)与 `charge`(按 `llm_calls.cost_cny` 实扣)。
  `llm_call_id` 关联到具体调用,让"按用户 / 按功能"两个维度的成本归因
  与改造前一致。

透支:成本只有调用返回后才知道,`balance_cny` 允许短暂为负,上界是
"该用户同时在飞的调用数 × 单次调用成本"。余额 <= 0 时闸门拒绝新调用。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models.base import Base, IDMixin

TOPUP = "topup"
CHARGE = "charge"
ADJUST = "adjust"

BALANCE_TRANSACTION_KINDS: tuple[str, ...] = (TOPUP, CHARGE, ADJUST)


class UserBalance(Base):
    __tablename__ = "user_balances"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    balance_cny: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, server_default="0"
    )
    total_topup_cny: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, server_default="0"
    )
    total_spent_cny: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BalanceTransaction(Base, IDMixin):
    __tablename__ = "balance_transactions"

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_cny: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    """正数入账(充值),负数出账(扣费)。"""
    balance_after_cny: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)

    # 成本归因:feature 支撑"按功能"维度,link_* 支撑追溯到具体调用。
    feature: Mapped[str | None] = mapped_column(String(50))
    channel: Mapped[str | None] = mapped_column(String(20))
    """generation | rerank | embedding — 三条计费链路。"""
    llm_call_id: Mapped[int | None] = mapped_column(BigInteger)
    job_id: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(Text())

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
