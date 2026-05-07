"""Application settings (pydantic-settings)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor `.env` to monorepo root so launching uvicorn from any subdirectory
# (apps/api vs project root) loads the same file. Layout:
# apps/api/src/jobcopilot_api/settings.py → parents[4] = monorepo root
_PROJECT_ROOT = Path(__file__).resolve().parents[4]


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
        default_factory=lambda: ["http://localhost:3000"],
    )

    database_url: str = Field(
        default="postgresql+asyncpg://jobcopilot:jobcopilot@localhost:5432/jobcopilot",
    )

    dashscope_api_key: str = Field(default="")
    llm_provider: str = Field(default="dashscope")

    # S21 4-B: response cache 默认开 — dogfood/评测命中率 70%+,直接降一个数量级
    # 成本。需要每次都走真 LLM(prompt 调试 / token 流式)时设 false 关掉。
    llm_cache_enabled: bool = Field(default=True)


settings = Settings()
