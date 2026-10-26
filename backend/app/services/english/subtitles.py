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

#: Cues shorter than this are capture artifacts, not speech. YouTube auto
#: captions emit 10 ms "transition" frames between every real caption.
_MIN_CUE_MS = 120

#: Cue that must never be merged into a sentence even if it is very short.
_MAX_MERGED_CHARS = 320
_MAX_MERGED_MS = 30_000

#: ``[music]`` / ``(applause)`` / ``♪`` — sound tags, not language.
_NOISE_TAG = re.compile(r"[\[\]\(\u266a\u266b][^\])\u266a\u266b]{0,40}[\])\u266a\u266b]")

#: A period after these does not end a sentence.
_ABBREV = frozenset(
    """
    mr mrs ms dr prof st jr sr vs etc fig approx no nos vol pp al ed
    jan feb mar apr jun jul aug sep sept oct nov dec mon tue wed thu fri sat
    """.split()
)


@dataclass(frozen=True)
class Segment:
    index: int
    start: int  # ms
    end: int  # ms
    text: str
    #: Speaker turn label ("A"/"B"/…), ``None`` when the source has no markers.
    speaker: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "i": self.index,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "spk": self.speaker,
        }


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


def _words(text: str) -> list[str]:
    return text.split()


def _overlap_trim(prev: str, current: str, *, max_words: int = 24) -> str:
    """Drop the words ``current`` repeats from the tail of ``prev``.

    Rolling captions re-state the previous line before adding new text::

        prev    "Hello everyone and welcome back to Mr."
        current "Hello everyone and welcome back to Mr. English channel …"

    Returns the genuinely new tail (possibly an empty string).
    """
    prev_words = _words(prev)
    cur_words = _words(current)
    limit = min(len(prev_words), len(cur_words), max_words)
    for size in range(limit, 0, -1):
        if [w.lower() for w in prev_words[-size:]] == [w.lower() for w in cur_words[:size]]:
            return " ".join(cur_words[size:])
    return current


def _ends_sentence(text: str) -> bool:
    stripped = text.rstrip(' "\'\u201d\u2019)')
    if not stripped or stripped[-1] not in ".!?":
        return False
    if stripped[-1] != ".":
        return True
    tail = re.search(r"([A-Za-z.]+)$", stripped)
    if tail is None:
        return True
    word = tail.group(1).lower().strip(".")
    if not word:
        return True
    if word in _ABBREV:
        return False
    if len(word) == 1 and word.isalpha():  # initials: "J. Smith"
        return False
    return True


def _dedupe_key(text: str) -> str:
    """Punctuation/case-insensitive key used to spot repeated captions."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _split_turns(text: str) -> list[tuple[str, bool]]:
    """Split cue text at ``>>`` markers.

    Auto-captions accumulate text, so the marker sits *inside* a cue right
    before the words of the next speaker::

        "… I'm Emily. >> Hello Emily. Hi everyone. It is"

    Returns ``[(chunk, starts_new_turn), …]``.
    """
    parts = re.split(r">>+", text)
    return [(part, idx > 0) for idx, part in enumerate(parts)]


def normalize(segments: list[Segment]) -> list[Segment]:
    """Turn rolling auto-captions into clean, speaker-separated line blocks.

    What the input actually looks like (YouTube/Podcast auto-captions):

    * most cues are 10 ms empty "transition" frames;
    * every cue repeats the tail of the previous one, so text quadruples;
    * sound tags (``[music]``) are interleaved;
    * ``>>`` marks the point where another person starts talking.

    So the passes are: drop empty/noise cues → remove the repeated prefix →
    cut at every ``>>`` and close the block there (this is what keeps one
    speaker's lines together instead of gluing two people together) → merge
    until a real sentence ends → drop a block that repeats the previous one.
    """
    if not segments:
        return []

    cleaned: list[tuple[int, int, str]] = []
    for seg in sorted(segments, key=lambda s: (s.start, s.end)):
        text = _NOISE_TAG.sub(" ", seg.text or "")
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if seg.end - seg.start < _MIN_CUE_MS:
            continue
        if cleaned:
            start, end, prev = cleaned[-1]
            trimmed = _overlap_trim(prev, text)
            if not trimmed.strip():
                # Pure repeat — keep the later end time so timing stays honest.
                cleaned[-1] = (start, max(end, seg.end), prev)
                continue
            text = trimmed
        cleaned.append((seg.start, seg.end, text))

    merged: list[Segment] = []
    buf_start: int | None = None
    buf_end = 0
    buf: list[str] = []
    buf_turn: int = -1
    turn = -1  # -1 = no speaker markers seen yet

    def flush() -> None:
        nonlocal buf_start, buf_end, buf, buf_turn
        if buf_start is not None and buf:
            body = " ".join(buf)
            body = _NOISE_TAG.sub(" ", body)
            body = re.sub(r">>+", " ", body)
            body = re.sub(r"\s+", " ", body).strip(" -").strip()
            if body:
                label = None if buf_turn < 0 else ("AB"[buf_turn % 2])
                merged.append(
                    Segment(
                        index=0,  # renumbered below
                        start=buf_start,
                        end=max(buf_end, buf_start),
                        text=body,
                        speaker=label,
                    )
                )
        buf_start = None
        buf_end = 0
        buf = []
        buf_turn = -1

    for start, end, text in cleaned:
        for chunk, is_turn in _split_turns(text):
            chunk = re.sub(r"\s+", " ", chunk).strip(" -").strip()
            if is_turn:
                # Speaker change: close whatever this person was saying first,
                # so each speaker keeps their own consecutive block.
                flush()
                turn = 0 if turn < 0 else turn + 1
            if not chunk:
                continue
            if buf_start is None:
                buf_start = start
                buf_turn = turn
            buf_end = end
            buf.append(chunk)
            joined = " ".join(buf)
            if (
                _ends_sentence(joined)
                or len(joined) >= _MAX_MERGED_CHARS
                or buf_end - buf_start >= _MAX_MERGED_MS
            ):
                flush()
    flush()

    deduped: list[Segment] = []
    seen_key = ""
    for seg in merged:
        key = _dedupe_key(seg.text)
        if key and key == seen_key:
            if deduped:
                prev = deduped[-1]
                deduped[-1] = Segment(
                    index=prev.index,
                    start=prev.start,
                    end=max(prev.end, seg.end),
                    text=prev.text,
                    speaker=prev.speaker,
                )
            continue
        seen_key = key
        deduped.append(seg)

    return [
        Segment(index=i, start=s.start, end=s.end, text=s.text, speaker=s.speaker)
        for i, s in enumerate(deduped)
    ]


def parse_file(path: Path) -> list[Segment]:
    """Parse a subtitle file; returns an empty list on any failure."""
    try:
        if not path.is_file():
            return []
        segments = normalize(parse_text(read_text(path)))
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
    "normalize",
    "parse_file",
    "parse_text",
    "read_text",
    "search_text",
    "to_json",
]
