"""YouTube channel / playlist provider built on yt-dlp."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.media import ytdlp
from app.services.media.base import (
    DownloadResult,
    MediaItem,
    ProgressCallback,
    SubscriptionProvider,
)

logger = logging.getLogger("naspilot.youtube.provider")

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_VIDEO_URL = "https://www.youtube.com/watch?v={video_id}"

_VIDEO_EXTS = {".mp4", ".mkv", ".webm"}
_AUDIO_EXTS = {".mp3", ".opus", ".m4a", ".aac", ".flac", ".wav", ".ogg"}


def safe_folder_name(name: str | None, fallback: str = "Youtube") -> str:
    """Make a value usable as a directory name on Linux/NAS shares."""
    cleaned = _UNSAFE.sub("_", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:120] or fallback


def _parse_date(entry: dict[str, Any]) -> datetime | None:
    for key in ("timestamp", "release_timestamp"):
        value = entry.get(key)
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                pass
    raw = entry.get("upload_date") or entry.get("release_date")
    if isinstance(raw, str) and len(raw) == 8 and raw.isdigit():
        try:
            return datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


class YouTubeProvider(SubscriptionProvider):
    """Enumerate and download from a YouTube channel or playlist."""

    provider_type = "youtube"
    display_name = "YouTube"

    # ── URL handling ───────────────────────────────────────────────────
    @staticmethod
    def normalize_url(url: str, sub_type: str) -> str:
        """Point channel URLs at the ``/videos`` tab (newest uploads)."""
        target = url.strip()
        if "list=" in target:
            return target  # already a concrete playlist
        if re.search(r"/(videos|shorts|streams|playlists)(?:[?#]|$)", target):
            return target
        if sub_type in ("channel", "youtube") and re.search(
            r"youtube\.com/(?:@|channel/|c/|user/)", target
        ):
            return target.rstrip("/") + "/videos"
        return target

    @property
    def scan_limit(self) -> int:
        limit = int(settings.YOUTUBE_SCAN_LIMIT or 0)
        return limit if limit > 0 else 0

    # ── Discovery ──────────────────────────────────────────────────────
    def _entries_to_items(
        self,
        entries: list[dict[str, Any]],
        *,
        channel_hint: str | None,
        playlist_hint: str | None,
        start_index: int = 1,
    ) -> list[MediaItem]:
        items: list[MediaItem] = []
        counter = start_index
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            video_id = entry.get("id") or entry.get("video_id") or ""
            if not video_id or not re.fullmatch(r"[\w-]{6,32}", str(video_id)):
                continue
            if entry.get("live_status") == "is_live":
                continue
            items.append(
                MediaItem(
                    item_id=str(video_id),
                    title=str(entry.get("title") or video_id),
                    url=_VIDEO_URL.format(video_id=video_id),
                    channel=entry.get("channel") or entry.get("uploader") or channel_hint,
                    playlist=entry.get("playlist") or playlist_hint,
                    publish_date=_parse_date(entry),
                    index=entry.get("playlist_index") or counter,
                    raw=entry,
                )
            )
            counter += 1
        return items

    async def list_items(self, limit: int | None = None) -> list[MediaItem]:
        limit = limit if limit is not None else self.scan_limit
        target = self.normalize_url(self.subscription.url, self.subscription.type)
        data = await ytdlp.dump_playlist_json(target, limit=limit)

        entries = data.get("entries") or []
        channel_hint = (
            data.get("channel")
            or data.get("uploader")
            or data.get("uploader_id")
            or self.subscription.name
        )
        playlist_hint = data.get("title") if data.get("_type") in ("playlist", "multi_video") else None

        # Case: the user pasted a "/playlists" URL — expand one level so the
        # individual playlists' videos are discovered too.
        if entries and all(self._looks_like_playlist(e) for e in entries if isinstance(e, dict)):
            expanded: list[MediaItem] = []
            idx = 1
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                playlist_url = entry.get("url") or entry.get("webpage_url")
                if not playlist_url:
                    continue
                if isinstance(playlist_url, str) and not playlist_url.startswith("http"):
                    playlist_url = f"https://www.youtube.com/{playlist_url.lstrip('/')}"
                try:
                    sub_data = await ytdlp.dump_playlist_json(str(playlist_url), limit=limit)
                except Exception as exc:
                    logger.warning("Failed to expand playlist %s: %s", playlist_url, exc)
                    continue
                sub_entries = sub_data.get("entries") or []
                expanded.extend(
                    self._entries_to_items(
                        sub_entries,
                        channel_hint=channel_hint,
                        playlist_hint=sub_data.get("title") or entry.get("title"),
                        start_index=idx,
                    )
                )
                idx += len(sub_entries)
                if limit and len(expanded) >= limit:
                    break
            return expanded[:limit] if limit else expanded

        return self._entries_to_items(entries, channel_hint=channel_hint, playlist_hint=playlist_hint)

    @staticmethod
    def _looks_like_playlist(entry: dict[str, Any]) -> bool:
        if entry.get("_type") == "url":
            url = str(entry.get("url") or "")
            return "playlist" in url or "list=" in url
        return False

    # ── Download ───────────────────────────────────────────────────────
    def output_template(self, item: MediaItem) -> str:
        """``<base>/<folder>/<original title>.<ext>`` — never renames the file.

        Subtitles and thumbnails follow the same base name (``xxx.en.srt``,
        ``xxx.jpg``), so every artifact keeps the original video title.
        """
        folder = safe_folder_name(
            item.playlist or item.channel or self.subscription.name,
            fallback=safe_folder_name(self.subscription.name),
        )
        return str(Path(self.base_dir()) / folder / "%(title)s.%(ext)s")

    async def download(
        self,
        item: MediaItem,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        mode = self.media_mode()
        audio_only = mode == "audio"
        height = self.video_height()
        afmt = self.audio_format()
        outtmpl = self.output_template(item)
        Path(outtmpl).parent.mkdir(parents=True, exist_ok=True)

        last_emit = 0.0

        async def _on_line(line: str) -> None:
            nonlocal last_emit
            if on_progress is None:
                return
            parsed = ytdlp.parse_progress_line(line)
            if parsed:
                # throttle: at most ~2 DB updates per second
                now = datetime.now(timezone.utc).timestamp()
                if now - last_emit < 0.5 and parsed["progress"] < 100:
                    return
                last_emit = now
                await on_progress(
                    parsed["progress"],
                    parsed["speed"],
                    parsed["downloaded_bytes"],
                    parsed["total_bytes"],
                    parsed["eta"],
                    "downloading",
                )
            elif ytdlp.is_processing_line(line):
                await on_progress(100.0, None, 0, 0, None, "processing")

        code, tail, produced = await ytdlp.download(
            item.url,
            outtmpl,
            media_mode=mode,
            video_height=height,
            audio_format=afmt,
            on_line=_on_line,
        )

        if code != 0:
            reason = (tail or "").splitlines()[-1][:800] if tail else f"yt-dlp exit {code}"
            hint = ytdlp.network_hint(tail)
            if hint:
                reason = f"{hint} — {reason}"
            return DownloadResult(
                item=item,
                status="failed",
                media_type="audio" if audio_only else "video",
                title=item.title,
                error=reason,
            )

        file_path = self._pick_output_file(produced, item, audio_only=audio_only)
        extras: list[str] = []

        # ``both``: keep the merged MP4 and derive a same-name audio file.
        if mode == "both" and file_path and file_path.lower().endswith((".mp4", ".mkv")):
            if on_progress is not None:
                await on_progress(100.0, None, 0, 0, None, "processing")
            rc, out, produced_audio = await ytdlp.extract_audio(file_path, afmt)
            if rc == 0 and produced_audio and os.path.isfile(produced_audio):
                extras.append(produced_audio)
            else:
                logger.warning("Audio extraction failed for %s: %s", file_path, out)

        size = os.path.getsize(file_path) if file_path and os.path.isfile(file_path) else None
        return DownloadResult(
            item=item,
            status="completed",
            file_path=file_path,
            file_size=size,
            media_type={"audio": "audio", "both": "both"}.get(mode, "video"),
            title=item.title,
            extra_paths=extras,
        )

    def _pick_output_file(
        self, produced: list[str], item: MediaItem, *, audio_only: bool = False
    ) -> str | None:
        media_exts = _AUDIO_EXTS if audio_only else _VIDEO_EXTS
        for path in reversed(produced):
            if os.path.splitext(path)[1].lower() in media_exts and os.path.isfile(path):
                return path
        if produced:
            for path in reversed(produced):
                if os.path.isfile(path):
                    return path
        # Fallback: the most recently written media file in the folder.
        try:
            folder = Path(self.base_dir()) / safe_folder_name(
                item.playlist or item.channel or self.subscription.name,
                fallback=safe_folder_name(self.subscription.name),
            )
            candidates = [
                p for p in folder.iterdir()
                if p.is_file() and p.suffix.lower() in media_exts
            ]
            if candidates:
                return str(max(candidates, key=lambda p: p.stat().st_mtime))
        except OSError:
            pass
        return None
