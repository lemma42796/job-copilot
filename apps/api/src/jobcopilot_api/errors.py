"""JobCopilot error hierarchy + RFC 7807 (`application/problem+json`).

Subclass `JobCopilotError`, set `status_code` / `code` / `title`. The
exception handler installed by `install_exception_handlers` converts every
unhandled exception into a Problem+JSON body matching OpenAPI / Pydantic schemas
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from jobcopilot_api.infra.request_id import request_id_ctxvar


class JobCopilotError(Exception):
    """Base for domain errors mapped to RFC 7807."""

    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    title: str = "服务内部错误"

    def __init__(
        self,
        detail: str = "",
        *,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.errors = errors or []


class NotFoundError(JobCopilotError):
    status_code = 404
    code = "NOT_FOUND"
    title = "未找到该资源"


class ConflictError(JobCopilotError):
    status_code = 409
    code = "CONFLICT"
    title = "资源状态冲突"


class ValidationError(JobCopilotError):
    status_code = 400
    code = "VALIDATION_ERROR"
    title = "请求参数有误"


class NoChunksForQueryError(JobCopilotError):
    """retrieval_pipeline 0 命中守门(PRD Q-10:< 3 chunks → 报错不兜底)。

    语义:用户的笔记库里没找到跟 query 主题相关的内容 — 让用户改 query 或
    先扩笔记。422 = 请求语法合法但语义无对应资源(比 404 更贴 "笔记里没这主题")。
    """

    status_code = 422
    code = "NO_CHUNKS_FOR_QUERY"
    title = "笔记里没找到这个主题"


class ModeNotImplementedError(JobCopilotError):
    """OpenAPI / Pydantic schemas:M2 阶段传 mode=job / mode=auto 返 422。"""

    status_code = 422
    code = "mode_not_implemented"
    title = "该模式 M2 阶段未启用"


class QueryRequiredError(JobCopilotError):
    """OpenAPI / Pydantic schemas:mode=topic 但 query 为空。"""

    status_code = 422
    code = "query_required"
    title = "query 不能为空"


class QueryTooLongError(JobCopilotError):
    """OpenAPI / Pydantic schemas:query 超过 200 字符。"""

    status_code = 422
    code = "query_too_long"
    title = "query 超过长度限制"


class ProblemResponse(JSONResponse):
    media_type = "application/problem+json"


_HTTP_CODE_MAP: dict[int, str] = {
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA",
    429: "RATE_LIMITED",
}


def _problem(
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    instance: str,
    errors: list[dict[str, Any]] | None = None,
) -> ProblemResponse:
    body: dict[str, Any] = {
        "type": f"https://jobcopilot.local/errors/{code.lower().replace('_', '-')}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
        "request_id": request_id_ctxvar.get(),
        "code": code,
    }
    if errors:
        body["errors"] = errors
    return ProblemResponse(status_code=status, content=body)


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(JobCopilotError)
    async def _handle_jobcopilot(request: Request, exc: JobCopilotError) -> ProblemResponse:
        return _problem(
            status=exc.status_code,
            code=exc.code,
            title=exc.title,
            detail=exc.detail,
            instance=request.url.path,
            errors=exc.errors or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> ProblemResponse:
        errors = [
            {
                "field": ".".join(str(p) for p in err["loc"]),
                "msg": err["msg"],
                "type": err["type"],
            }
            for err in exc.errors()
        ]
        return _problem(
            status=400,
            code="VALIDATION_ERROR",
            title="请求参数有误",
            detail="请求参数校验失败",
            instance=request.url.path,
            errors=errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException) -> ProblemResponse:
        code = _HTTP_CODE_MAP.get(exc.status_code, "HTTP_ERROR")
        title = (
            exc.detail if isinstance(exc.detail, str) and exc.detail else f"HTTP {exc.status_code}"
        )
        return _problem(
            status=exc.status_code,
            code=code,
            title=title,
            detail=title,
            instance=request.url.path,
        )
