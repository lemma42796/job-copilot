"""认证 + 余额端点(P0 / P1)。

挂在 `/api` 下,实际路径 `/api/auth/*` 与 `/api/billing/*`。

充值是**模拟实现** — 直接改余额,不接支付网关、不做对账与发票。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.infra.auth import CurrentUserId
from jobcopilot_api.infra.db import get_session
from jobcopilot_api.schemas.auth import (
    BalanceOut,
    BalanceTransactionListOut,
    BalanceTransactionOut,
    LoginIn,
    RegisterIn,
    SpendByFeatureItem,
    SpendSummaryOut,
    TokenOut,
    TopupIn,
    UserOut,
)
from jobcopilot_api.services import auth_service, billing_service

router = APIRouter(tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/auth/register", response_model=TokenOut, status_code=201)
async def register(payload: RegisterIn, session: SessionDep) -> TokenOut:
    user = await auth_service.register(
        session,
        email=payload.email,
        password=payload.password,
        name=payload.name,
    )
    # 先签 token 再 commit:签发失败(如 AUTH_SECRET 未配置)时
    # get_session 会 rollback,避免“用户已落库但拿不到 token”的残留。
    token = auth_service.issue_token(user.id)
    await session.commit()
    return TokenOut(
        access_token=token.access_token,
        expires_at=token.expires_at,
        user_id=user.id,
    )


@router.post("/auth/login", response_model=TokenOut)
async def login(payload: LoginIn, session: SessionDep) -> TokenOut:
    user = await auth_service.authenticate(
        session, email=payload.email, password=payload.password
    )
    token = auth_service.issue_token(user.id)
    return TokenOut(
        access_token=token.access_token,
        expires_at=token.expires_at,
        user_id=user.id,
    )


@router.get("/auth/me", response_model=UserOut)
async def me(user_id: CurrentUserId, session: SessionDep) -> UserOut:
    user = await auth_service.get_active_user(session, user_id)
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        locale=user.locale,
        created_at=user.created_at,
    )


@router.get("/billing/balance", response_model=BalanceOut)
async def get_balance(user_id: CurrentUserId, session: SessionDep) -> BalanceOut:
    snapshot = await billing_service.get_balance(session, user_id)
    return BalanceOut(**snapshot.__dict__)


@router.post("/billing/topup", response_model=BalanceOut)
async def topup(
    payload: TopupIn,
    user_id: CurrentUserId,
    session: SessionDep,
) -> BalanceOut:
    snapshot = await billing_service.topup(
        session,
        user_id=user_id,
        amount_cny=payload.amount_cny,
        note=payload.note or "simulated_topup",
    )
    await session.commit()
    return BalanceOut(**snapshot.__dict__)


@router.get("/billing/transactions", response_model=BalanceTransactionListOut)
async def list_transactions(
    user_id: CurrentUserId,
    session: SessionDep,
    cursor: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> BalanceTransactionListOut:
    rows = await billing_service.list_transactions(
        session, user_id=user_id, cursor=cursor, limit=limit + 1
    )
    has_more = len(rows) > limit
    visible = rows[:limit]
    items = [
        BalanceTransactionOut(
            id=row.id,
            kind=row.kind,
            amount_cny=row.amount_cny,
            balance_after_cny=row.balance_after_cny,
            channel=row.channel,
            feature=row.feature,
            llm_call_id=row.llm_call_id,
            job_id=row.job_id,
            note=row.note,
            created_at=row.created_at,
        )
        for row in visible
    ]
    return BalanceTransactionListOut(
        items=items,
        next_cursor=items[-1].id if has_more and items else None,
        has_more=has_more,
    )


@router.get("/billing/spend-summary", response_model=SpendSummaryOut)
async def spend_summary(
    user_id: CurrentUserId,
    session: SessionDep,
) -> SpendSummaryOut:
    """按 (链路, 功能) 两个维度的成本归因,与改造前的 llm_calls 归因等价。"""
    rows = await billing_service.spend_by_feature(session, user_id=user_id)
    snapshot = await billing_service.get_balance(session, user_id)
    return SpendSummaryOut(
        user_id=user_id,
        total_spent_cny=snapshot.total_spent_cny,
        items=[
            SpendByFeatureItem(channel=channel, feature=feature, spent_cny=spent)
            for channel, feature, spent in rows
        ],
    )
