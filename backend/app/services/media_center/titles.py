"""Turn a 100-character video title into a short name for the file.

Real titles are unusable as file names: a Bilibili collection's parts are named
`<the whole collection title> p03 <part>`, which is 186 bytes on disk and shows
up as the lesson name in the English library. This module reduces that to a
handful of characters.

Two properties matter more than quality:

* **It must always answer.** Downloads run unattended; an AI outage must not
  fail a download or leave it with the raw 186-byte name. There is therefore a
  local fallback that needs no network at all.
* **It must not merge parts.** A collection's parts share the same collection
  title, so the part-specific part of the title is what gets summarised, and the
  `pNN` marker is kept in front. Without that every part would be called the
  same thing — the collision bug that once collapsed 20 downloads into 1 file.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

logger = logging.getLogger("naspilot.media.titles")

#: Default cap, in characters. Configurable via ``MEDIA_TITLE_MAX_CHARS``.
MAX_CHARS = 8

#: Width of the part prefix (`03 `), which counts against ``MAX_CHARS``.
#:
#: `p03 ` cost 4 of the 8 characters and pushed the model's answer out of
#: budget, so a 2-character prefix is used instead.
MARKER_WIDTH = 2

#: yt-dlp/Bilibili put the part number in the title as ` p03 `.
_PART_RE = re.compile(r"\s*[pP]0*(\d{1,3})(?![0-9])\s*")

#: Bracketed asides: 【…】 […], （…）, (…). Often the useful part, often noise.
_BRACKET_RE = re.compile(r"[【\[（(]([^】\]）)]*)[】\]）)]")

#: Filler that says nothing about the actual content.
_NOISE = (
    "完整全集", "完整版", "全集", "全集合集", "合集", "高清", "超清", "蓝光",
    "中英发音", "中英字幕", "中英双语", "双语字幕", "中文字幕", "英文字幕",
    "油管", "YouTube", "youtube", "爆火", "热门", "附完整资料", "附资料",
    "日积月累", "坚持", "磨耳朵", "训练视频", "听力训练", "官方", "正片",
)

#: Characters allowed to survive into a file name.
_DROP = re.compile(r"[\"'“”‘’《》〈〉·・|｜/\\:*?<>+~^`、，。！？；：\s_]+")

#: Decoration a model wraps around an answer. Unlike `_DROP` these are also
#: removed when they are the *whole* string: `【初级听力】` must become
#: `初级听力`, not "".
_DECOR_RE = re.compile(r"[\"'“”‘’《》〈〉【】\[\]（）()「」『』\s]+")

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def part_segments(title: str) -> tuple[str, str, str]:
    """Split `… p03 第1部分` into ``("p03", "before", "after")``.

    ``after`` is the part-specific text: for a collection that is the only thing
    that tells the parts apart, and it is what should be summarised.
    """
    value = title or ""
    match = _PART_RE.search(value)
    if not match:
        return "", value.strip(), ""
    return (
        f"p{int(match.group(1)):02d}",
        value[: match.start()].strip(),
        value[match.end() :].strip(),
    )


def part_marker(title: str) -> tuple[str, str]:
    """Backwards-compatible form: ``("p03", everything-but-the-marker)``."""
    marker, before, after = part_segments(title)
    if not marker:
        return "", before
    return marker, f"{before} {after}".strip()


def _strip_noise(text: str) -> str:
    out = text
    for word in _NOISE:
        out = out.replace(word, " ")
    out = _DROP.sub(" ", out)
    out = re.sub(r"\b\d{3,4}p?\b", " ", out)  # 1080, 720p
    out = re.sub(r"\s+", " ", out).strip()
    return out


#: Leading numbering such as `1_`, `03-`, `2 ` — a part index, not content.
_LEADING_NUMBER = re.compile(r"^[\d_\-\s]+")


def _clean(text: str) -> str:
    """Strip what a model likes to add: quotes, brackets, punctuation.

    Bracket *characters* are dropped but their contents are kept — the answer is
    usually inside them.
    """
    value = (text or "").strip()
    value = value.splitlines()[0] if value else ""
    value = _DECOR_RE.sub("", value)
    value = _DROP.sub("", value)
    # `1_听力9000句` must not become `1听力9` after truncation.
    value = _LEADING_NUMBER.sub("", value)
    return value.strip()


def local_shorten(title: str, max_chars: int = MAX_CHARS) -> str:
    """Best-effort shortening with no network. "" when it cannot help.

    This heuristic removes filler words from Chinese titles. It **cannot
    translate**, so for an English title it degenerates into `5Everyda` — a
    meaningless fragment. Returning "" lets the caller keep the real title,
    which is far more useful than a mangled one.
    """
    text = (title or "").strip()
    if not text or not _CJK.search(text):
        return ""

    # A bracketed aside is usually the topic, so try those first …
    for candidate in _BRACKET_RE.findall(text):
        cleaned = _clean(candidate)
        if 2 <= len(cleaned) <= max_chars and _CJK.search(cleaned):
            return cleaned

    # … and drop the asides before falling back to "first few characters",
    # since in a long upload title they are filler.
    cleaned = _clean(_strip_noise(_BRACKET_RE.sub(" ", text)))
    if not cleaned:
        cleaned = _clean(_strip_noise(text))
    if not cleaned:
        cleaned = _clean(text)
    if not cleaned:
        return ""

    # A run of digits ("9000句") is usually the most identifying part.
    numbered = re.search(r"[\u4e00-\u9fff]{0,3}\d{3,5}[\u4e00-\u9fff]{0,2}", cleaned)
    if numbered and 2 <= len(numbered.group(0)) <= max_chars:
        return numbered.group(0)

    return cleaned[:max_chars]


def _looks_valid(value: str, max_chars: int) -> bool:
    if not value or len(value) > max_chars:
        return False
    # The library is Chinese-facing; an English-only string means the model
    # ignored the instruction (or echoed the input), so the fallback is better.
    return bool(_CJK.search(value))


#: Cache keyed by (input, max_chars) so a 20-part collection is not 20 AI calls
#: for the same collection title, and a re-download costs nothing.
_cache: dict[tuple[str, int], str] = {}

_PROMPT = (
    "把下面的视频标题概括成不超过 {n} 个汉字的最核心短语。\n"
    "要求：只输出短语本身；不要引号、书名号、标点、空格、数字编号；"
    "不要英文、不要解释、不要括号说明。\n"
    "标题：{text}"
)


async def short_title(
    title: str,
    *,
    index: int | None = None,
    max_chars: int = MAX_CHARS,
    summarizer: Callable[[str, int], Awaitable[str]] | None = None,
    db: Any = None,
) -> str:
    """A short, file-safe name for ``title`` (never empty for a non-empty title).

    ``summarizer`` is injectable so the AI can be stubbed in tests. When it is
    absent the real AI service is used, and any failure falls back to
    :func:`local_shorten`.
    """
    marker, before, after = part_segments(title or "")
    # A part's distinguishing text is what follows the marker; summarising the
    # shared collection title instead gave every part the same name.
    if marker and len(after) >= 2:
        source = after
    else:
        source = f"{before} {after}".strip() or (title or "").strip()
    if not source:
        return ""

    limit = max(2, min(int(max_chars or MAX_CHARS), 40))

    # `03 ` rather than `p03 `: two characters instead of four, so the summary
    # keeps enough room to be meaningful.
    number = ""
    if marker:
        number = marker.lstrip("pP0") or "0"
    if index is not None:
        number = str(int(index))
    marker_text = f"{int(number):02d} " if number.isdigit() else ""
    budget = max(2, limit - len(marker_text))

    key = (source, budget)
    cached = _cache.get(key)
    if cached:
        return f"{marker_text}{cached}"[:limit]

    summary = ""
    if summarizer is not None:
        try:
            summary = _clean(await summarizer(source, budget))
        except Exception:  # noqa: BLE001 - a summariser must never break a download
            logger.warning("Title summariser failed for %r", source[:60])
    else:
        summary = await _ai_shorten(source, budget, db=db)

    # A model answer that is merely too long is still the best text available:
    # truncating it beats discarding it for the heuristic.
    if summary and len(summary) > budget and _CJK.search(summary):
        summary = summary[:budget]

    ai_answer_used = _looks_valid(summary, budget)
    if not ai_answer_used:
        summary = local_shorten(source, budget)
    if not summary:
        # "" means "keep the raw title" for the caller — never a bare `p03`.
        return ""

    # Only model output is cached. Caching the heuristic made a later call that
    # *did* reach the AI return the offline answer instead.
    if ai_answer_used:
        _cache[key] = summary
    return f"{marker_text}{summary}"[:limit]


async def _ai_shorten(text: str, budget: int, *, db: Any = None) -> str:
    """Ask the configured model, or return "" when it cannot be used.

    The API key normally lives in the settings table, not in the environment —
    `resolve_config(None)` would report "not configured" and silently disable
    this feature. A short-lived session is opened when the caller has none.
    """
    try:
        from app.services import ai_service
    except Exception:  # noqa: BLE001
        return ""

    prompt = _PROMPT.format(n=budget, text=text[:400])

    async def ask(session: Any) -> str:
        return await ai_service.ask(
            prompt, db=session, temperature=0.2, max_tokens=40
        )

    try:
        if db is not None:
            return (await ask(db) or "").strip()

        from app.core.database import async_session_factory

        async with async_session_factory() as session:
            return (await ask(session) or "").strip()
    except Exception:  # noqa: BLE001 - unconfigured, offline, quota, parse…
        logger.info("AI title summariser unavailable; using the local fallback")
        return ""


def clear_cache() -> None:
    _cache.clear()


__all__ = [
    "MAX_CHARS",
    "clear_cache",
    "local_shorten",
    "part_marker",
    "part_segments",
    "short_title",
]
