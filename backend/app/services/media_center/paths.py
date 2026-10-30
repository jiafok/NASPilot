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
    from app.services.media_center import settings_store

    explicit = (getattr(subscription, "download_path", None) or "").strip()
    if explicit:
        return Path(explicit)

    return settings_store.dir_for_mode(
        getattr(subscription, "media_mode", None),
        bool(getattr(subscription, "audio_only", False)),
    )


def output_dir(subscription, provider: str, channel: str | None, playlist: str | None) -> Path:
    """Full directory: ``<root>/<provider>/<channel>[/<playlist>]``."""
    root = base_root(subscription)
    parts = [safe_segment(provider, "custom")]
    parts.append(safe_segment(channel, safe_segment(subscription.name)))
    if playlist:
        parts.append(safe_segment(playlist))
    return root.joinpath(*parts)


#: Title characters kept when the name must be unique (collection / playlist).
#:
#: Budget in bytes, since 255 is a *byte* limit and a Chinese character costs 3:
#:
#:   60 chars = 180 B + " [BV1XWa36MEkP_p20]" 19 B + ".mp4" 4 B = 203 B
#:
#: `--trim-filenames` is deliberately set high (see `engine`) so it cannot cut
#: the id off again — that is exactly how 20 parts once collapsed into one file.
UNIQUE_TITLE_CHARS = 60

#: Title characters kept for ordinary (non-colliding) downloads.
#:
#: 80 characters is 240 bytes of Chinese, which still fits in 255. Titles are
#: longer than that only in pathological cases, and silently failing to write
#: the file would be worse than a shorter name.
PLAIN_TITLE_CHARS = 80


def output_template(
    subscription,
    provider: str,
    channel: str | None,
    playlist: str | None,
    *,
    index: int | None = None,
    unique_name: bool = False,
) -> str:
    """yt-dlp style output template for one item.

    ``unique_name`` appends the provider id and truncates the title. Without it
    two items whose titles collide silently overwrite each other — see
    ``providers.YtDlpProvider._needs_unique_name``.
    """
    directory = output_dir(subscription, provider, channel, playlist)
    prefix = ""
    if getattr(subscription, "number_prefix", False):
        prefix = f"{int(index or 1):03d}-"
    if unique_name:
        stem = f"{prefix}%(title).{UNIQUE_TITLE_CHARS}s [%(id)s]"
    else:
        stem = f"{prefix}%(title).{PLAIN_TITLE_CHARS}s"
    return str(directory / f"{stem}.%(ext)s")


def suggest_library_paths() -> dict[str, list[str]]:
    """Recommended library roots for media servers."""
    from app.services.media_center import settings_store

    video = str(settings_store.video_dir())
    audio = str(settings_store.audio_dir())
    return {
        "emby": [f"{video}（电影/电视节目）", f"{audio}（音乐）"],
        "jellyfin": [f"{video}（视频）", f"{audio}（音乐）"],
        "audiobookshelf": [f"{audio}（播客/有声书）"],
    }
