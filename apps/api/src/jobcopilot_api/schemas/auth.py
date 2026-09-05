"""认证与余额相关的请求 / 响应 schema(P0 / P1)。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class RegisterIn(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=100)


class LoginIn(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: int = Field(description="令牌过期时间(epoch 秒)")
    user_id: int


class UserOut(BaseModel):
    id: int
    email: str
    name: str | None
    locale: str
    created_at: datetime


class BalanceOut(BaseModel):
    user_id: int
    balance_cny: Decimal
    total_topup_cny: Decimal
    total_spent_cny: Decimal


class TopupIn(BaseModel):
    amount_cny: Decimal = Field(gt=0, description="模拟充值金额,不接真实支付")
    note: str | None = Field(default=None, max_length=200)


class BalanceTransactionOut(BaseModel):
    id: int
    kind: str
    amount_cny: Decimal
    balance_after_cny: Decimal
    channel: str | None
    feature: str | None
    llm_call_id: int | None
    job_id: int | None
    note: str | None
    created_at: datetime


class BalanceTransactionListOut(BaseModel):
    items: list[BalanceTransactionOut]
    next_cursor: int | None
    has_more: bool


class SpendByFeatureItem(BaseModel):
    channel: str
    feature: str
    spent_cny: Decimal


class SpendSummaryOut(BaseModel):
    user_id: int
    total_spent_cny: Decimal
    items: list[SpendByFeatureItem]
