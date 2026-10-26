"""Unified media subscription services (Media Subscription Center)."""

from app.services.media.base import (
    DownloadResult,
    MediaItem,
    ProgressCallback,
    SubscriptionProvider,
)
from app.services.media.providers import (
    BilibiliProvider,
    PodcastProvider,
    RssProvider,
    XimalayaProvider,
    build_provider,
    get_provider_class,
    register_provider,
    supported_types,
)
from app.services.media.youtube_provider import YouTubeProvider

__all__ = [
    "DownloadResult",
    "MediaItem",
    "ProgressCallback",
    "SubscriptionProvider",
    "YouTubeProvider",
    "BilibiliProvider",
    "PodcastProvider",
    "XimalayaProvider",
    "RssProvider",
    "build_provider",
    "get_provider_class",
    "register_provider",
    "supported_types",
]
