"""注册 / 登录 / 会话令牌(P0)。

密码哈希与令牌签名都走标准库(`hashlib.pbkdf2_hmac` + `hmac`),不引入
passlib / python-jose,减少依赖面。令牌是自包含的签名串,格式:

    <user_id>.<expires_at_epoch>.<hmac_sha256_hex>

服务端不存会话表:令牌过期时间写在串里,签名用 `settings.auth_secret`。
改 secret 即全体登出。令牌默认有效期 `settings.auth_token_ttl_seconds`。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jobcopilot_api.errors import ConflictError, JobCopilotError, ValidationError
from jobcopilot_api.models.user import User
from jobcopilot_api.services import billing_service
from jobcopilot_api.settings import settings

PBKDF2_ITERATIONS = 240_000
PBKDF2_ALGORITHM = "pbkdf2_sha256"
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


class AuthenticationError(JobCopilotError):
    status_code = 401
    code = "unauthorized"
    title = "未认证或令牌无效"


class EmailAlreadyRegisteredError(ConflictError):
    code = "email_already_registered"
    title = "该邮箱已注册"


class InvalidCredentialsError(JobCopilotError):
    status_code = 401
    code = "invalid_credentials"
    title = "邮箱或密码不正确"


@dataclass(frozen=True)
class IssuedToken:
    access_token: str
    expires_at: int


# ---------- 密码 ----------


def hash_password(password: str) -> str:
    _validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"{PBKDF2_ALGORITHM}${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, raw_iterations, salt_hex, digest_hex = stored.split("$", 3)
    except ValueError:
        return False
    if algorithm != PBKDF2_ALGORITHM:
        return False
    try:
        iterations = int(raw_iterations)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(candidate, expected)


def _validate_password(password: str) -> None:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise ValidationError(
            f"密码长度必须在 {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} 之间"
        )


# ---------- 令牌 ----------


def _secret() -> bytes:
    secret = settings.auth_secret.strip()
    if not secret:
        # 空 secret 会让任何人都能伪造令牌,直接拒绝启动认证链路。
        raise AuthenticationError(
            "JOBCOPILOT_AUTH_SECRET 未配置,认证链路不可用"
        )
    return secret.encode("utf-8")


def _sign(payload: str) -> str:
    mac = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")


def issue_token(user_id: int, *, now: int | None = None) -> IssuedToken:
    issued_at = int(time.time()) if now is None else now
    expires_at = issued_at + settings.auth_token_ttl_seconds
    payload = f"{user_id}.{expires_at}"
    return IssuedToken(
        access_token=f"{payload}.{_sign(payload)}",
        expires_at=expires_at,
    )


def parse_token(token: str, *, now: int | None = None) -> int:
    """校验签名与有效期,返回 user_id。任何异常统一成 401。"""
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise AuthenticationError("令牌格式非法")
    raw_user_id, raw_expires_at, signature = parts
    payload = f"{raw_user_id}.{raw_expires_at}"
    if not hmac.compare_digest(_sign(payload), signature):
        raise AuthenticationError("令牌签名校验失败")
    try:
        user_id = int(raw_user_id)
        expires_at = int(raw_expires_at)
    except ValueError as exc:
        raise AuthenticationError("令牌载荷非法") from exc
    current = int(time.time()) if now is None else now
    if expires_at <= current:
        raise AuthenticationError("令牌已过期,请重新登录")
    return user_id


# ---------- 注册 / 登录 ----------


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if "@" not in normalized or len(normalized) > 255:
        raise ValidationError("email 格式非法")
    return normalized


async def register(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    name: str | None = None,
) -> User:
    normalized = normalize_email(email)
    user = User(
        email=normalized,
        password_hash=hash_password(password),
        name=(name or "").strip() or None,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise EmailAlreadyRegisteredError(f"{normalized} 已注册") from exc

    # 新用户建余额行,并按配置发放模拟赠额(默认 0)。
    await billing_service.ensure_balance_row(session, user.id)
    if settings.billing_signup_grant_cny > 0:
        await billing_service.topup(
            session,
            user_id=user.id,
            amount_cny=settings.billing_signup_grant_cny,
            note="signup_grant",
        )
    return user


async def authenticate(
    session: AsyncSession,
    *,
    email: str,
    password: str,
) -> User:
    normalized = normalize_email(email)
    user = (
        await session.execute(
            sa.select(User)
            .where(User.email == normalized)
            .where(User.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentialsError("邮箱或密码不正确")
    if not user.is_active:
        raise InvalidCredentialsError("账号已停用")
    return user


async def get_active_user(session: AsyncSession, user_id: int) -> User:
    user = (
        await session.execute(
            sa.select(User)
            .where(User.id == user_id)
            .where(User.deleted_at.is_(None))
            .where(User.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if user is None:
        raise AuthenticationError(f"user {user_id} 不存在或已停用")
    return user
