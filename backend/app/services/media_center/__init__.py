"""Media Download Center — unified subscription / download / management."""

from app.services.media_center.base import (
    DownloadOptions,
    DownloadResult,
    MediaItem,
    ProgressCallback,
    SubscriptionProvider,
    all_providers,
    build_provider,
    detect_provider,
    get_provider_class,
    register_provider,
    resolve_options,
)

# importing providers populates the registry
from app.services.media_center import providers as _providers  # noqa: F401
from app.services.media_center.paths import output_dir, output_template, safe_segment

__all__ = [
    "DownloadOptions",
    "DownloadResult",
    "MediaItem",
    "ProgressCallback",
    "SubscriptionProvider",
    "all_providers",
    "build_provider",
    "detect_provider",
    "get_provider_class",
    "output_dir",
    "output_template",
    "register_provider",
    "resolve_options",
    "safe_segment",
]
