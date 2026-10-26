"""Media Download Center — unified subscription / history / task models.

Design notes
------------
* One row in ``media_subscriptions`` describes *any* source (YouTube channel,
  Bilibili UP, Podcast RSS, SoundCloud artist, Vimeo channel, raw URL).
* ``media_download_history`` is the archive. **De-duplication is by
  ``(provider, media_id)``** — never by file name.
* ``media_download_tasks`` is the live queue surfaced by the "下载任务" page and
  also the control plane for pause / resume / cancel / retry.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base

# provider identifiers (extensible)
PROVIDERS = (
    "youtube",
    "bilibili",
    "podcast",
    "soundcloud",
    "vimeo",
    "twitch",
    "x",
    "ximalaya",
    "xiaoyuzhou",
    "custom",
)

# subscription kinds
SUBSCRIPTION_TYPES = (
    "video",      # single video / episode
    "playlist",   # playlist / album / collection
    "channel",    # channel / UP / artist / feed subscription
)

SCHEDULE_TYPES = ("manual", "hourly", "every_6h", "every_12h", "daily", "cron")

NETWORK_MODES = ("system", "direct", "http", "socks5", "docker_vpn")

TASK_STATUSES = (
    "pending",     # queued, waiting for a worker slot
    "downloading",
    "paused",
    "processing",  # merging / audio extraction / subtitle conversion
    "completed",
    "failed",
    "cancelled",
)


class MediaSubscription(Base):
    """A subscription to any supported media source."""

    __tablename__ = "media_subscriptions"
    __table_args__ = (
        Index("ix_media_sub_url", "provider", "url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="custom", index=True)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    # video | playlist | channel
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="video", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", index=True)

    # ── output ────────────────────────────────────────────────────────
    audio_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    media_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="video", server_default="video"
    )  # video | audio | both
    video_height: Mapped[int] = mapped_column(Integer, default=480, server_default="480")  # 0 = best
    audio_format: Mapped[str] = mapped_column(
        String(16), nullable=False, default="mp3", server_default="mp3"
    )  # mp3 | best | m4a | opus | flac
    # Empty → provider/channel/playlist default tree
    download_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Prefix files with the playlist index (``001-title.mp4``). Default off:
    # filenames keep the original title unless explicitly requested.
    number_prefix: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    # ── behaviour ─────────────────────────────────────────────────────
    schedule_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="every_6h", server_default="every_6h", index=True
    )
    cron_expr: Mapped[str | None] = mapped_column(String(128), nullable=True)
    max_items_per_sync: Mapped[int] = mapped_column(Integer, default=20, server_default="20")
    # 0 = unlimited (bytes/second), otherwise ``--limit-rate`` value e.g. 5M
    rate_limit: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ── network policy (per subscription) ─────────────────────────────
    # system → inherit env/default | direct → bypass proxy
    # http | socks5 → use proxy_url (+ optional credentials)
    # docker_vpn → traffic already routed by a VPN sidecar container
    network_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="system", server_default="system"
    )
    proxy_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    proxy_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    proxy_password: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # e.g. gluetun / v2raya / clash — informational + validation only
    vpn_container: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # ── state ─────────────────────────────────────────────────────────
    last_check_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MediaDownloadHistory(Base):
    """Archive of downloaded media, de-duplicated by (provider, media_id)."""

    __tablename__ = "media_download_history"
    __table_args__ = (
        # Never key on file names — provider media id is the identity.
        Index("ix_media_history_provider_media", "provider", "media_id", unique=True),
        Index("ix_media_history_download_time", "download_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    media_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    playlist_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    publish_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # completed | failed
    status: Mapped[str] = mapped_column(String(16), default="completed", server_default="completed", index=True)
    media_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # video|audio|both
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)  # seconds
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_subscriptions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MediaDownloadTask(Base):
    """Live download task + control state (pause / resume / cancel / retry)."""

    __tablename__ = "media_download_tasks"
    __table_args__ = (
        Index("ix_media_task_status_created", "status", "created_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_subscriptions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    subscription_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="custom", index=True)
    media_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Snapshot of discovery metadata so a queued task can rebuild its MediaItem
    channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    playlist_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    item_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    speed: Mapped[str | None] = mapped_column(String(32), nullable=True)
    downloaded_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    eta: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
