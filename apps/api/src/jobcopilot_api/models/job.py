"""异步任务与任务事件(P3)。

在线 POST 接口只写 `jobs` 一行然后返回 202;LLM 全流程在 worker 侧执行,
进度写 `job_events`。SSE 订阅接口只读 `job_events`,按 `seq` 递增拉取,
断线重连时带上已收到的 `seq` 就能补齐断开期间的事件。

`status` 取值见 `JOB_STATUS_VALUES`。`insufficient_balance` 是独立终态:
余额中途耗尽导致就地中止,已产生的结果保留,前端据此提示充值而不是报错。

`dedupe_key` 承担幂等:worker 重启后重复消费同一 job_id 不得重复调用
LLM 或重复扣费,领取动作是 `UPDATE jobs SET status='running' WHERE id=:id
AND status='queued'` 的条件写,只有一个消费者能成功。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from jobcopilot_api.models.base import Base, IDMixin

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
INSUFFICIENT_BALANCE = "insufficient_balance"
DEADLINE_EXCEEDED = "deadline_exceeded"

JOB_STATUS_VALUES: tuple[str, ...] = (
    QUEUED,
    RUNNING,
    SUCCEEDED,
    FAILED,
    INSUFFICIENT_BALANCE,
    DEADLINE_EXCEEDED,
)
TERMINAL_JOB_STATUSES: frozenset[str] = frozenset(
    {SUCCEEDED, FAILED, INSUFFICIENT_BALANCE, DEADLINE_EXCEEDED}
)

# job kind — 与 workers/job_worker.py 的 handler 注册表一一对应。
KIND_QUIZ_CREATE = "quiz_create"
KIND_ANSWER_TURN = "answer_turn"
KIND_SESSION_FINISH = "session_finish"
KIND_SESSION_SUBMIT = "session_submit"
KIND_JD_ANALYSIS = "jd_analysis"

JOB_KIND_VALUES: tuple[str, ...] = (
    KIND_QUIZ_CREATE,
    KIND_ANSWER_TURN,
    KIND_SESSION_FINISH,
    KIND_SESSION_SUBMIT,
    KIND_JD_ANALYSIS,
)


class Job(Base, IDMixin):
    __tablename__ = "jobs"

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=QUEUED
    )

    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    """同一逻辑任务的去重键;唯一索引只在非终态行上生效。"""

    payload: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default=func.cast("{}", postgresql.JSONB),
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(postgresql.JSONB)

    # 业务资源关联,让前端从 job 跳回 session / analysis 详情。
    resource_kind: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[int | None] = mapped_column(BigInteger)

    error_code: Mapped[str | None] = mapped_column(String(60))
    error_detail: Mapped[str | None] = mapped_column(Text())

    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    """已写入的最大 job_events.seq,worker 侧单调递增。"""

    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class JobEvent(Base, IDMixin):
    __tablename__ = "job_events"

    job_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[str] = mapped_column(String(40), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB,
        nullable=False,
        server_default=func.cast("{}", postgresql.JSONB),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
