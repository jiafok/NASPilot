"""Media subscription models — YouTube auto-download (Media Subscription Center).

Tables
------
``youtube_subscriptions``
    A channel / playlist the user subscribes to. The scheduler polls it every
    ``YOUTUBE_SYNC_INTERVAL_HOURS`` hours (default 6) or on manual sync.
``youtube_download_history``
    One row per downloaded video. ``video_id`` is the **only** identity used
    for de-duplication (never file names), so renaming files on disk can never
    cause a re-download.
``youtube_download_tasks``
    Live queue / progress rows rendered by the "下载任务" page. Kept separate
    from history so a failed attempt does not pollute the archive.
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


class YoutubeSubscription(Base):
    """A YouTube channel or playlist subscription."""

    __tablename__ = "youtube_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    # channel | playlist  (extensible: bilibili / podcast / ximalaya / rss)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="channel", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", index=True)
    # Legacy flag kept for compatibility; always derived from ``media_mode``
    # (``audio_only == (media_mode == "audio")``).
    audio_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # video | audio | both
    media_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="video", server_default="video", index=True
    )
    # Video height cap: 0 → use settings.YOUTUBE_MAX_HEIGHT (0 there = best)
    video_height: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Audio container: "" → settings.YOUTUBE_AUDIO_FORMAT
    # mp3 | best (原流零转码) | opus | m4a | flac | wav
    audio_format: Mapped[str] = mapped_column(
        String(16), nullable=False, default="", server_default=""
    )
    # Empty → falls back to settings.YOUTUBE_AUDIO_DIR / YOUTUBE_VIDEO_DIR
    download_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    last_check_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class YoutubeDownloadHistory(Base):
    """Permanent archive of downloaded videos, keyed by YouTube video id."""

    __tablename__ = "youtube_download_history"
    __table_args__ = (
        # Unique by video id — de-dup is *never* based on file name.
        Index("ix_youtube_history_video_id", "video_id", unique=True),
        Index("ix_youtube_history_download_time", "download_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(255), nullable=True)
    playlist: Mapped[str | None] = mapped_column(String(255), nullable=True)
    publish_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    download_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)  # bytes
    # completed | failed  (failed rows are kept for "重新下载" retries)
    status: Mapped[str] = mapped_column(String(16), default="completed", server_default="completed", index=True)
    # Media kind actually produced: video | audio
    media_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Nullable: history survives subscription deletion (ON DELETE SET NULL)
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("youtube_subscriptions.id", ondelete="SET NULL"), nullable=True, index=True
    )


class YoutubeDownloadTask(Base):
    """Live download task (pending / downloading / completed / failed)."""

    __tablename__ = "youtube_download_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("youtube_subscriptions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    subscription_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    video_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # pending | downloading | processing | completed | failed
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0, server_default="0")  # percent 0-100
    speed: Mapped[str | None] = mapped_column(String(32), nullable=True)
    downloaded_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    eta: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
