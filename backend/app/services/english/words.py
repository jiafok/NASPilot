"""Per-user saved words (生词本) — server side.

The Stage-0 client kept the word book in ``localStorage['ef_words']``, so the
same account showed a *different* word book on the phone and on the laptop: the
words were never sent to the server at all. This module moves them into the
database, deduplicated per user, so every device reads the same list.

Two deliberate choices:

* ``begin_write`` is taken up front on every write. The row is looked up and
  then inserted/updated, and SQLite's default deferred transaction would let two
  devices saving the same word at once both "win" (one row would fail the unique
  index at commit, or the second write would be lost).
* The text the learner actually tapped (``display``) is stored next to the
  normalised key, so the list keeps the original capitalisation while dedupe
  stays case-insensitive.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import begin_write
from app.models import EnglishLesson, EnglishSavedWord

logger = logging.getLogger("naspilot.english.words")

#: Hard cap per user — a runaway client must not be able to fill the database.
MAX_WORDS = 2000


def normalise(word: str) -> str:
    """Collapse whitespace, lowercase, and clamp to the column width."""
    return " ".join((word or "").split()).strip().lower()[:64]


def payload(row: EnglishSavedWord) -> dict[str, Any]:
    """Serialise to the shape the client's ``SavedWord`` already uses."""
    return {
        "word": row.display or row.word,
        "key": row.word,
        "phonetic": row.phonetic or "",
        "meaning": row.meaning or "",
        "lessonId": row.lesson_id,
        "sentence": row.sentence,
        "examples": list(row.examples or []),
        "addedAt": int(row.added_at or 0),
    }


async def list_words(db: AsyncSession, user_id: int, *, limit: int = MAX_WORDS) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(EnglishSavedWord)
            .where(EnglishSavedWord.user_id == user_id)
            .order_by(EnglishSavedWord.added_at.desc(), EnglishSavedWord.id.desc())
            .limit(max(1, min(limit, MAX_WORDS)))
        )
    ).scalars().all()
    return {"items": [payload(row) for row in rows], "total": len(rows)}


async def save_word(
    db: AsyncSession,
    user_id: int,
    *,
    word: str,
    phonetic: str | None = None,
    meaning: str | None = None,
    lesson_id: int | None = None,
    sentence: str | None = None,
    examples: list[str] | None = None,
    added_at: int | None = None,
) -> dict[str, Any] | None:
    """Insert the word if the user does not have it yet; never duplicates.

    Returns ``None`` for an empty word (the caller turns that into a 400).
    Repeat saves return the existing row with ``created: False`` so the client
    can retry a failed sync safely.
    """
    key = normalise(word)
    if not key:
        return None

    await begin_write(db)
    existing = (
        await db.execute(
            select(EnglishSavedWord).where(
                EnglishSavedWord.user_id == user_id, EnglishSavedWord.word == key
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        # Only fill gaps: a definition the learner already has must not be
        # overwritten by a later, possibly worse, lookup.
        if not existing.meaning and meaning:
            existing.meaning = meaning
        if not existing.phonetic and phonetic:
            existing.phonetic = phonetic
        if not existing.sentence and sentence:
            existing.sentence = sentence
        if not existing.examples and examples:
            existing.examples = list(examples)
        if existing.lesson_id is None and lesson_id is not None:
            existing.lesson_id = lesson_id
        await db.commit()
        await db.refresh(existing)
        return {**payload(existing), "created": False}

    if lesson_id is not None and await db.get(EnglishLesson, lesson_id) is None:
        # A deleted lesson must not make saving the word fail.
        lesson_id = None

    count = len(
        (
            await db.execute(
                select(EnglishSavedWord.id).where(EnglishSavedWord.user_id == user_id).limit(MAX_WORDS)
            )
        ).all()
    )
    if count >= MAX_WORDS:
        logger.warning("Saved-word cap reached for user %s (%s)", user_id, MAX_WORDS)
        return None

    row = EnglishSavedWord(
        user_id=user_id,
        word=key,
        display=(word or "").strip()[:64] or key,
        phonetic=(phonetic or None),
        meaning=(meaning or None),
        lesson_id=lesson_id,
        sentence=(sentence or None),
        examples=list(examples or []),
        # The client sends the original save time when it migrates its local
        # list, so an old word does not jump to the top.
        added_at=int(added_at) if added_at else int(time.time() * 1000),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {**payload(row), "created": True}


async def remove_word(db: AsyncSession, user_id: int, word: str) -> dict[str, Any]:
    key = normalise(word)
    await begin_write(db)
    row = (
        await db.execute(
            select(EnglishSavedWord).where(
                EnglishSavedWord.user_id == user_id, EnglishSavedWord.word == key
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return {"word": key, "removed": False}
    await db.delete(row)
    await db.commit()
    return {"word": key, "removed": True}


async def has_word(db: AsyncSession, user_id: int, word: str) -> bool:
    key = normalise(word)
    if not key:
        return False
    found = (
        await db.execute(
            select(EnglishSavedWord.id).where(
                EnglishSavedWord.user_id == user_id, EnglishSavedWord.word == key
            )
        )
    ).scalar_one_or_none()
    return found is not None


__all__ = ["MAX_WORDS", "has_word", "list_words", "normalise", "payload", "remove_word", "save_word"]
