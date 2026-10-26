"""Metadata inference for the English Learning Center.

Everything here is deterministic and local — no AI, no network. Given a
directory path and a file name it derives: channel, course title, category,
difficulty, slug and a human title.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: category keywords matched (case-insensitive) against the path
_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Business", ("business", "商务", "workplace", "office", "meeting", "career")),
    ("Daily", ("daily", "日常", "conversation", "口语", "life", "travel", "shopping")),
    ("Podcast", ("podcast", "播客", "radio", "interview")),
    ("TED", ("ted", "tedx")),
    ("BBC", ("bbc", "6-minute", "6 minute", "learning english")),
    ("News", ("news", "新闻", "voa", "cnn")),
    ("Music", ("song", "music", "音乐", "lyrics")),
    ("Movie", ("movie", "film", "电影", "clip")),
    ("Grammar", ("grammar", "语法", "tense", "tense")),
    ("Exam", ("ielts", "toefl", "cet", "雅思", "托福", "四级", "六级")),
)

_DIFFICULTY_BY_CATEGORY = {
    "TED": "advanced",
    "News": "advanced",
    "Exam": "advanced",
    "Business": "intermediate",
    "Podcast": "intermediate",
    "Grammar": "intermediate",
    "BBC": "intermediate",
    "Daily": "beginner",
    "Music": "beginner",
    "Movie": "intermediate",
}

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_WS = re.compile(r"\s+")
#: Leading lesson numbering: "001 - Title", "12. Title", "(3) Title", "[04] Title",
#: "Lesson 3 - Title". The separator is REQUIRED so titles that merely start
#: with a number ("6 Minute English") are left untouched.
_NUMBERING = re.compile(
    r"^\s*(?:"
    r"\[\d{1,4}\]\s*"
    r"|\(?\d{1,4}\)?\s*[\.\)\-–—:]\s+"
    r"|(?:lesson|episode|ep|part|chapter)\s*\d{1,4}\s*[-–—:.]?\s*"
    r")",
    re.IGNORECASE,
)
_BRACKETS = re.compile(r"[\[【(（][^\]】)）]{0,40}[\]】)）]")


def safe_segment(value: str | None, fallback: str = "未命名", max_len: int = 120) -> str:
    cleaned = _UNSAFE.sub("_", (value or "").strip())
    cleaned = _WS.sub(" ", cleaned).strip(" .")
    return cleaned[:max_len] or fallback


def slugify(value: str) -> str:
    text = safe_segment(value).lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text).strip("-")
    return text or "untitled"


def classify(path_parts: list[str]) -> tuple[str, str]:
    """Return ``(category, difficulty)`` derived from the path components."""
    haystack = " ".join(path_parts).lower()
    for category, keywords in _CATEGORY_RULES:
        if any(k in haystack for k in keywords):
            return category, _DIFFICULTY_BY_CATEGORY.get(category, "mixed")
    return "其他", "mixed"


def clean_title(raw: str) -> str:
    """Human title from a file/directory name: drop numbering and brackets."""
    name = Path(raw).stem if Path(raw).suffix else raw
    name = name.replace("_", " ")
    name = _NUMBERING.sub("", name)
    name = _BRACKETS.sub(" ", name)
    name = _WS.sub(" ", name).strip(" -–—.")
    return name or Path(raw).stem or raw


def lesson_position(name: str) -> tuple[int, str]:
    """Extract leading index from a file name for ordering.

    ``"001 - Greetings"`` → ``(1, "Greetings")``; unordered → ``(0, name)``.
    """
    stem = Path(name).stem if Path(name).suffix else name
    match = re.match(r"^\s*(\d{1,4})\b", stem)
    if match:
        return int(match.group(1)), clean_title(name)
    match = re.search(r"(?:lesson|episode|ep|part|chapter)\s*(\d{1,4})", stem, re.IGNORECASE)
    if match:
        return int(match.group(1)), clean_title(name)
    return 0, clean_title(name)


def read_course_override(directory: Path) -> dict[str, Any]:
    """Load an optional ``course.json`` next to the media files.

    Any problem (missing file, bad JSON, wrong types) is ignored so scanning
    never breaks because of a malformed sidecar.
    """
    import json

    for name in ("course.json", "course.meta.json"):
        candidate = directory / name
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        out: dict[str, Any] = {}
        for key in ("title", "category", "difficulty", "description", "cover", "source"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                out[key] = value.strip()
        tags = data.get("tags")
        if isinstance(tags, list):
            out["tags"] = [str(t) for t in tags if str(t).strip()][:20]
        if out:
            return out
    return {}


def guess_source(path_parts: list[str]) -> str | None:
    """Best-effort provider name, matching Media Download Center dir layout."""
    haystack = " ".join(part.lower() for part in path_parts)
    for provider, keys in (
        ("youtube", ("youtube", "yt-")),
        ("podcast", ("podcast", "rss", "播客")),
        ("bilibili", ("bilibili", "b站")),
        ("ted", ("ted",)),
        ("bbc", ("bbc",)),
    ):
        if any(k in haystack for k in keys):
            return provider
    return None


__all__ = [
    "classify",
    "clean_title",
    "guess_source",
    "lesson_position",
    "read_course_override",
    "safe_segment",
    "slugify",
]
