"""Application configuration — settings via environment / .env."""

import logging
import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("naspilot.config")


#: Placeholder values that must never be used as a real signing key.
_PLACEHOLDER_KEYS = {
    "",
    "change-me-in-production-please",
    "change-me-to-a-random-string-at-least-32-chars",
    "change-me-please-generate-with-openssl-rand-hex-32",
    "your-secret-key",
    "secret",
}

#: Where the auto-generated key lives (inside the mounted data volume).
_SECRET_KEY_FILENAME = "secret_key"

#: Minimum acceptable length for a provided key.
_MIN_SECRET_LEN = 16


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
    # Optional: auto-generated and persisted to ``DATA_DIR/secret_key`` on first
    # start when unset, so a plain `docker compose up` just works.
    SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h
    #: Lifetime of a refresh token, in days. Mobile clients stay signed in this
    #: long without re-entering a password, which is the whole point: a 24h
    #: access token alone forced a login on the second morning.
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
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

    # ── Media Download Center (unified, all providers) ─────────────────
    # 只需要一个根目录：其下自动使用 Video/ 与 Audio/。
    # 留空 = <DATA_DIR>/media，也就是跟数据库在同一个挂载卷里，
    # 因此 compose 只需要 - ./data:/app/data 一行，不用再挂 /Media。
    # 运行时可从「系统设置」页修改（存 settings 表，优先于这里的默认值）。
    MEDIA_ROOT: str = ""
    # 可选：单独覆盖某个子目录（留空 = <MEDIA_ROOT>/Video 或 /Audio）
    MEDIA_VIDEO_DIR: str = ""
    MEDIA_AUDIO_DIR: str = ""
    MEDIA_DEFAULT_VIDEO_HEIGHT: int = 480
    MEDIA_AUDIO_QUALITY: str = "0"
    MEDIA_SUB_LANGS: str = "en.*"
    MEDIA_SCAN_LIMIT: int = 100
    MEDIA_MAX_PER_SYNC: int = 20
    MEDIA_MAX_CONCURRENT: int = 2
    MEDIA_RATE_LIMIT: str = ""            # global default, e.g. "5M"
    MEDIA_PROXY_URL: str = ""             # default proxy for network_mode=system
    MEDIA_DEFAULT_SCHEDULE: str = "every_6h"
    MEDIA_RETENTION_DAYS: int = 30
    # Optional cookies file (X / age-restricted / members-only)
    MEDIA_COOKIES_FILE: str = ""
    # e.g. "node" / "deno" when yt-dlp needs a JS runtime for signatures
    MEDIA_JS_RUNTIMES: str = ""
    # Global default audio container when a subscription does not set one:
    #   mp3  → lossy re-encode (libmp3lame VBR q0, best compatibility)
    #   best → zero-transcode remux, keeps the source stream (Opus/AAC)
    #   opus / m4a / flac / wav → explicit container (stream copy when possible)
    MEDIA_AUDIO_FORMAT: str = "mp3"
    # ── English Learning Center ───────────────────────────────────────
    #: Root of the English library. Empty means "reuse the media library":
    #: the scanner walks MEDIA_ROOT itself, so everything the Media Download
    #: Center put under <MEDIA_ROOT>/Video or /Audio shows up as lessons with
    #: no second copy and no extra config. Set it only to point at another
    #: directory entirely.
    ENGLISH_ROOT: str = ""
    #: How many vocabulary terms to keep per course after extraction.
    ENGLISH_VOCAB_LIMIT: int = 300
    #: Scan the library once at startup. Off by default: the watcher below
    #: already notices a changed library within its interval.
    ENGLISH_SCAN_ON_STARTUP: bool = False
    #: Poll interval for the auto-scan watcher, in seconds. 0 disables it.
    #: Polling compares a cheap fingerprint (file count / total size / newest
    #: mtime) and only runs an *incremental* scan when something changed, so a
    #: short interval costs almost nothing.
    ENGLISH_AUTO_SCAN_SECONDS: int = 60

    # ── Media servers (deep links + library hints) ────────────────────
    EMBY_URL: str = ""
    JELLYFIN_URL: str = ""
    AUDIOBOOKSHELF_URL: str = ""

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
    def _normalise_secret_key(cls, v: object) -> str:
        """Reject obvious placeholders; a missing value is fine (auto-generated)."""
        s = str(v or "").strip()
        if s in _PLACEHOLDER_KEYS:
            return ""
        return s

    @model_validator(mode="after")
    def _ensure_secret_key(self) -> "Settings":
        """Load the persisted key, or create one on first run.

        Keeping the key in ``DATA_DIR`` (which is a mounted volume) means login
        sessions survive restarts while the operator never has to invent or
        copy a secret.
        """
        if len(self.SECRET_KEY) >= _MIN_SECRET_LEN:
            return self
        if self.SECRET_KEY:
            logger.warning(
                "SECRET_KEY is only %d characters (need %d) — ignoring it and "
                "using the generated key instead.",
                len(self.SECRET_KEY),
                _MIN_SECRET_LEN,
            )

        key_file = Path(self.DATA_DIR) / _SECRET_KEY_FILENAME

        # 1) reuse the key generated by an earlier start
        try:
            existing = key_file.read_text(encoding="utf-8").strip()
        except OSError:
            existing = ""
        if len(existing) >= _MIN_SECRET_LEN:
            self.SECRET_KEY = existing
            return self

        # 2) generate, persist, and keep it for this process
        generated = secrets.token_hex(32)
        try:
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_text(generated, encoding="utf-8")
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
            logger.info("Generated a new SECRET_KEY and saved it to %s", key_file)
        except OSError as exc:
            # Read-only volume: still start, but warn that sessions won't persist
            logger.warning(
                "Could not persist SECRET_KEY to %s (%s). "
                "Using a temporary key — logins will be invalidated on restart.",
                key_file,
                exc,
            )
        self.SECRET_KEY = generated
        return self

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
