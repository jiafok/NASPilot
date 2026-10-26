"""Unified media subscription framework.

The goal is a *Media Subscription Center*: any source (YouTube channel/playlist
today; Bilibili, Podcast RSS, Ximalaya tomorrow) implements the same small
interface, so the scheduler, download queue and history tables are shared.

    ┌────────────────────┐
    │ SubscriptionProvider│  (abstract)
    └─────────┬──────────┘
              │
     ┌────────┼───────────┬─────────────┬──────────────┐
 YouTubeProvider  BilibiliProvider  PodcastProvider  RssProvider
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, Protocol


class SubscriptionLike(Protocol):
    """Duck-typed view of ``YoutubeSubscription`` (avoids model import cycles)."""

    id: int
    name: str
    url: str
    type: str
    enabled: bool
    audio_only: bool
    download_path: str | None
    #: ``video`` | ``audio`` | ``both`` (optional for older providers/models)
    media_mode: str
    video_height: int
    audio_format: str


@dataclass(slots=True)
class MediaItem:
    """A single downloadable item discovered on a subscription."""

    item_id: str  # provider-native unique id — NEVER a file name
    title: str
    url: str
    channel: str | None = None
    playlist: str | None = None
    publish_date: datetime | None = None
    index: int | None = None
    # free-form provider payload (e.g. raw yt-dlp entry)
    raw: dict = field(default_factory=dict)


@dataclass(slots=True)
class DownloadResult:
    """Outcome of downloading one ``MediaItem``."""

    item: MediaItem
    status: str = "completed"  # completed | failed
    file_path: str | None = None
    file_size: int | None = None
    media_type: str | None = None  # video | audio | both
    title: str | None = None
    error: str | None = None
    #: additional files produced alongside ``file_path`` (e.g. the MP3 of "both")
    extra_paths: list[str] = field(default_factory=list)


# progress(percent, speed, downloaded_bytes, total_bytes, eta, status)
ProgressCallback = Callable[[float, "str | None", int, int, "str | None", str], Awaitable[None]]


class SubscriptionProvider(ABC):
    """Base class for every media subscription source."""

    provider_type: str = "unknown"
    #: human readable name shown in the UI
    display_name: str = "Unknown"

    def __init__(self, subscription: SubscriptionLike) -> None:
        self.subscription = subscription

    # ── Discovery ──────────────────────────────────────────────────────
    @abstractmethod
    async def list_items(self, limit: int | None = None) -> list[MediaItem]:
        """Return the newest items exposed by the source (newest first)."""

    async def get_new_items(self, downloaded_ids: set[str], limit: int | None = None) -> list[MediaItem]:
        """Return only items whose provider id is not in ``downloaded_ids``.

        De-duplication is strictly id based — file names are never inspected.
        """
        items = await self.list_items(limit=limit)
        return [item for item in items if item.item_id not in downloaded_ids]

    # ── Transfer ───────────────────────────────────────────────────────
    @abstractmethod
    async def download(
        self,
        item: MediaItem,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        """Download a single item and return the produced files' summary."""

    # ── Helpers ────────────────────────────────────────────────────────
    def base_dir(self) -> str:
        """Resolve the destination root for this subscription.

        The legacy page shares the unified media root, so a NAS only has to
        mount one directory (``/Media``) no matter which page is used.
        ``YOUTUBE_VIDEO_DIR`` / ``YOUTUBE_AUDIO_DIR`` still win when set
        explicitly, which keeps older deployments working.
        """
        import os

        from app.services.media_center import settings_store

        if self.subscription.download_path:
            return self.subscription.download_path

        audio = (os.environ.get("YOUTUBE_AUDIO_DIR") or "").strip()
        video = (os.environ.get("YOUTUBE_VIDEO_DIR") or "").strip()
        if self.media_mode() == "audio":
            return audio or str(settings_store.audio_dir())
        return video or str(settings_store.video_dir())

    def media_mode(self) -> str:
        """Resolve the output mode: ``video`` | ``audio`` | ``both``."""
        mode = getattr(self.subscription, "media_mode", None)
        if mode in ("video", "audio", "both"):
            return str(mode)
        return "audio" if getattr(self.subscription, "audio_only", False) else "video"

    def video_height(self) -> int:
        """Effective video height cap (0 = best available)."""
        from app.core.config import settings

        value = int(getattr(self.subscription, "video_height", 0) or 0)
        return value if value > 0 else int(settings.YOUTUBE_MAX_HEIGHT or 0)

    def audio_format(self) -> str:
        """Effective audio container/codec request."""
        from app.core.config import settings

        value = (getattr(self.subscription, "audio_format", "") or "").strip().lower()
        if value:
            return value
        return (settings.YOUTUBE_AUDIO_FORMAT or "mp3").strip().lower()
