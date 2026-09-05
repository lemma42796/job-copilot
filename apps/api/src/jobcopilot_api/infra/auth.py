"""认证依赖(P0)。

`CurrentUserId` 是全仓唯一的身份来源:每个业务路由都必须声明它,拿到
`user_id` 再往 service 层传。没有它的路由就是没有归属过滤的路由。

令牌从 `Authorization: Bearer <token>` 读。开发期允许用 `X-User-Id`
直连(`settings.auth_allow_header_user` 由 env=dev 隐含),生产环境
(env != dev)只接受 Bearer。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header

from jobcopilot_api.infra.db import get_sessionmaker
from jobcopilot_api.services import auth_service
from jobcopilot_api.settings import settings


async def get_current_user_id(
    authorization: Annotated[str | None, Header()] = None,
    x_user_id: Annotated[str | None, Header()] = None,
) -> int:
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise auth_service.AuthenticationError(
                "Authorization 头必须是 Bearer <token>"
            )
        user_id = auth_service.parse_token(token)
    elif x_user_id and settings.env == "dev":
        # dev 直连:免登录跑评测脚本 / 手工验证。生产不开。
        try:
            user_id = int(x_user_id)
        except ValueError as exc:
            raise auth_service.AuthenticationError("X-User-Id 非法") from exc
    else:
        raise auth_service.AuthenticationError("缺少 Authorization: Bearer 令牌")

    async with get_sessionmaker()() as session:
        user = await auth_service.get_active_user(session, user_id)
        return user.id


CurrentUserId = Annotated[int, Depends(get_current_user_id)]
