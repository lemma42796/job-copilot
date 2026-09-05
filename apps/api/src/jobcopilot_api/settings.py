"""Application settings (pydantic-settings)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor `.env` to monorepo root so launching uvicorn from any subdirectory
# (apps/api vs project root) loads the same file. Layout:
# apps/api/src/jobcopilot_api/settings.py → parents[4] = monorepo root
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
PROJECT_ROOT = _PROJECT_ROOT


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="JOBCOPILOT_",
        extra="ignore",
    )

    env: str = Field(default="dev", description="dev | prod | test")
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)

    cors_allow_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
    )

    database_url: str = Field(
        default="postgresql+asyncpg://jobcopilot:jobcopilot@localhost:5432/jobcopilot",
    )

    dashscope_api_key: str = Field(default="")
    llm_provider: str = Field(default="dashscope")

    # S21 4-B: response cache 默认开 — dogfood/评测命中率 70%+,直接降一个数量级
    # 成本。需要每次都走真 LLM(prompt 调试 / token 流式)时设 false 关掉。
    llm_cache_enabled: bool = Field(default=True)

    # 所有文本生成 Agent 共用的进程内并发闸门。它限制的是正在占用上游
    # Provider 的逻辑调用数,避免 JD 分析、出题和面试评分一起把上游额度
    # 与本地连接池打满。单进程 MVP 先采用背压等待,不引入外部队列。
    # P2:默认由 4 提到 32。实测上游 qwen3.6-flash TPM 10M 允许约 333 个调用
    # 同时在飞,32 仍有余量;此阶段真正的上限是数据库连接池而非上游。
    llm_max_concurrency: int = Field(default=32, ge=1)

    # JD 聚合是批量长任务,单独限制同时运行数。等待中的记录仍保持
    # in_progress,由详情接口和 SSE 观察;不为轻量闭环新增 queued 状态。
    jd_analysis_max_concurrency: int = Field(default=1, ge=1)

    # Query embedding cache 专用守门。评测脚本默认打开 cache-only,避免重复
    # 跑 smoke 时继续请求 embedding provider;产品链路默认允许 miss 后实时计算。
    query_embedding_cache_only: bool = Field(default=False)

    # Local filesystem root that corresponds to logical `notes/`.
    # Empty = dev auto-detects test-notes/llm-notes when present, otherwise
    # falls back to <project>/notes. Used only for generated recall markdown.
    notes_fs_root: str = Field(default="")

    # Langfuse 三件套(M0 v2)— SDK 走 LANGFUSE_* 命名,本项目走 JOBCOPILOT_ 前缀,
    # main.py 启动时把字段镜像到 os.environ 给 SDK 用。public_key 留空 = noop 模式
    # (dev 不污染主 trace project,详见 README / AGENTS.md)。
    langfuse_host: str = Field(default="")
    langfuse_public_key: str = Field(default="")
    langfuse_secret_key: str = Field(default="")

    # ---------- P0 认证 ----------

    # 令牌 HMAC 签名密钥。留空 = 认证链路直接 401,避免"空密钥可伪造令牌"。
    auth_secret: str = Field(default="")
    auth_token_ttl_seconds: int = Field(default=7 * 24 * 3600, ge=60)

    # ---------- P1 余额 ----------

    # 注册即发放的模拟赠额(CNY)。0 = 不发,用户必须先充值。
    billing_signup_grant_cny: Decimal = Field(default=Decimal("0"))

    # ---------- P2 连接池 ----------

    # 单进程 40 条连接(20 + 20)。校验:API 进程数 × 40 + worker 副本数 ×
    # worker 池大小 必须小于 PostgreSQL max_connections(默认 100)。
    db_pool_size: int = Field(default=20, ge=1)
    db_max_overflow: int = Field(default=20, ge=0)
    db_pool_timeout_s: float = Field(default=30.0, gt=0)
    db_pool_recycle_s: int = Field(default=1800, ge=-1)

    # ---------- P3 / P4 任务与队列 ----------

    # 留空 = 不用 Redis,worker 退化为轮询 jobs 表(单机 dev 可用)。
    redis_url: str = Field(default="")
    job_stream_key: str = Field(default="jobcopilot:jobs")
    job_consumer_group: str = Field(default="jobcopilot-workers")
    job_claim_min_idle_ms: int = Field(default=120_000, ge=1000)
    job_poll_interval_s: float = Field(default=1.0, gt=0)
    job_worker_concurrency: int = Field(default=4, ge=1)
    job_default_deadline_s: int = Field(default=900, ge=30)
    job_event_poll_interval_s: float = Field(default=0.5, gt=0)
    job_sse_idle_timeout_s: float = Field(default=900.0, gt=0)

    # ---------- P6 减少上游调用 ----------

    # 语义缓存近似命中。缓存按 user_id 隔离,跨用户不共享(笔记内容私有)。
    llm_semantic_cache_enabled: bool = Field(default=False)
    llm_semantic_cache_min_similarity: float = Field(default=0.97, ge=0.0, le=1.0)
    llm_semantic_cache_candidates: int = Field(default=20, ge=1)

    # 短 query 跳过 rewrite;候选集不超过 top_k 时跳过 rerank。
    query_rewrite_min_chars: int = Field(default=8, ge=0)
    rerank_skip_when_candidates_le_top_k: bool = Field(default=True)

    # embed worker 一轮并发多少个 batch(每 batch 上限 10,对齐百炼)。
    embed_worker_batch_concurrency: int = Field(default=4, ge=1)

    # ---------- P7 过载保护 ----------

    # 待执行 job 超过水位 → 提交接口直接 503 + Retry-After。
    queue_high_watermark: int = Field(default=500, ge=1)
    queue_retry_after_seconds: int = Field(default=30, ge=1)

    # 上游连续 429 达阈值 → 熔断,期间不再发送(现有 tenacity 重试在过载
    # 时会放大压力,必须有熔断兜底)。
    upstream_breaker_threshold: int = Field(default=8, ge=1)
    upstream_breaker_cooldown_s: float = Field(default=20.0, gt=0)


settings = Settings()
