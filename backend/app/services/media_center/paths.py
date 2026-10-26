"""Directory layout rules for the Media Download Center.

Default tree (per the product spec)::

    /Media/Video/<provider>/<channel>/<playlist>/<title>.mp4
    /Media/Audio/<provider>/<channel>/<playlist>/<title>.mp3

An explicit ``download_path`` on the subscription replaces the root only, the
``provider/channel/playlist`` segments are still appended so different sources
never collide.

Filenames keep the original title by default. ``number_prefix`` is opt-in and
produces ``001-title.ext`` (index from the provider's playlist position).
"""

from __future__ import annotations

import re
from pathlib import Path

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_WS = re.compile(r"\s+")


def safe_segment(value: str | None, fallback: str = "Unknown", max_len: int = 120) -> str:
    """Make ``value`` usable as a single path segment on Linux/NAS shares."""
    cleaned = _UNSAFE.sub("_", (value or "").strip())
    cleaned = _WS.sub(" ", cleaned).strip(" .")
    return cleaned[:max_len] or fallback


def base_root(subscription) -> Path:
    """Root directory for a subscription (``/Media/Video`` or ``/Media/Audio``)."""
    from app.core.config import settings

    explicit = (getattr(subscription, "download_path", None) or "").strip()
    if explicit:
        return Path(explicit)

    mode = getattr(subscription, "media_mode", None) or "video"
    if mode == "audio" or getattr(subscription, "audio_only", False):
        return Path(settings.MEDIA_AUDIO_DIR)
    return Path(settings.MEDIA_VIDEO_DIR)


def output_dir(subscription, provider: str, channel: str | None, playlist: str | None) -> Path:
    """Full directory: ``<root>/<provider>/<channel>[/<playlist>]``."""
    root = base_root(subscription)
    parts = [safe_segment(provider, "custom")]
    parts.append(safe_segment(channel, safe_segment(subscription.name)))
    if playlist:
        parts.append(safe_segment(playlist))
    return root.joinpath(*parts)


def output_template(
    subscription,
    provider: str,
    channel: str | None,
    playlist: str | None,
    *,
    index: int | None = None,
) -> str:
    """yt-dlp style output template for one item."""
    directory = output_dir(subscription, provider, channel, playlist)
    prefix = ""
    if getattr(subscription, "number_prefix", False):
        prefix = f"{int(index or 1):03d}-"
    return str(directory / f"{prefix}%(title)s.%(ext)s")


def suggest_library_paths() -> dict[str, list[str]]:
    """Recommended library roots for media servers."""
    from app.core.config import settings

    video = str(settings.MEDIA_VIDEO_DIR)
    audio = str(settings.MEDIA_AUDIO_DIR)
    return {
        "emby": [f"{video}:/Media/Video (类型: 电影/电视节目)", f"{audio}:/Media/Audio (类型: 音乐)"],
        "jellyfin": [f"{video}:/Media/Video (类型: 视频)", f"{audio}:/Media/Audio (类型: 音乐)"],
        "audiobookshelf": [f"{audio}:/Media/Audio (类型: 播客/有声书)"],
    }
