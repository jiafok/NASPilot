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
    APP_VERSION: str = "1.0.0-rc1"
    DEBUG: bool = False
    SECRET_KEY: str = ""  # MUST be set via environment variable; empty default forces crash
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h
    ALGORITHM: str = "HS256"

    # ── Database ───────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/naspilot.db"
    DB_POOL_SIZE: int = 10
    DB_POOL_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 10
    DB_POOL_RECYCLE: int = 1800  # 30 minutes — prevent stale SQLite locks

    # ── Paths ──────────────────────────────────────────────────────────
    DATA_DIR: Path = Path("./data")
    LOG_DIR: Path = Path("./data/logs")
    PLUGIN_DIR: Path = Path("./plugins")

    # ── CORS ────────────────────────────────────────────────────────────
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://localhost:4175",
        "http://127.0.0.1:4175",
    ]

    # ── Docker ───────────────────────────────────────────────────────────
    DOCKER_SOCK: str = "/var/run/docker.sock"

    # ── Initial Admin ─────────────────────────────────────────────────
    INITIAL_ADMIN_USER: str = "admin"
    FIRST_ADMIN_PASSWORD: str = ""  # empty → random generation at first startup

    # ── Scheduler ──────────────────────────────────────────────────────
    SCHEDULER_THREADPOOL: int = 20

    # ── Notification (defaults — can be overridden in UI) ──────────────
    FEISHU_WEBHOOK: str = ""
    FEISHU_SECRET: str = ""

    # ── AI Assistant ───────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"

    @field_validator("SECRET_KEY", mode="before")
    @classmethod
    def _require_secret_key(cls, v: object) -> str:
        s = str(v or "").strip()
        if not s or s == "change-me-in-production-please":
            raise ValueError(
                "SECRET_KEY is not set. "
                "Set the SECRET_KEY environment variable to a random string (≥32 chars). "
                "Example: export SECRET_KEY=$(openssl rand -hex 32)"
            )
        if len(s) < 16:
            raise ValueError("SECRET_KEY must be at least 16 characters")
        return s

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
