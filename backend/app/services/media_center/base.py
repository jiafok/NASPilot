"""Unified media subscription provider interface.

Every source (YouTube, Bilibili, Podcast RSS, SoundCloud, Vimeo, …) implements
this contract, so the scheduler, download queue, history archive and UI are
shared. Adding a source must never require touching those layers: register a
new class with ``@register_provider`` and it appears everywhere automatically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, ClassVar, Protocol


class SubscriptionLike(Protocol):
    """Duck-typed view of ``MediaSubscription`` (avoids model import cycles)."""

    id: int | None
    name: str
    provider: str
    url: str
    type: str
    enabled: bool
    media_mode: str
    audio_only: bool
    video_height: int
    audio_format: str
    download_path: str | None
    number_prefix: bool
    rate_limit: str | None
    max_items_per_sync: int
    network_mode: str
    proxy_url: str | None
    proxy_username: str | None
    proxy_password: str | None
    vpn_container: str | None


@dataclass(slots=True)
class MediaItem:
    """One downloadable item discovered on a subscription."""

    media_id: str  # provider-native identity — NEVER a file name
    title: str
    url: str
    channel: str | None = None
    playlist: str | None = None
    publish_date: datetime | None = None
    index: int | None = None
    duration: int | None = None
    raw: dict = field(default_factory=dict)


@dataclass(slots=True)
class DownloadOptions:
    """Resolved per-subscription download options."""

    media_mode: str = "video"       # video | audio | both
    video_height: int = 480         # 0 = best
    audio_format: str = "mp3"       # mp3 | best | m4a | opus | flac
    number_prefix: bool = False
    rate_limit: str | None = None
    proxy: str | None = None        # fully resolved proxy URL (None = direct)
    sub_langs: str = "en.*"
    write_subs: bool = True
    write_thumbnail: bool = True


@dataclass(slots=True)
class DownloadResult:
    item: MediaItem
    status: str = "completed"  # completed | failed | cancelled
    file_path: str | None = None
    file_size: int | None = None
    media_type: str | None = None  # video | audio | both
    title: str | None = None
    duration: int | None = None
    error: str | None = None
    extra_paths: list[str] = field(default_factory=list)


# (percent, speed, downloaded_bytes, total_bytes, eta, status)
ProgressCallback = Callable[[float, "str | None", int, int, "str | None", str], Awaitable[None]]
# returns False to abort (used for pause/cancel control)
LineHook = Callable[[str], Awaitable[bool | None]]


class SubscriptionProvider(ABC):
    """Base class for every media source."""

    provider: ClassVar[str] = "custom"
    display_name: ClassVar[str] = "Custom URL"
    implemented: ClassVar[bool] = True
    supported_types: ClassVar[tuple[str, ...]] = ("video", "playlist", "channel")
    url_examples: ClassVar[tuple[str, ...]] = ()
    note: ClassVar[str | None] = None
    #: domains used by :meth:`detect` (substring match)
    url_patterns: ClassVar[tuple[str, ...]] = ()

    def __init__(self, subscription: SubscriptionLike) -> None:
        self.subscription = subscription

    # ── URL handling ───────────────────────────────────────────────────
    @classmethod
    def validate_url(cls, url: str) -> tuple[bool, str | None]:
        """Return ``(ok, message)`` for a URL of this provider."""
        value = (url or "").strip()
        if not value.startswith(("http://", "https://")):
            return False, "URL 必须以 http:// 或 https:// 开头"
        return True, None

    @classmethod
    def guess_type(cls, url: str) -> str:
        """Best-effort subscription type from a URL (overridable)."""
        low = (url or "").lower()
        if any(k in low for k in ("playlist", "/sets/", "/album/", "/collectiondetail", "/medialist")):
            return "playlist"
        if any(k in low for k in ("/@", "/channel/", "/c/", "/user/", "/space/", "/artist/", "/feed")):
            return "channel"
        return "video"

    # ── Discovery ──────────────────────────────────────────────────────
    @abstractmethod
    async def list_items(self, limit: int | None = None) -> list[MediaItem]:
        """Return items exposed by the source (newest first)."""

    async def get_new_items(
        self, known_ids: set[str], limit: int | None = None
    ) -> list[MediaItem]:
        """Only items whose provider id is not in ``known_ids``."""
        items = await self.list_items(limit=limit)
        return [item for item in items if item.media_id not in known_ids]

    # ── Transfer ───────────────────────────────────────────────────────
    @abstractmethod
    async def download(
        self,
        item: MediaItem,
        options: DownloadOptions,
        *,
        on_progress: ProgressCallback | None = None,
        on_line: LineHook | None = None,
    ) -> DownloadResult:
        """Download one item using ``options`` (proxy / quality / paths)."""

    # ── Display helpers ────────────────────────────────────────────────
    def channel_name(self, item: MediaItem) -> str | None:
        return item.channel or self.subscription.name

    def playlist_name(self, item: MediaItem) -> str | None:
        return item.playlist or None


# ══════════════════════════════════════════════════════════════════════
#  Registry
# ══════════════════════════════════════════════════════════════════════

_REGISTRY: dict[str, type[SubscriptionProvider]] = {}


def register_provider(cls: type[SubscriptionProvider]) -> type[SubscriptionProvider]:
    """Class decorator — register a provider implementation."""
    _REGISTRY[cls.provider] = cls
    return cls


def get_provider_class(name: str) -> type[SubscriptionProvider] | None:
    return _REGISTRY.get((name or "").lower())


def all_providers() -> dict[str, type[SubscriptionProvider]]:
    return dict(_REGISTRY)


def detect_provider(url: str) -> str:
    """Guess which provider owns ``url`` (defaults to ``custom``)."""
    low = (url or "").lower()
    for name, cls in _REGISTRY.items():
        if name == "custom":
            continue
        if any(pattern in low for pattern in cls.url_patterns):
            return name
    return "custom"


def build_provider(subscription: SubscriptionLike) -> SubscriptionProvider:
    cls = get_provider_class(subscription.provider) or get_provider_class("custom")
    if cls is None:  # pragma: no cover - registry always has custom
        raise RuntimeError("没有可用的 Provider（custom 未注册）")
    return cls(subscription)


def resolve_options(subscription: SubscriptionLike) -> DownloadOptions:
    """Translate a subscription row into concrete download options."""
    from app.core.config import settings
    from app.services.media_center.network import resolve_proxy

    mode = getattr(subscription, "media_mode", None)
    if mode not in ("video", "audio", "both"):
        mode = "audio" if getattr(subscription, "audio_only", False) else "video"
    height = int(getattr(subscription, "video_height", 0) or 0)
    if height < 0:
        height = int(settings.MEDIA_DEFAULT_VIDEO_HEIGHT or 0)
    return DownloadOptions(
        media_mode=mode,
        video_height=height,
        audio_format=(getattr(subscription, "audio_format", "") or "mp3").lower(),
        number_prefix=bool(getattr(subscription, "number_prefix", False)),
        rate_limit=(getattr(subscription, "rate_limit", None) or None),
        proxy=resolve_proxy(subscription),
        sub_langs=settings.MEDIA_SUB_LANGS,
    )


__all__ = [
    "DownloadOptions",
    "DownloadResult",
    "LineHook",
    "MediaItem",
    "ProgressCallback",
    "SubscriptionLike",
    "SubscriptionProvider",
    "all_providers",
    "build_provider",
    "detect_provider",
    "get_provider_class",
    "register_provider",
    "resolve_options",
]
