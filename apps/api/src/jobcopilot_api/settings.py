"""Application settings (pydantic-settings)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
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


settings = Settings()
