"""Extract a subtitle track that is *inside* the media file to a sidecar ``.srt``.

Many downloaded lessons carry their subtitles as a real track inside the
container (``mov_text`` in MP4, ``subrip``/``ass`` in MKV) rather than as a
separate file. The English library only looks at sidecar files, so those lessons
arrived with no transcript even though the text was right there.

Extraction is exact — no OCR, no guessing, original timings — and it writes the
same ``<name>.<lang>.srt`` a downloader would, so the scanner, the player and the
transcript parser pick it up with no further changes.

Bitmap subtitle tracks (PGS / DVD / DVB) are deliberately skipped: ffmpeg can
only turn those into images, and a picture of a subtitle is not a transcript.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger("naspilot.english.embedded")

#: Codecs ffmpeg can convert to text.
TEXT_CODECS = {
    "subrip",
    "srt",
    "ass",
    "ssa",
    "mov_text",
    "webvtt",
    "text",
    "eia_608",
    "microdvd",
    "subviewer",
    "subviewer1",
    "sami",
    "jacosub",
    "mpl2",
    "realtext",
    "stl",
    "pjs",
    "vplayer",
    "jacosub",
}

#: Codecs that are bitmaps: extracting them yields images, not text.
BITMAP_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}

_LANG_CLEAN = re.compile(r"[^A-Za-z0-9-]")

PROBE_TIMEOUT = 25
EXTRACT_TIMEOUT = 90


def _run(cmd: list[str], timeout: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def probe_tracks(media: Path) -> list[dict[str, str | int]]:
    """Subtitle tracks inside ``media``, in container order."""
    code, out = _run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "s",
            "-show_entries", "stream=index,codec_name:stream_tags=language,title",
            "-of", "default=noprint_wrappers=1",
            str(media),
        ],
        PROBE_TIMEOUT,
    )
    if code != 0:
        return []

    tracks: list[dict[str, str | int]] = []
    current: dict[str, str | int] = {}
    for line in out.splitlines():
        key, _, value = line.partition("=")
        # `-of default=noprint_wrappers=1` renders metadata as
        # `TAG:language=eng`, so compare only the leaf name or every tag is
        # silently dropped (the sidecar came out as `.und.srt`).
        key = key.strip().rsplit(":", 1)[-1]
        value = value.strip()
        if not key:
            continue
        if key == "index":
            if current:
                tracks.append(current)
            current = {"index": int(value) if value.isdigit() else -1}
        elif key in ("codec_name", "language", "title"):
            current[key] = value
    if current:
        tracks.append(current)
    return [track for track in tracks if track.get("index", -1) >= 0]


def _pick_track(tracks: list[dict[str, str | int]]) -> dict[str, str | int] | None:
    """Prefer a text track, and English over anything else."""
    text_tracks = [t for t in tracks if str(t.get("codec_name", "")).lower() in TEXT_CODECS]
    if not text_tracks:
        return None

    def score(track: dict[str, str | int]) -> int:
        lang = str(track.get("language", "")).lower()
        if lang.startswith("en"):
            return 3
        # "und"/missing is usually the only track in a single-language file.
        return 2 if lang in ("", "und", "unknown") else 1

    return max(text_tracks, key=score)


#: ISO 639-2 (what containers usually store) → ISO 639-1, which is what the
#: scanner's language ranking and the `--sub-langs` list use.
_LANG_ALIASES = {
    "eng": "en",
    "chi": "zh",
    "zho": "zh",
    "cmn": "zh",
    "yue": "zh-yue",
    "jpn": "ja",
    "kor": "ko",
    "fra": "fr",
    "fre": "fr",
    "deu": "de",
    "ger": "de",
    "spa": "es",
    "rus": "ru",
    "por": "pt",
    "ita": "it",
}


def _language_tag(track: dict[str, str | int]) -> str:
    lang = str(track.get("language") or "").strip().lower()
    # yt-dlp/ffmpeg sometimes write `en-US` / `eng_US`; keep the primary subtag.
    lang = _LANG_CLEAN.sub("-", lang).strip("-")
    primary = lang.split("-", 1)[0]
    return _LANG_ALIASES.get(primary, primary) or "und"


async def extract_sidecar(media: Path, *, overwrite: bool = False) -> Path | None:
    """Write ``<media name>.<lang>.srt`` from an embedded track, if there is one.

    Returns the created path, or ``None`` when the file has no text subtitle
    track (or already has a sidecar).
    """
    if not media.is_file():
        return None

    existing = sorted(media.parent.glob(f"{media.stem}.*.srt")) + sorted(
        media.parent.glob(f"{media.stem}.srt")
    )
    if existing and not overwrite:
        return None

    tracks = await asyncio.to_thread(probe_tracks, media)
    track = _pick_track(tracks)
    if track is None:
        if tracks:
            logger.info(
                "Subtitle tracks in %s are bitmap-only; OCR would be required", media.name
            )
        return None

    tag = _language_tag(track)
    target = media.with_name(f"{media.stem}.{tag}.srt")
    index = int(track["index"])

    code, out = await asyncio.to_thread(
        _run,
        [
            "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
            "-i", str(media),
            "-map", f"0:{index}",
            "-c:s", "srt",
            "-f", "srt",
            str(target),
        ],
        EXTRACT_TIMEOUT,
    )
    if code != 0 or not target.is_file() or target.stat().st_size == 0:
        logger.warning(
            "Could not extract the embedded subtitle from %s: %s", media.name, out[-300:]
        )
        target.unlink(missing_ok=True)
        return None

    logger.info("Extracted embedded subtitle %s -> %s", media.name, target.name)
    return target


__all__ = ["BITMAP_CODECS", "TEXT_CODECS", "extract_sidecar", "probe_tracks"]
