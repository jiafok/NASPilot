"""Provider registry — YouTube today, other media sources reserved for later.

Bilibili / Podcast / Ximalaya / RSS are intentionally implemented as thin
placeholders: they already satisfy the ``SubscriptionProvider`` contract and
can be enabled by filling in ``list_items`` / ``download`` without touching the
scheduler, queue, history tables or UI.
"""

from __future__ import annotations

from app.services.media.base import (
    DownloadResult,
    MediaItem,
    ProgressCallback,
    SubscriptionProvider,
)
from app.services.media.youtube_provider import YouTubeProvider


class _UnimplementedProvider(SubscriptionProvider):
    """Base for reserved providers — reports a clear message instead of crashing."""

    provider_type = "unimplemented"

    async def list_items(self, limit: int | None = None) -> list[MediaItem]:
        raise NotImplementedError(f"{self.display_name} 订阅源尚未实现")

    async def download(
        self,
        item: MediaItem,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        raise NotImplementedError(f"{self.display_name} 下载尚未实现")


class BilibiliProvider(_UnimplementedProvider):
    provider_type = "bilibili"
    display_name = "Bilibili 合集"


class PodcastProvider(_UnimplementedProvider):
    provider_type = "podcast"
    display_name = "Podcast RSS"


class XimalayaProvider(_UnimplementedProvider):
    provider_type = "ximalaya"
    display_name = "喜马拉雅专辑"


class RssProvider(_UnimplementedProvider):
    provider_type = "rss"
    display_name = "通用 RSS"


_REGISTRY: dict[str, type[SubscriptionProvider]] = {
    # YouTube — both "channel" and "playlist" rows are served by YouTubeProvider.
    # Other sources (Bilibili / Podcast / SoundCloud / Vimeo / …) live in the
    # unified Media Download Center: app.services.media_center
    "youtube": YouTubeProvider,
    "channel": YouTubeProvider,
    "playlist": YouTubeProvider,
}


def register_provider(*types: str):
    """Decorator — register a provider class for one or more subscription types."""

    def _wrap(cls: type[SubscriptionProvider]) -> type[SubscriptionProvider]:
        for key in types:
            _REGISTRY[key.lower()] = cls
        return cls

    return _wrap


def get_provider_class(subscription_type: str) -> type[SubscriptionProvider]:
    cls = _REGISTRY.get((subscription_type or "").lower())
    if cls is None:
        raise ValueError(f"不支持的订阅类型: {subscription_type}")
    return cls


def build_provider(subscription) -> SubscriptionProvider:
    """Instantiate the provider that serves ``subscription``."""
    return get_provider_class(subscription.type)(subscription)


def supported_types() -> list[dict[str, str]]:
    """Types offered by the legacy YouTube module (YouTube only)."""
    return [
        {"value": "channel", "label": "YouTube 频道"},
        {"value": "playlist", "label": "YouTube 播放列表"},
    ]
