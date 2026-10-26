"""Subtitle parsing for the English Learning Center.

Supports SubRip (``.srt``) and WebVTT (``.vtt``) → a normalized list of
segments. Timestamps are converted to **integer milliseconds** so the client
never has to deal with float comparison, and the same value can be used for
search / AI context / progress positions.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("naspilot.english.subtitles")

#: ``00:00:02,400`` (srt) / ``00:00:02.400`` / ``00:02.400`` (vtt)
_TIME = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[.,](?P<ms>\d{1,3})"
    r"|(?P<m2>\d{1,2}):(?P<s2>\d{2})[.,](?P<ms2>\d{1,3})"
)
_CUE_TIME = re.compile(r"^\s*(?P<start>\S+)\s*-->\s*(?P<end>\S+)")
_TAG = re.compile(r"<[^>]+>")
_ASS_ONLY = re.compile(r"^\s*Dialogue:")

SUBTITLE_EXTS = {".srt", ".vtt"}


@dataclass(frozen=True)
class Segment:
    index: int
    start: int  # ms
    end: int  # ms
    text: str

    def as_dict(self) -> dict[str, object]:
        return {"i": self.index, "start": self.start, "end": self.end, "text": self.text}


def _to_ms(value: str) -> int | None:
    match = _TIME.search(value.strip())
    if not match:
        return None
    if match.group("h") is not None:
        h, m, s, ms = (
            int(match.group("h")),
            int(match.group("m")),
            int(match.group("s")),
            int(match.group("ms").ljust(3, "0")),
        )
    else:
        h, m, s, ms = (
            0,
            int(match.group("m2")),
            int(match.group("s2")),
            int(match.group("ms2").ljust(3, "0")),
        )
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def _clean_text(raw: str) -> str:
    """Strip cue markup, ASS overrides and HTML entities."""
    text = _TAG.sub(" ", raw)
    text = re.sub(r"\{\\[^}]*\}", " ", text)  # ASS style overrides
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def read_text(path: Path) -> str:
    """Decode a subtitle file, tolerating BOM and non-UTF-8 encodings."""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_text(text: str) -> list[Segment]:
    """Parse srt/vtt content into segments (tolerant of malformed cues)."""
    segments: list[Segment] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    i = 0
    pending_text: list[str] = []
    start: int | None = None
    end: int | None = None

    def flush() -> None:
        nonlocal pending_text, start, end
        if start is not None and pending_text:
            body = _clean_text(" ".join(pending_text))
            if body:
                segments.append(
                    Segment(index=len(segments), start=start, end=end or start, text=body)
                )
        pending_text = []
        start = None
        end = None

    while i < len(lines):
        line = lines[i]
        cue = _CUE_TIME.match(line)
        if cue:
            flush()
            start = _to_ms(cue.group("start"))
            end = _to_ms(cue.group("end"))
            if start is None:  # unusable cue header → skip line
                start = end = None
            i += 1
            continue

        stripped = line.strip()
        if not stripped:
            # Blank line terminates the current cue (srt) but VTT may use
            # blank lines between header and body — flushing is harmless.
            flush()
            i += 1
            continue

        if _ASS_ONLY.match(stripped) or stripped.upper() == "WEBVTT":
            i += 1
            continue
        if stripped.isdigit() and start is None:
            i += 1  # srt cue number
            continue
        if stripped.startswith(("NOTE ", "STYLE", "REGION")):
            i += 1
            continue
        if re.match(r"^[A-Za-z]+:", stripped) and start is None:
            i += 1  # VTT cue id like "cue-1:"
            continue

        pending_text.append(stripped)
        i += 1

    flush()
    return segments


def parse_file(path: Path) -> list[Segment]:
    """Parse a subtitle file; returns an empty list on any failure."""
    try:
        if not path.is_file():
            return []
        segments = parse_text(read_text(path))
        if not segments:
            logger.debug("No segments parsed from %s", path)
        return segments
    except Exception:
        logger.warning("Failed to parse subtitle %s", path, exc_info=True)
        return []


def duration_ms(segments: list[Segment]) -> int:
    return max((s.end for s in segments), default=0)


def search_text(segments: list[Segment], limit: int = 200_000) -> str:
    """Concatenated sentences, used by the MVP search (``LIKE``)."""
    joined = " ".join(s.text for s in segments)
    return joined[:limit]


def to_json(segments: list[Segment]) -> list[dict[str, object]]:
    return [s.as_dict() for s in segments]


__all__ = [
    "SUBTITLE_EXTS",
    "Segment",
    "duration_ms",
    "parse_file",
    "parse_text",
    "read_text",
    "search_text",
    "to_json",
]
