"""Application configuration — settings via environment / .env."""

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global application settings loaded from .env or environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────────────
    APP_NAME: str = "NASPilot"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production-please"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h
    ALGORITHM: str = "HS256"

    # ── Database ───────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/naspilot.db"

    # ── Paths ──────────────────────────────────────────────────────────
    DATA_DIR: Path = Path("./data")
    LOG_DIR: Path = Path("./data/logs")
    PLUGIN_DIR: Path = Path("./plugins")

    # ── CORS ────────────────────────────────────────────────────────────
    CORS_ORIGINS: list[str] = ["*"]

    # ── Docker ───────────────────────────────────────────────────────────
    DOCKER_SOCK: str = "/var/run/docker.sock"

    # ── Initial Admin ─────────────────────────────────────────────────
    INITIAL_ADMIN_USER: str = "admin"
    INITIAL_ADMIN_PASSWORD: str = "admin123"

    # ── Scheduler ──────────────────────────────────────────────────────
    SCHEDULER_THREADPOOL: int = 20

    # ── Notification (defaults — can be overridden in UI) ──────────────
    FEISHU_WEBHOOK: str = ""
    FEISHU_SECRET: str = ""

    # ── AI Assistant ───────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"

    @field_validator("DATA_DIR", "LOG_DIR", "PLUGIN_DIR", mode="after")
    @classmethod
    def _ensure_dirs(cls, v: Path) -> Path:
        try:
            v.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            pass  # volume mount may restrict write; handled in lifespan
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
