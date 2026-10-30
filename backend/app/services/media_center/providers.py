"""Built-in providers for the Media Download Center.

| provider      | engine                      | status |
| ------------- | --------------------------- | ------ |
| youtube       | yt-dlp                      | ✅ |
| bilibili      | yt-dlp                      | ✅ |
| podcast       | feedparser + HTTP (httpx)   | ✅ |
| soundcloud    | yt-dlp                      | ✅ |
| vimeo         | yt-dlp                      | ✅ |
| twitch        | yt-dlp                      | ✅ |
| x             | yt-dlp                      | ✅ |
| ximalaya      | —（预留）                   | ⏳ |
| xiaoyuzhou    | —（预留）                   | ⏳ |
| custom        | yt-dlp（任意站点）          | ✅ |
"""

from __future__ import annotations

import logging
import os
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.media_center import engine
from app.services.media_center.base import (
    DownloadOptions,
    DownloadResult,
    LineHook,
    MediaItem,
    ProgressCallback,
    SubscriptionProvider,
    register_provider,
)
from app.services.media_center.network import resolve_policy
from app.services.media_center.paths import output_template, safe_segment

logger = logging.getLogger("naspilot.media.providers")

_VIDEO_ID_RE = re.compile(r"^[\w-]{4,64}$")


def _sanitise_media_id(value: str) -> str:
    """Reduce anything to the shape ``_VIDEO_ID_RE`` accepts."""
    return re.sub(r"[^\w-]", "_", value or "")[:64].strip("_")


def _derive_media_id(url: str) -> str:
    """Build a stable id for an entry yt-dlp described with only a URL.

    ``--flat-playlist`` returns ``{"_type": "url", "url": ".../BV1XWa36MEkP?p=3"}``
    for Bilibili multi-part collections — no ``id`` at all. Requiring one threw
    every part away, so such a subscription discovered zero items and could
    never download anything.

    The reconstruction matches the id yt-dlp itself reports
    (``BV1XWa36MEkP_p3``), so download history stays consistent.
    """
    if not url:
        return ""
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query)

    match = re.search(r"/video/([A-Za-z0-9]+)", parsed.path)
    if match:
        base = match.group(1)
        page = (query.get("p") or [""])[0]
        return _sanitise_media_id(f"{base}_p{page}" if page.isdigit() else base)

    # Generic fallback: the last path segment, else a query parameter that
    # usually carries the video identity.
    # `/watch?v=ID` style: the identity is in the query, not the path.
    for key in ("v", "id", "vid"):
        value = (query.get(key) or [""])[0]
        if value:
            return _sanitise_media_id(value)
    segment = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return _sanitise_media_id(segment)


def _entry_title(entry: dict[str, Any], media_id: str) -> str:
    """Entry title, with yt-dlp's ``"NA"`` placeholder treated as missing."""
    title = str(entry.get("title") or "").strip()
    if title and title.upper() not in {"NA", "N/A", "NONE"}:
        return title
    return media_id


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


# ══════════════════════════════════════════════════════════════════════
#  Shared yt-dlp implementation
# ══════════════════════════════════════════════════════════════════════


class YtDlpProvider(SubscriptionProvider):
    """Provider backed by yt-dlp (works for most video/audio sites)."""

    provider = "custom"
    display_name = "任意 URL (yt-dlp)"
    url_patterns = ()

    #: how a channel URL should be normalised to "newest uploads"
    channel_suffix = ""

    # ── URL helpers ───────────────────────────────────────────────────
    def normalize_url(self) -> str:
        url = self.subscription.url.strip()
        if self.subscription.type == "channel" and self.channel_suffix:
            if not re.search(r"/(videos|shorts|streams|playlists|video|audio)(?:[?#]|$)", url):
                url = url.rstrip("/") + self.channel_suffix
        return url

    # ── Discovery ─────────────────────────────────────────────────────
    def _to_items(
        self,
        entries: list[dict[str, Any]],
        *,
        channel_hint: str | None,
        playlist_hint: str | None,
    ) -> list[MediaItem]:
        items: list[MediaItem] = []
        counter = 1
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            page_url = entry.get("webpage_url") or entry.get("url")
            media_id = str(entry.get("id") or entry.get("video_id") or "")
            if not media_id:
                # Multi-part collections (Bilibili) expose only a URL here.
                media_id = _derive_media_id(str(page_url or ""))
            if not media_id or not _VIDEO_ID_RE.match(media_id):
                continue
            if entry.get("live_status") == "is_live":
                continue
            if not page_url:
                page_url = self._item_url(media_id)
            items.append(
                MediaItem(
                    media_id=media_id,
                    title=_entry_title(entry, media_id),
                    url=str(page_url),
                    channel=entry.get("channel") or entry.get("uploader") or channel_hint,
                    playlist=entry.get("playlist") or playlist_hint,
                    publish_date=_parse_date(entry),
                    index=entry.get("playlist_index") or counter,
                    duration=entry.get("duration") if isinstance(entry.get("duration"), int) else None,
                    raw=entry,
                )
            )
            counter += 1
        return items

    def _item_url(self, media_id: str) -> str:
        return media_id

    async def list_items(self, limit: int | None = None) -> list[MediaItem]:
        from app.core.config import settings

        from app.services.media_center.base import resolve_options

        opts = resolve_options(self.subscription)
        cap = limit or int(settings.MEDIA_SCAN_LIMIT or 0) or None
        data = await engine.dump_json(
            self.normalize_url(), limit=cap, opts=opts, provider=self.provider
        )

        entries = data.get("entries") or []
        if not entries and data.get("id"):
            entries = [data]  # single video page
        channel_hint = (
            data.get("channel") or data.get("uploader")
            or data.get("uploader_id") or self.subscription.name
        )
        playlist_hint = data.get("title") if data.get("_type") in ("playlist", "multi_video") else None
        items = self._to_items(entries, channel_hint=channel_hint, playlist_hint=playlist_hint)

        # A degraded flat dump is recognisable: it lists entries but gives none
        # of them an id. That is exactly what Bilibili returns for a multi-part
        # collection (bare URLs). One non-flat request resolves the whole page
        # with ids *and* real part titles.
        #
        # Checking the ids rather than `not items` also covers the case where a
        # derived id already produced items but no titles: YouTube/SoundCloud
        # flat listings always carry ids, so this never costs extra requests
        # there.
        flat_lost_ids = bool(entries) and not any(
            isinstance(entry, dict) and (entry.get("id") or entry.get("video_id"))
            for entry in entries
        )
        if flat_lost_ids or (not items and entries):
            logger.info("Flat listing was incomplete; retrying full dump for %s", self.normalize_url())
            full = await engine.dump_json(
                self.normalize_url(), limit=cap, opts=opts,
                provider=self.provider, flat=False,
            )
            full_entries = full.get("entries") or []
            if not full_entries and full.get("id"):
                full_entries = [full]
            full_channel = (
                full.get("channel") or full.get("uploader")
                or full.get("uploader_id") or channel_hint
            )
            full_playlist = (
                full.get("title")
                if full.get("_type") in ("playlist", "multi_video")
                else playlist_hint
            )
            items = self._to_items(
                full_entries, channel_hint=full_channel, playlist_hint=full_playlist
            )
        return items

    # ── Download ──────────────────────────────────────────────────────
    async def download(
        self,
        item: MediaItem,
        options: DownloadOptions,
        *,
        on_progress: ProgressCallback | None = None,
        on_line: LineHook | None = None,
    ) -> DownloadResult:
        audio_only = options.media_mode == "audio"
        channel = self.channel_name(item)
        playlist = self.playlist_name(item)

        outtmpl = output_template(
            self.subscription, self.provider, channel, playlist, index=item.index
        )
        # Extra safety: an absolute template must never escape the root.
        Path(outtmpl.replace("%(title)s", "x").replace("%(ext)s", "x")).parent.mkdir(
            parents=True, exist_ok=True
        )

        last_emit = 0.0
        aborted = False

        async def _on_line(line: str) -> bool | None:
            nonlocal last_emit, aborted
            parsed = engine.parse_progress_line(line)
            if parsed and on_progress is not None:
                now = datetime.now(timezone.utc).timestamp()
                if now - last_emit >= 0.5 or parsed["progress"] >= 100:
                    last_emit = now
                    await on_progress(
                        parsed["progress"],
                        parsed["speed"],
                        parsed["downloaded_bytes"],
                        parsed["total_bytes"],
                        parsed["eta"],
                        "downloading",
                    )
            elif engine.is_processing_line(line) and on_progress is not None:
                await on_progress(100.0, None, 0, 0, None, "processing")

            if on_line is not None:
                verdict = await on_line(line)
                if verdict is False:
                    aborted = True
                    return False
            return None

        result = await engine.download(
            item.url,
            outtmpl,
            options,
            mode=options.media_mode,
            on_line=_on_line,
        )

        if result.aborted or aborted:
            return DownloadResult(item=item, status="cancelled", title=item.title)

        if result.rc != 0:
            tail = result.tail.strip().splitlines()
            reason = tail[-1][:800] if tail else f"yt-dlp exit {result.rc}"
            hint = engine.network_hint(result.tail, self.provider)
            if hint:
                reason = f"{hint} — {reason}"
            return DownloadResult(
                item=item,
                status="failed",
                media_type=options.media_mode,
                title=item.title,
                duration=item.duration,
                error=reason,
            )

        primary = engine.pick_output(result.files, audio_only=audio_only)
        extras: list[str] = []

        # ``both``: keep the video and derive a same-name audio track.
        if options.media_mode == "both" and primary and primary.lower().endswith((".mp4", ".mkv", ".webm")):
            if on_progress is not None:
                await on_progress(100.0, None, 0, 0, None, "processing")
            rc, msg, produced = await engine.extract_audio(primary, options.audio_format)
            if rc == 0 and produced and os.path.isfile(produced):
                extras.append(produced)
            else:
                logger.warning("audio extraction failed for %s: %s", primary, msg)

        size = os.path.getsize(primary) if primary and os.path.isfile(primary) else None
        return DownloadResult(
            item=item,
            status="completed",
            file_path=primary,
            file_size=size,
            media_type=options.media_mode,
            title=item.title,
            duration=item.duration,
            extra_paths=extras,
        )


# ══════════════════════════════════════════════════════════════════════
#  YouTube
# ══════════════════════════════════════════════════════════════════════


@register_provider
class YoutubeProvider(YtDlpProvider):
    provider = "youtube"
    display_name = "YouTube"
    url_patterns = ("youtube.com", "youtu.be")
    channel_suffix = "/videos"
    url_examples = (
        "https://www.youtube.com/watch?v=…",
        "https://www.youtube.com/@Mr.EnglishChannel",
        "https://www.youtube.com/playlist?list=…",
        "https://youtu.be/…",
    )

    def _item_url(self, media_id: str) -> str:
        return f"https://www.youtube.com/watch?v={media_id}"

    @classmethod
    def validate_url(cls, url: str) -> tuple[bool, str | None]:
        ok, msg = super().validate_url(url)
        if not ok:
            return ok, msg
        low = url.lower()
        if "youtube.com" not in low and "youtu.be" not in low:
            return False, "不是 YouTube 链接"
        return True, None

    @classmethod
    def guess_type(cls, url: str) -> str:
        low = url.lower()
        if "list=" in low or "/playlist" in low:
            return "playlist"
        if re.search(r"/(@|channel/|c/|user/)", low) and "watch" not in low:
            return "channel"
        return "video"


# ══════════════════════════════════════════════════════════════════════
#  Bilibili
# ══════════════════════════════════════════════════════════════════════


@register_provider
class BilibiliProvider(YtDlpProvider):
    provider = "bilibili"
    display_name = "Bilibili"
    url_patterns = ("bilibili.com", "b23.tv")
    url_examples = (
        "https://www.bilibili.com/video/BV1xx411c7mD",
        "https://space.bilibili.com/123456/video",
        "https://space.bilibili.com/123456/channel/collectiondetail?sid=…",
        "https://space.bilibili.com/123456/favlist?fid=…",
    )

    def _item_url(self, media_id: str) -> str:
        # A part id looks like `BV1XWa36MEkP_p3`; the page URL needs `?p=3`.
        base, _, page = media_id.partition("_p")
        if page.isdigit():
            return f"https://www.bilibili.com/video/{base}?p={page}"
        return f"https://www.bilibili.com/video/{media_id}"

    @classmethod
    def validate_url(cls, url: str) -> tuple[bool, str | None]:
        ok, msg = super().validate_url(url)
        if not ok:
            return ok, msg
        low = url.lower()
        if "bilibili.com" not in low and "b23.tv" not in low:
            return False, "不是 Bilibili 链接"
        return True, None

    @classmethod
    def guess_type(cls, url: str) -> str:
        low = url.lower()
        if any(k in low for k in ("collectiondetail", "favlist", "medialist", "seriesdetail", "sid=", "fid=")):
            return "playlist"
        if "space.bilibili.com" in low or "/space/" in low:
            return "channel"
        return "video"

    def normalize_url(self) -> str:
        url = self.subscription.url.strip()
        # yt-dlp expects ``/video`` on the space page for uploads
        if self.subscription.type == "channel" and "space.bilibili.com" in url:
            if not re.search(r"/(video|dynamic|favlist|channel)(?:[?#]|$)", url):
                url = url.rstrip("/") + "/video"
        return url


# ══════════════════════════════════════════════════════════════════════
#  SoundCloud
# ══════════════════════════════════════════════════════════════════════


@register_provider
class SoundCloudProvider(YtDlpProvider):
    provider = "soundcloud"
    display_name = "SoundCloud"
    url_patterns = ("soundcloud.com",)
    url_examples = (
        "https://soundcloud.com/artist/track",
        "https://soundcloud.com/artist/sets/album",
        "https://soundcloud.com/artist",
    )

    @classmethod
    def validate_url(cls, url: str) -> tuple[bool, str | None]:
        ok, msg = super().validate_url(url)
        if not ok:
            return ok, msg
        if "soundcloud.com" not in url.lower():
            return False, "不是 SoundCloud 链接"
        return True, None

    @classmethod
    def guess_type(cls, url: str) -> str:
        low = url.lower()
        if "/sets/" in low:
            return "playlist"
        path = low.split("soundcloud.com", 1)[-1].strip("/")
        return "channel" if path and "/" not in path else "video"


# ══════════════════════════════════════════════════════════════════════
#  Vimeo
# ══════════════════════════════════════════════════════════════════════


@register_provider
class VimeoProvider(YtDlpProvider):
    provider = "vimeo"
    display_name = "Vimeo"
    url_patterns = ("vimeo.com",)
    url_examples = (
        "https://vimeo.com/123456789",
        "https://vimeo.com/channels/staffpicks",
        "https://vimeo.com/showcase/12345",
    )

    @classmethod
    def validate_url(cls, url: str) -> tuple[bool, str | None]:
        ok, msg = super().validate_url(url)
        if not ok:
            return ok, msg
        if "vimeo.com" not in url.lower():
            return False, "不是 Vimeo 链接"
        return True, None

    @classmethod
    def guess_type(cls, url: str) -> str:
        low = url.lower()
        if any(k in low for k in ("/channels/", "/showcase", "/album/", "/groups/")):
            return "playlist"
        if re.search(r"vimeo\.com/\d+", low):
            return "video"
        return "channel"


# ══════════════════════════════════════════════════════════════════════
#  Twitch / X
# ══════════════════════════════════════════════════════════════════════


@register_provider
class TwitchProvider(YtDlpProvider):
    provider = "twitch"
    display_name = "Twitch"
    url_patterns = ("twitch.tv",)
    url_examples = ("https://www.twitch.tv/videos/1234567890", "https://www.twitch.tv/somechannel")

    @classmethod
    def guess_type(cls, url: str) -> str:
        return "video" if "/videos/" in url.lower() else "channel"


@register_provider
class XProvider(YtDlpProvider):
    provider = "x"
    display_name = "X (Twitter)"
    url_patterns = ("twitter.com", "x.com")
    url_examples = ("https://x.com/user/status/1234567890",)
    note = "需要 cookies 才能抓取多数推文视频"


# ══════════════════════════════════════════════════════════════════════
#  Podcast RSS (native implementation — feedparser + httpx)
# ══════════════════════════════════════════════════════════════════════


@register_provider
class PodcastProvider(SubscriptionProvider):
    provider = "podcast"
    display_name = "Podcast RSS"
    url_patterns = ("/feed", ".rss", ".xml", "feeds.", "anchor.fm", "libsyn", "simplecast", "buzzsprout")
    supported_types = ("channel", "playlist", "video")
    url_examples = (
        "https://feeds.bbci.co.uk/learningenglish/english/features/6-minute-english/rss",
        "https://feeds.simplecast.com/54nAGcIl (The Daily)",
        "https://feeds.megaphone.fm/TEDTalksDaily",
        "任何标准 RSS 2.0 / iTunes Podcast 地址",
    )

    @classmethod
    def validate_url(cls, url: str) -> tuple[bool, str | None]:
        ok, msg = super().validate_url(url)
        if not ok:
            return ok, msg
        return True, None

    @classmethod
    def guess_type(cls, url: str) -> str:
        return "channel"

    # ── discovery ────────────────────────────────────────────────────
    async def _fetch_feed(self) -> Any:
        import httpx
        import feedparser

        policy = resolve_policy(self.subscription)
        proxy = None if policy.force_direct else policy.proxy
        async with httpx.AsyncClient(
            proxy=proxy,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, read=60.0),
            headers={"User-Agent": "NASPilot/1.0 (+media-center)"},
        ) as client:
            resp = await client.get(self.subscription.url)
            resp.raise_for_status()
            raw = resp.content
        # feedparser is synchronous but parsing is CPU-light
        return feedparser.parse(raw)

    async def list_items(self, limit: int | None = None) -> list[MediaItem]:
        feed = await self._fetch_feed()
        if getattr(feed, "bozo", 0) and not feed.entries:
            raise RuntimeError(f"RSS 解析失败: {getattr(feed, 'bozo_exception', '未知错误')}")
        channel = getattr(feed.feed, "title", None) or self.subscription.name
        items: list[MediaItem] = []
        for idx, entry in enumerate(feed.entries, start=1):
            audio_url = self._enclosure(entry)
            if not audio_url:
                continue
            media_id = str(
                entry.get("id") or entry.get("guid")
                or getattr(entry, "link", None) or audio_url
            )
            media_id = media_id[:128]
            published = None
            for key in ("published_parsed", "updated_parsed"):
                parsed = entry.get(key)
                if parsed:
                    try:
                        published = datetime(*parsed[:6], tzinfo=timezone.utc)
                    except (TypeError, ValueError):
                        published = None
                    break
            duration = None
            raw_duration = entry.get("itunes_duration")
            if raw_duration:
                duration = _parse_duration(str(raw_duration))
            items.append(
                MediaItem(
                    media_id=media_id,
                    title=str(entry.get("title") or f"Episode {idx}"),
                    url=audio_url,
                    channel=channel,
                    playlist=None,
                    publish_date=published,
                    index=idx,
                    duration=duration,
                    raw={"image": self._feed_image(feed, entry)},
                )
            )
            if limit and len(items) >= limit:
                break
        return items

    @staticmethod
    def _enclosure(entry: Any) -> str | None:
        for link in entry.get("links", []) or []:
            if link.get("rel") == "enclosure" and link.get("href"):
                if (link.get("type") or "").startswith(("audio/", "video/")) or "audio" in (link.get("type") or ""):
                    return str(link["href"])
        for enc in entry.get("enclosures", []) or []:
            href = enc.get("href") or enc.get("url")
            if href:
                return str(href)
        return None

    @staticmethod
    def _feed_image(feed: Any, entry: Any) -> str | None:
        image = entry.get("image")
        if isinstance(image, dict) and image.get("href"):
            return str(image["href"])
        for key in ("image_url", "itunes_image"):
            value = entry.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict) and value.get("href"):
                return str(value["href"])
        feed_img = getattr(feed, "feed", {}).get("image")
        if isinstance(feed_img, dict) and feed_img.get("href"):
            return str(feed_img["href"])
        return None

    # ── download ─────────────────────────────────────────────────────
    async def download(
        self,
        item: MediaItem,
        options: DownloadOptions,
        *,
        on_progress: ProgressCallback | None = None,
        on_line: LineHook | None = None,
    ) -> DownloadResult:
        from app.core.config import settings

        ext = _guess_audio_ext(item.url)
        channel = self.channel_name(item)
        playlist = self.playlist_name(item)
        directory = output_template(
            self.subscription, self.provider, channel, playlist, index=item.index
        )
        # strip the ``%(title)s.%(ext)s`` tail to get the directory
        directory = str(Path(directory).parent)
        prefix = ""
        if getattr(self.subscription, "number_prefix", False):
            prefix = f"{int(item.index or 1):03d}-"
        filename = f"{prefix}{safe_segment(item.title, 'episode', 150)}{ext}"
        dest = str(Path(directory) / filename)

        policy = resolve_policy(self.subscription)
        proxy = None if policy.force_direct else policy.proxy

        if on_progress is not None:
            await on_progress(0.0, None, 0, 0, None, "downloading")

        rc, msg, written = await engine.http_download(
            item.url, dest, proxy=proxy, on_progress=on_progress
        )
        if rc != 0:
            return DownloadResult(
                item=item,
                status="failed",
                media_type="audio",
                title=item.title,
                duration=item.duration,
                error=msg[:800],
            )

        # cover art (播客封面) — best effort, keeps the same base name
        image = (item.raw or {}).get("image")
        if options.write_thumbnail and image:
            await self._save_cover(image, dest, proxy)

        # Audio mode must never leave a video container behind: when the RSS
        # enclosure is a video file, extract the audio track (copy when the
        # codec already matches the requested format).
        if options.media_mode == "audio" and os.path.splitext(dest)[1].lower() in engine.VIDEO_EXTS:
            rc0, msg0, audio_path = await engine.extract_audio(dest, options.audio_format)
            if rc0 == 0 and audio_path and os.path.isfile(audio_path):
                try:
                    os.remove(dest)
                except OSError:
                    pass
                dest = audio_path
            else:
                logger.warning("podcast audio extraction failed: %s", msg0)

        size = os.path.getsize(dest) if os.path.isfile(dest) else written
        extra: list[str] = []
        if options.media_mode == "both":
            rc2, _m, produced = await engine.extract_audio(dest, options.audio_format)
            if rc2 == 0 and produced:
                extra.append(produced)

        return DownloadResult(
            item=item,
            status="completed",
            file_path=dest,
            file_size=size,
            media_type=options.media_mode,
            title=item.title,
            duration=item.duration,
            extra_paths=extra,
        )

    async def _save_cover(self, image_url: str, media_path: str, proxy: str | None) -> None:
        import httpx

        target = os.path.splitext(media_path)[0] + ".jpg"
        try:
            async with httpx.AsyncClient(proxy=proxy, follow_redirects=True, timeout=30.0) as client:
                resp = await client.get(image_url)
                resp.raise_for_status()
                with open(target, "wb") as fh:
                    fh.write(resp.content)
        except Exception:
            logger.debug("podcast cover download failed: %s", image_url, exc_info=True)


def _guess_audio_ext(url: str) -> str:
    path = url.split("?")[0].lower()
    for ext in (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac", ".mp4"):
        if path.endswith(ext):
            return ext
    return ".mp3"


def _parse_duration(value: str) -> int | None:
    value = value.strip()
    if value.isdigit():
        return int(value)
    parts = value.split(":")
    try:
        total = 0
        for part in parts:
            total = total * 60 + int(float(part))
        return total
    except (TypeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════════
#  Reserved providers
# ══════════════════════════════════════════════════════════════════════


class _ReservedProvider(SubscriptionProvider):
    implemented = False
    supported_types = ()

    async def list_items(self, limit: int | None = None) -> list[MediaItem]:
        raise NotImplementedError(
            f"{self.display_name} 尚未实现（预留），可先用「任意 URL」+ yt-dlp 尝试"
        )

    async def download(
        self,
        item: MediaItem,
        options: DownloadOptions,
        *,
        on_progress: ProgressCallback | None = None,
        on_line: LineHook | None = None,
    ) -> DownloadResult:
        raise NotImplementedError(f"{self.display_name} 尚未实现")


@register_provider
class XimalayaProvider(_ReservedProvider):
    provider = "ximalaya"
    display_name = "喜马拉雅（预留）"
    url_patterns = ("ximalaya.com",)
    note = "预留：需要签名算法，后续版本支持"


@register_provider
class XiaoyuzhouProvider(_ReservedProvider):
    provider = "xiaoyuzhou"
    display_name = "小宇宙（预留）"
    url_patterns = ("xiaoyuzhoufm.com",)
    note = "预留：小宇宙本质是 Podcast，可先用 Podcast RSS 或「任意 URL」"


@register_provider
class CustomProvider(YtDlpProvider):
    provider = "custom"
    display_name = "任意 URL (yt-dlp)"
    url_examples = ("任意 yt-dlp 支持的站点链接（约 1800+ 站点）",)
    note = "兜底 Provider：未识别的链接都走这里"


def provider_infos() -> list[dict[str, Any]]:
    """Metadata for the API / UI."""
    from app.services.media_center.base import all_providers

    out: list[dict[str, Any]] = []
    for name, cls in sorted(all_providers().items()):
        out.append(
            {
                "provider": name,
                "display_name": cls.display_name,
                "implemented": bool(cls.implemented),
                "supported_types": list(cls.supported_types),
                "url_examples": list(cls.url_examples),
                "note": cls.note,
            }
        )
    return out


__all__ = [
    "BilibiliProvider",
    "CustomProvider",
    "PodcastProvider",
    "SoundCloudProvider",
    "TwitchProvider",
    "VimeoProvider",
    "XProvider",
    "XiaoyuzhouProvider",
    "XimalayaProvider",
    "YoutubeProvider",
    "YtDlpProvider",
    "provider_infos",
]
