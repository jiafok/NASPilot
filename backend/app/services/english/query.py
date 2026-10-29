"""Read-side aggregation for the English Learning Open API.

Keeping the query/serialization logic here means `api/v1/english.py` stays a
thin HTTP layer, and the same payloads can later be reused by a scheduled
"send me my weekly summary" job without duplicating SQL.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import begin_write
from app.models import (
    EnglishBookmark,
    EnglishCourse,
    EnglishDailyStat,
    EnglishFavorite,
    EnglishLesson,
    EnglishProgress,
    EnglishTranscript,
    EnglishVocabulary,
    User,
)

logger = logging.getLogger("naspilot.english.query")


def cover_url(course: EnglishCourse) -> str | None:
    return f"/api/stream/cover/{course.id}" if course.cover_path else None


def lesson_cover_url(lesson: EnglishLesson) -> str | None:
    return f"/api/stream/lesson-cover/{lesson.id}" if lesson.cover_path else None


def media_url(lesson: EnglishLesson) -> str:
    return f"/api/media/{lesson.id}"


def subtitle_url(lesson: EnglishLesson) -> str | None:
    return f"/api/subtitle/{lesson.id}" if lesson.subtitle_path else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _progress_ratio(position: float | None, duration: int | None) -> float:
    if not duration:
        return 0.0
    return round(min(max((position or 0.0) / duration, 0.0), 1.0), 4)


# ── Courses ──────────────────────────────────────────────────────────────


async def favorite_course_ids(db: AsyncSession, user_id: int) -> set[int]:
    rows = await db.execute(select(EnglishFavorite.course_id).where(EnglishFavorite.user_id == user_id))
    return {int(r[0]) for r in rows.all()}


async def serialize_course(
    course: EnglishCourse, *, favorite: bool = False
) -> dict[str, Any]:
    return {
        "id": course.id,
        "title": course.title,
        "channel": course.channel,
        "category": course.category,
        "difficulty": course.difficulty,
        # multi-dimensional classification (see services/english/taxonomy.py)
        "level": course.level or "B1",
        "topics": list(course.topics or []),
        "skills": list(course.skills or []),
        "format": course.format or "monologue",
        "description": course.description,
        "tags": list(course.tags or []),
        "lesson_count": course.lesson_count,
        "total_seconds": course.total_seconds,
        "source": course.source,
        "cover": cover_url(course),
        "is_favorite": favorite,
        "updated_at": course.updated_at.isoformat() if course.updated_at else None,
    }


def _json_has(column: str, param: str, value: str):
    """``EXISTS`` for a label inside a JSON array column (SQLite json_each)."""
    return text(
        f"EXISTS (SELECT 1 FROM json_each({column}) AS je WHERE je.value = :{param})"
    ).bindparams(**{param: value})


async def list_courses(
    db: AsyncSession,
    user_id: int,
    *,
    q: str | None = None,
    channel: str | None = None,
    category: str | None = None,
    difficulty: str | None = None,
    level: str | None = None,
    topic: str | None = None,
    skill: str | None = None,
    format: str | None = None,
    favorite: bool | None = None,
    sort: str = "recent",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    stmt = select(EnglishCourse)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                EnglishCourse.title.ilike(like),
                EnglishCourse.channel.ilike(like),
                EnglishCourse.description.ilike(like),
            )
        )
    if channel:
        stmt = stmt.where(EnglishCourse.channel == channel)
    if category:
        stmt = stmt.where(EnglishCourse.category == category)
    if difficulty:
        stmt = stmt.where(EnglishCourse.difficulty == difficulty)
    if level:
        stmt = stmt.where(EnglishCourse.level == level)
    if format:
        stmt = stmt.where(EnglishCourse.format == format)
    # topics/skills are JSON arrays. A LIKE on the raw column cannot work:
    # SQLAlchemy serialises JSON with ensure_ascii=True, so the column holds
    # "\u65e5\u5e38" instead of "日常". json_each decodes it properly and
    # compares exact labels (so "词汇" never matches a longer label).
    if topic:
        stmt = stmt.where(_json_has("courses.topics", "topic_value", topic))
    if skill:
        stmt = stmt.where(_json_has("courses.skills", "skill_value", skill))
    if favorite:
        stmt = stmt.where(
            EnglishCourse.id.in_(select(EnglishFavorite.course_id).where(EnglishFavorite.user_id == user_id))
        )

    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar() or 0

    if sort == "title":
        stmt = stmt.order_by(EnglishCourse.title.asc())
    elif sort == "lessons":
        stmt = stmt.order_by(EnglishCourse.lesson_count.desc(), EnglishCourse.title.asc())
    else:
        stmt = stmt.order_by(EnglishCourse.updated_at.desc(), EnglishCourse.id.desc())

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    rows = (
        await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()

    favorites = await favorite_course_ids(db, user_id)
    return {
        "items": [await serialize_course(c, favorite=c.id in favorites) for c in rows],
        "total": int(total),
        "page": page,
        "page_size": page_size,
    }


async def list_lessons(
    db: AsyncSession,
    user_id: int,
    *,
    q: str | None = None,
    course_id: int | None = None,
    level: str | None = None,
    topic: str | None = None,
    skill: str | None = None,
    format: str | None = None,
    sort: str = "recent",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Flat lesson list, filterable by the per-file classification.

    Course-level filters answer "which series should I start"; this answers
    "give me something at my level right now", across every course.
    """
    stmt = (
        select(EnglishLesson, EnglishCourse)
        .join(EnglishCourse, EnglishCourse.id == EnglishLesson.course_id)
        .where(EnglishLesson.status == "ready")
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(EnglishLesson.title.ilike(like), EnglishCourse.title.ilike(like))
        )
    if course_id is not None:
        stmt = stmt.where(EnglishLesson.course_id == course_id)
    if level:
        stmt = stmt.where(EnglishLesson.level == level)
    if format:
        stmt = stmt.where(EnglishLesson.format == format)
    if topic:
        stmt = stmt.where(_json_has("lessons.topics", "topic_value", topic))
    if skill:
        stmt = stmt.where(_json_has("lessons.skills", "skill_value", skill))

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0

    if sort == "title":
        stmt = stmt.order_by(EnglishLesson.title.asc())
    elif sort == "duration":
        stmt = stmt.order_by(EnglishLesson.duration.desc())
    else:
        stmt = stmt.order_by(EnglishLesson.id.desc())

    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    rows = (
        await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    ).all()

    progress_rows = (
        await db.execute(
            select(EnglishProgress).where(EnglishProgress.user_id == user_id)
        )
    ).scalars().all()
    progress = {p.lesson_id: p for p in progress_rows}

    return {
        "items": [
            {
                "id": lesson.id,
                "course_id": lesson.course_id,
                "course_title": course.title,
                "channel": course.channel,
                "title": lesson.title,
                "position": lesson.position,
                "duration": lesson.duration,
                "media_type": lesson.media_type,
                "level": lesson.level or "B1",
                "topics": list(lesson.topics or []),
                "skills": list(lesson.skills or []),
                "format": lesson.format or "monologue",
                "progress": _progress_ratio(
                    progress[lesson.id].position if lesson.id in progress else 0.0,
                    lesson.duration,
                ),
                "completed": bool(progress[lesson.id].completed)
                if lesson.id in progress
                else False,
            }
            for lesson, course in rows
        ],
        "total": int(total),
        "page": page,
        "page_size": page_size,
    }


async def course_filters(db: AsyncSession) -> dict[str, list[str]]:
    """Everything the UI can filter by, in display order.

    Levels are emitted in CEFR order rather than alphabetically, and topics /
    skills are de-duplicated across the JSON arrays with a stable ordering so
    the filter bar does not jump around between loads.
    """
    from app.services.english.taxonomy import FORMAT_LABEL, LEVELS

    channels = (
        await db.execute(select(EnglishCourse.channel).distinct().order_by(EnglishCourse.channel))
    ).scalars().all()
    categories = (
        await db.execute(select(EnglishCourse.category).distinct().order_by(EnglishCourse.category))
    ).scalars().all()
    difficulties = (
        await db.execute(
            select(EnglishCourse.difficulty).distinct().order_by(EnglishCourse.difficulty)
        )
    ).scalars().all()
    levels = (
        await db.execute(select(EnglishCourse.level).distinct())
    ).scalars().all()
    formats = (
        await db.execute(select(EnglishCourse.format).distinct())
    ).scalars().all()
    rows = (
        await db.execute(select(EnglishCourse.topics, EnglishCourse.skills))
    ).all()

    topic_counts: dict[str, int] = {}
    skill_counts: dict[str, int] = {}
    for topics, skills in rows:
        for label in topics or []:
            topic_counts[str(label)] = topic_counts.get(str(label), 0) + 1
        for label in skills or []:
            skill_counts[str(label)] = skill_counts.get(str(label), 0) + 1

    found_levels = {lv for lv in levels if lv}
    return {
        "channels": [c for c in channels if c],
        "categories": [c for c in categories if c],
        "difficulties": [d for d in difficulties if d],
        "levels": [lv for lv in LEVELS if lv in found_levels],
        # most common first, so the useful filters are within reach
        "topics": sorted(topic_counts, key=lambda k: (-topic_counts[k], k)),
        "skills": sorted(skill_counts, key=lambda k: (-skill_counts[k], k)),
        "formats": [f for f in formats if f],
        "format_labels": FORMAT_LABEL,
        "lesson_filters": await lesson_filters(db),
    }


async def lesson_filters(db: AsyncSession) -> dict[str, list[str]]:
    """Filter options computed from the lesson rows themselves.

    A course may sit at B1 while a few of its episodes are A2, so the options
    for a per-file filter have to come from the files, not from the courses.
    """
    from app.services.english.taxonomy import FORMAT_LABEL, LEVELS

    levels = (await db.execute(select(EnglishLesson.level).distinct())).scalars().all()
    formats = (await db.execute(select(EnglishLesson.format).distinct())).scalars().all()
    rows = (
        await db.execute(
            select(EnglishLesson.topics, EnglishLesson.skills).where(
                EnglishLesson.status == "ready"
            )
        )
    ).all()

    topic_counts: dict[str, int] = {}
    skill_counts: dict[str, int] = {}
    for topics, skills in rows:
        for label in topics or []:
            topic_counts[str(label)] = topic_counts.get(str(label), 0) + 1
        for label in skills or []:
            skill_counts[str(label)] = skill_counts.get(str(label), 0) + 1

    found = {lv for lv in levels if lv}
    return {
        "levels": [lv for lv in LEVELS if lv in found],
        "topics": sorted(topic_counts, key=lambda k: (-topic_counts[k], k)),
        "skills": sorted(skill_counts, key=lambda k: (-skill_counts[k], k)),
        "formats": [f for f in formats if f],
        "format_labels": FORMAT_LABEL,
    }


async def course_detail(
    db: AsyncSession, course_id: int, user_id: int
) -> dict[str, Any] | None:
    course = await db.get(EnglishCourse, course_id)
    if course is None:
        return None

    favorites = await favorite_course_ids(db, user_id)
    payload = await serialize_course(course, favorite=course_id in favorites)

    lessons = (
        await db.execute(
            select(EnglishLesson)
            .where(EnglishLesson.course_id == course_id, EnglishLesson.status == "ready")
            .order_by(EnglishLesson.position.asc(), EnglishLesson.id.asc())
        )
    ).scalars().all()

    progress_rows = (
        await db.execute(select(EnglishProgress).where(EnglishProgress.user_id == user_id))
    ).scalars().all()
    progress = {p.lesson_id: p for p in progress_rows}

    transcript_ids = {
        r[0]
        for r in (
            await db.execute(select(EnglishTranscript.lesson_id))
        ).all()
    }

    payload["lessons"] = [
        {
            "id": lesson.id,
            "title": lesson.title,
            "position": lesson.position,
            "duration": lesson.duration,
            "media_type": lesson.media_type,
            "media_ext": lesson.media_ext,
            "has_subtitle": lesson.id in transcript_ids,
            "cover": lesson_cover_url(lesson),
            # per-lesson classification (this file, not the course)
            "level": lesson.level or "B1",
            "topics": list(lesson.topics or []),
            "skills": list(lesson.skills or []),
            "format": lesson.format or "monologue",
            "progress": _progress_ratio(
                progress[lesson.id].position if lesson.id in progress else 0.0, lesson.duration
            ),
            "completed": bool(progress[lesson.id].completed) if lesson.id in progress else False,
        }
        for lesson in lessons
    ]
    payload["vocabulary_count"] = (
        await db.execute(
            select(func.count())
            .select_from(EnglishVocabulary)
            .where(EnglishVocabulary.course_id == course_id)
        )
    ).scalar() or 0
    return payload


# ── Lessons / transcripts / vocabulary ───────────────────────────────────


async def lesson_detail(
    db: AsyncSession, lesson_id: int, user_id: int
) -> dict[str, Any] | None:
    lesson = await db.get(EnglishLesson, lesson_id)
    if lesson is None:
        return None

    transcript = (
        await db.execute(select(EnglishTranscript).where(EnglishTranscript.lesson_id == lesson_id))
    ).scalar_one_or_none()
    progress = (
        await db.execute(
            select(EnglishProgress).where(
                EnglishProgress.user_id == user_id, EnglishProgress.lesson_id == lesson_id
            )
        )
    ).scalar_one_or_none()
    bookmarks = (
        await db.execute(
            select(EnglishBookmark)
            .where(
                EnglishBookmark.user_id == user_id, EnglishBookmark.lesson_id == lesson_id
            )
            .order_by(EnglishBookmark.position.asc())
        )
    ).scalars().all()

    vocab_rows = (
        await db.execute(
            select(EnglishVocabulary)
            .where(EnglishVocabulary.course_id == lesson.course_id)
            .order_by(EnglishVocabulary.frequency.desc())
            .limit(40)
        )
    ).scalars().all()

    neighbours = (
        await db.execute(
            select(EnglishLesson)
            .where(
                EnglishLesson.course_id == lesson.course_id,
                EnglishLesson.status == "ready",
                EnglishLesson.position > lesson.position,
            )
            .order_by(EnglishLesson.position.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    previous = (
        await db.execute(
            select(EnglishLesson)
            .where(
                EnglishLesson.course_id == lesson.course_id,
                EnglishLesson.status == "ready",
                EnglishLesson.position < lesson.position,
            )
            .order_by(EnglishLesson.position.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    return {
        "id": lesson.id,
        "course_id": lesson.course_id,
        "title": lesson.title,
        "media_type": lesson.media_type,
        "media_ext": lesson.media_ext,
        "duration": lesson.duration,
        "size_bytes": lesson.size_bytes,
        # classification of this file (see taxonomy.classify_lesson)
        "level": lesson.level or "B1",
        "topics": list(lesson.topics or []),
        "skills": list(lesson.skills or []),
        "format": lesson.format or "monologue",
        "features": lesson.features or {},
        "stream_url": media_url(lesson),
        "subtitle_url": subtitle_url(lesson),
        "has_subtitle": transcript is not None and bool(transcript.segment_count),
        "segment_count": transcript.segment_count if transcript else 0,
        "cover": lesson_cover_url(lesson) or f"/api/stream/cover/{lesson.course_id}",
        "vocabulary": [
            {
                "id": v.id,
                "word": v.word,
                "kind": v.kind,
                "frequency": v.frequency,
                "difficulty": v.difficulty,
                "meaning": v.meaning,
            }
            for v in vocab_rows
        ],
        "bookmarks": [
            {
                "id": b.id,
                "position": b.position,
                "at_seconds": b.at_seconds,
                "note": b.note,
                "text": b.text,
            }
            for b in bookmarks
        ],
        "progress": {
            "position": progress.position if progress else 0.0,
            "completed": bool(progress.completed) if progress else False,
            "listen_seconds": progress.listen_seconds if progress else 0,
            "repeat_target": progress.repeat_target if progress else 1,
            "completed_plays": progress.completed_plays if progress else 0,
        },
        "prev_lesson_id": previous.id if previous else None,
        "next_lesson_id": neighbours.id if neighbours else None,
    }


async def transcript_payload(db: AsyncSession, lesson_id: int) -> dict[str, Any] | None:
    lesson = await db.get(EnglishLesson, lesson_id)
    if lesson is None:
        return None
    transcript = (
        await db.execute(select(EnglishTranscript).where(EnglishTranscript.lesson_id == lesson_id))
    ).scalar_one_or_none()
    if transcript is None:
        return {"lesson_id": lesson_id, "language": "en", "segment_count": 0, "segments": []}
    return {
        "lesson_id": lesson_id,
        "language": transcript.language,
        "segment_count": transcript.segment_count,
        "segments": transcript.segments or [],
    }


async def vocabulary_payload(
    db: AsyncSession, course_id: int, *, limit: int = 300
) -> dict[str, Any] | None:
    course = await db.get(EnglishCourse, course_id)
    if course is None:
        return None
    rows = (
        await db.execute(
            select(EnglishVocabulary)
            .where(EnglishVocabulary.course_id == course_id)
            .order_by(EnglishVocabulary.frequency.desc(), EnglishVocabulary.word.asc())
            .limit(limit)
        )
    ).scalars().all()
    return {
        "course_id": course_id,
        "course_title": course.title,
        "items": [
            {
                "id": v.id,
                "word": v.word,
                "kind": v.kind,
                "frequency": v.frequency,
                "difficulty": v.difficulty,
                "meaning": v.meaning,
                "example": v.example,
            }
            for v in rows
        ],
    }


# ── Write paths ──────────────────────────────────────────────────────────


async def upsert_progress(
    db: AsyncSession,
    user_id: int,
    *,
    lesson_id: int,
    position: float,
    listen_seconds: int = 0,
    completed: bool | None = None,
    repeat_target: int | None = None,
) -> dict[str, Any] | None:
    # Reported every 10s while a scan may be committing: take the write lock
    # first, otherwise the read-then-write snapshot upgrade fails instantly.
    await begin_write(db)
    lesson = await db.get(EnglishLesson, lesson_id)
    if lesson is None:
        return None

    row = (
        await db.execute(
            select(EnglishProgress).where(
                EnglishProgress.user_id == user_id, EnglishProgress.lesson_id == lesson_id
            )
        )
    ).scalar_one_or_none()

    if row is None:
        row = EnglishProgress(
            user_id=user_id,
            lesson_id=lesson_id,
            position=0.0,
            repeat_target=1,
            completed_plays=0,
        )
        db.add(row)

    was_completed = bool(row.completed)
    target_changed = repeat_target is not None and repeat_target != row.repeat_target
    if repeat_target is not None:
        row.repeat_target = max(1, min(20, int(repeat_target)))
    row.position = max(0.0, float(position or 0))
    row.listen_seconds = (row.listen_seconds or 0) + max(0, int(listen_seconds or 0))
    row.last_played_at = _now()
    if completed is True:
        if row.completed_plays < row.repeat_target:
            row.completed_plays = (row.completed_plays or 0) + 1
        row.completed = row.completed_plays >= row.repeat_target
    elif completed is False:
        row.completed = False
    elif target_changed:
        row.completed = row.completed_plays >= row.repeat_target
    elif row.repeat_target <= 1:
        row.completed = was_completed or (
            lesson.duration > 0 and row.position >= lesson.duration * 0.95
        )
        if row.completed:
            row.completed_plays = max(row.completed_plays or 0, 1)

    # Count a completion only on the transition, never on every heartbeat
    newly_completed = 1 if (row.completed and not was_completed) else 0
    await _bump_daily_stat(
        db,
        user_id,
        listen_seconds=max(0, int(listen_seconds or 0)),
        lessons_completed=newly_completed,
    )
    await db.commit()
    return {
        "lesson_id": lesson_id,
        "position": row.position,
        "completed": bool(row.completed),
        "listen_seconds": row.listen_seconds,
        "repeat_target": row.repeat_target,
        "completed_plays": row.completed_plays,
    }


async def upsert_bookmark(
    db: AsyncSession,
    user_id: int,
    *,
    lesson_id: int,
    position: int,
    at_seconds: float = 0.0,
    note: str | None = None,
    text: str | None = None,
    remove: bool = False,
) -> dict[str, Any] | None:
    # Same reason as upsert_progress: read-then-write needs the lock up front.
    await begin_write(db)
    lesson = await db.get(EnglishLesson, lesson_id)
    if lesson is None:
        return None

    row = (
        await db.execute(
            select(EnglishBookmark).where(
                EnglishBookmark.user_id == user_id,
                EnglishBookmark.lesson_id == lesson_id,
                EnglishBookmark.position == position,
            )
        )
    ).scalar_one_or_none()

    if remove:
        if row is not None:
            await db.delete(row)
            await db.commit()
        return {"lesson_id": lesson_id, "position": position, "removed": True}

    if row is None:
        row = EnglishBookmark(
            user_id=user_id, lesson_id=lesson_id, position=position, at_seconds=at_seconds
        )
        db.add(row)

    row.at_seconds = float(at_seconds or 0)
    if note is not None:
        row.note = note
    if text is not None:
        row.text = text

    await db.commit()
    await db.refresh(row)
    return {
        "id": row.id,
        "lesson_id": row.lesson_id,
        "position": row.position,
        "at_seconds": row.at_seconds,
        "note": row.note,
        "text": row.text,
    }


async def set_favorite(
    db: AsyncSession, user_id: int, *, course_id: int, favorite: bool
) -> dict[str, Any] | None:
    # Same reason as upsert_progress: read-then-write needs the lock up front.
    await begin_write(db)
    course = await db.get(EnglishCourse, course_id)
    if course is None:
        return None

    row = (
        await db.execute(
            select(EnglishFavorite).where(
                EnglishFavorite.user_id == user_id, EnglishFavorite.course_id == course_id
            )
        )
    ).scalar_one_or_none()

    if favorite and row is None:
        db.add(EnglishFavorite(user_id=user_id, course_id=course_id))
    elif not favorite and row is not None:
        await db.delete(row)

    await db.commit()
    return {"course_id": course_id, "favorite": bool(favorite)}


async def _bump_daily_stat(
    db: AsyncSession, user_id: int, *, listen_seconds: int = 0, lessons_completed: int = 0
) -> None:
    if not listen_seconds and not lessons_completed:
        return
    day = date.today().isoformat()
    row = (
        await db.execute(
            select(EnglishDailyStat).where(
                EnglishDailyStat.user_id == user_id, EnglishDailyStat.day == day
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = EnglishDailyStat(user_id=user_id, day=day, listen_seconds=0, lessons_completed=0)
        db.add(row)
    row.listen_seconds = (row.listen_seconds or 0) + listen_seconds
    row.lessons_completed = (row.lessons_completed or 0) + lessons_completed


# ── Home ─────────────────────────────────────────────────────────────────


def _greeting() -> str:
    hour = datetime.now().hour
    if hour < 6:
        return "凌晨好"
    if hour < 12:
        return "早上好"
    if hour < 14:
        return "中午好"
    if hour < 19:
        return "下午好"
    return "晚上好"


def _fmt_duration(seconds: int | float | None) -> str:
    total = int(seconds or 0)
    hours, rest = divmod(total, 3600)
    minutes = rest // 60
    if hours:
        return f"{hours} 小时 {minutes} 分"
    return f"{minutes} 分钟"


async def home_payload(db: AsyncSession, user: User) -> dict[str, Any]:
    user_id = user.id
    today = date.today().isoformat()

    courses = (
        await db.execute(select(func.count()).select_from(EnglishCourse))
    ).scalar() or 0
    lessons = (
        await db.execute(
            select(func.count()).select_from(EnglishLesson).where(EnglishLesson.status == "ready")
        )
    ).scalar() or 0
    completed = (
        await db.execute(
            select(func.count())
            .select_from(EnglishProgress)
            .where(EnglishProgress.user_id == user_id, EnglishProgress.completed.is_(True))
        )
    ).scalar() or 0
    today_row = (
        await db.execute(
            select(EnglishDailyStat).where(
                EnglishDailyStat.user_id == user_id, EnglishDailyStat.day == today
            )
        )
    ).scalar_one_or_none()

    # Continue learning: unfinished progress, newest first
    rows = (
        await db.execute(
            select(EnglishProgress, EnglishLesson, EnglishCourse)
            .join(EnglishLesson, EnglishLesson.id == EnglishProgress.lesson_id)
            .join(EnglishCourse, EnglishCourse.id == EnglishLesson.course_id)
            .where(EnglishProgress.user_id == user_id, EnglishProgress.completed.is_(False))
            .order_by(EnglishProgress.last_played_at.desc().nulls_last())
            .limit(6)
        )
    ).all()

    completed_rows = (
        await db.execute(
            select(EnglishProgress, EnglishLesson, EnglishCourse)
            .join(EnglishLesson, EnglishLesson.id == EnglishProgress.lesson_id)
            .join(EnglishCourse, EnglishCourse.id == EnglishLesson.course_id)
            .where(EnglishProgress.user_id == user_id, EnglishProgress.completed.is_(True))
            .order_by(EnglishProgress.last_played_at.desc().nulls_last())
            .limit(8)
        )
    ).all()

    continue_learning = [
        {
            "lesson_id": lesson.id,
            "lesson_title": lesson.title,
            "course_id": course.id,
            "course_title": course.title,
            "channel": course.channel,
            "cover": cover_url(course),
            "duration": lesson.duration,
            "position": round(float(progress.position or 0), 1),
            "progress": _progress_ratio(progress.position, lesson.duration),
            "completed_plays": progress.completed_plays or 0,
            "repeat_target": progress.repeat_target or 1,
        }
        for progress, lesson, course in rows
    ]

    recent_completed = [
        {
            "lesson_id": lesson.id,
            "lesson_title": lesson.title,
            "course_id": course.id,
            "course_title": course.title,
            "cover": cover_url(course),
            "duration": lesson.duration,
            "completed_plays": progress.completed_plays or 0,
            "repeat_target": progress.repeat_target or 1,
            "last_played_at": progress.last_played_at,
        }
        for progress, lesson, course in completed_rows
    ]

    recent = (
        await db.execute(
            select(EnglishCourse).order_by(EnglishCourse.updated_at.desc()).limit(8)
        )
    ).scalars().all()

    favorites = await favorite_course_ids(db, user_id)
    recommended_pool = (
        await db.execute(
            select(EnglishCourse).order_by(
                EnglishCourse.lesson_count.desc(), EnglishCourse.updated_at.desc()
            ).limit(8)
        )
    ).scalars().all()

    return {
        "greeting": _greeting(),
        "user": {"id": user.id, "name": user.display_name or user.username},
        "stats": {
            "courses": int(courses),
            "lessons": int(lessons),
            "completed": int(completed),
            "minutes_today": int((today_row.listen_seconds or 0) // 60) if today_row else 0,
            "total_time": _fmt_duration(
                (
                    await db.execute(
                        select(func.coalesce(func.sum(EnglishProgress.listen_seconds), 0)).where(
                            EnglishProgress.user_id == user_id
                        )
                    )
                ).scalar()
                or 0
            ),
        },
        "continue_learning": continue_learning,
        "recent_completed": recent_completed,
        "recent": [await serialize_course(c, favorite=c.id in favorites) for c in recent],
        "recommended": [
            await serialize_course(c, favorite=c.id in favorites) for c in recommended_pool
        ],
    }


# ── AI tutor ─────────────────────────────────────────────────────────────


async def transcript_context(db: AsyncSession, lesson_id: int, *, max_chars: int = 6000) -> str:
    transcript = (
        await db.execute(select(EnglishTranscript).where(EnglishTranscript.lesson_id == lesson_id))
    ).scalar_one_or_none()
    if transcript is None or not transcript.search_text:
        return ""
    return transcript.search_text[:max_chars]


async def lesson_context(db: AsyncSession, lesson_id: int) -> tuple[EnglishLesson | None, str]:
    lesson = await db.get(EnglishLesson, lesson_id)
    if lesson is None:
        return None, ""
    text = await transcript_context(db, lesson_id)
    return lesson, text


def segment_at(segments: Sequence[dict], position: int) -> dict | None:
    for seg in segments or []:
        if int(seg.get("i", -1)) == int(position):
            return seg
    return None


__all__ = [
    "course_detail",
    "course_filters",
    "cover_url",
    "favorite_course_ids",
    "home_payload",
    "lesson_context",
    "lesson_detail",
    "list_courses",
    "media_url",
    "segment_at",
    "serialize_course",
    "set_favorite",
    "subtitle_url",
    "transcript_context",
    "transcript_payload",
    "upsert_bookmark",
    "upsert_progress",
    "vocabulary_payload",
]


# ── Learning statistics ──────────────────────────────────────────────────
#
# The daily aggregate already exists (``learning_statistics``, written by
# ``upsert_progress``), so this is pure read-side work: the app needs a
# heat-map, a streak and per-level distribution, none of which the home
# payload exposes.


def _streak(days_with_activity: set[str], today: date) -> int:
    """Consecutive days of study ending today (or yesterday, gracefully)."""
    if not days_with_activity:
        return 0
    cursor = today
    if today.isoformat() not in days_with_activity:
        # Nothing yet today is normal at 08:00; keep the streak alive if the
        # user studied yesterday.
        cursor = today - timedelta(days=1)
        if cursor.isoformat() not in days_with_activity:
            return 0
    count = 0
    while cursor.isoformat() in days_with_activity:
        count += 1
        cursor -= timedelta(days=1)
    return count


async def stats_payload(db: AsyncSession, user_id: int, *, days: int = 90) -> dict[str, Any]:
    """Learning report: streak, daily series, heat-map, totals, level mix."""
    days = max(7, min(int(days or 90), 730))
    today = date.today()
    first_day = (today - timedelta(days=days - 1)).isoformat()

    rows = (
        await db.execute(
            select(EnglishDailyStat.day, EnglishDailyStat.listen_seconds, EnglishDailyStat.lessons_completed)
            .where(EnglishDailyStat.user_id == user_id, EnglishDailyStat.day >= first_day)
            .order_by(EnglishDailyStat.day.asc())
        )
    ).all()
    by_day = {str(day): int(seconds or 0) for day, seconds, _done in rows}
    completed_by_day = {str(day): int(done or 0) for day, _seconds, done in rows}

    active_days = {d for d, s in by_day.items() if s > 0}

    # Fill gaps so the client never has to reason about missing dates.
    series: list[dict[str, Any]] = []
    for offset in range(days):
        day = (today - timedelta(days=days - 1 - offset)).isoformat()
        series.append(
            {
                "day": day,
                "seconds": by_day.get(day, 0),
                "minutes": round(by_day.get(day, 0) / 60),
                "lessons_completed": completed_by_day.get(day, 0),
            }
        )

    week = series[-7:]
    month = series[-30:]

    # Totals come from the progress table, not the daily rollup, so a lesson
    # finished before the rollup existed still counts.
    completed_lessons = (
        await db.execute(
            select(func.count())
            .select_from(EnglishProgress)
            .where(EnglishProgress.user_id == user_id, EnglishProgress.completed.is_(True))
        )
    ).scalar() or 0
    total_seconds = (
        await db.execute(
            select(func.coalesce(func.sum(EnglishProgress.listen_seconds), 0)).where(
                EnglishProgress.user_id == user_id
            )
        )
    ).scalar() or 0
    started_lessons = (
        await db.execute(
            select(func.count()).select_from(EnglishProgress).where(EnglishProgress.user_id == user_id)
        )
    ).scalar() or 0
    favorites = (
        await db.execute(
            select(func.count()).select_from(EnglishFavorite).where(EnglishFavorite.user_id == user_id)
        )
    ).scalar() or 0
    bookmarks = (
        await db.execute(
            select(func.count()).select_from(EnglishBookmark).where(EnglishBookmark.user_id == user_id)
        )
    ).scalar() or 0

    # Level mix of what the user has *completed* — this is the "am I improving"
    # signal, so it must follow the lesson's own classification, not the course.
    level_rows = (
        await db.execute(
            select(EnglishLesson.level, func.count())
            .join(EnglishProgress, EnglishProgress.lesson_id == EnglishLesson.id)
            .where(EnglishProgress.user_id == user_id, EnglishProgress.completed.is_(True))
            .group_by(EnglishLesson.level)
        )
    ).all()
    levels = {str(level or "B1"): int(count or 0) for level, count in level_rows}

    finished_at = await db.execute(
        select(func.max(EnglishProgress.last_played_at)).where(EnglishProgress.user_id == user_id)
    )
    last_played = finished_at.scalar()

    return {
        "days": days,
        "streak": _streak(active_days, today),
        "today": {
            "day": today.isoformat(),
            "seconds": by_day.get(today.isoformat(), 0),
            "minutes": round(by_day.get(today.isoformat(), 0) / 60),
        },
        "series": series,
        "week": {
            "seconds": sum(item["seconds"] for item in week),
            "minutes": sum(item["minutes"] for item in week),
            "days_active": sum(1 for item in week if item["seconds"] > 0),
        },
        "month": {
            "seconds": sum(item["seconds"] for item in month),
            "minutes": sum(item["minutes"] for item in month),
            "days_active": sum(1 for item in month if item["seconds"] > 0),
        },
        "totals": {
            "lessons_started": int(started_lessons),
            "lessons_completed": int(completed_lessons),
            "listen_seconds": int(total_seconds),
            "listen_minutes": round(int(total_seconds) / 60),
            "favorites": int(favorites),
            "bookmarks": int(bookmarks),
            "active_days": len(active_days),
        },
        "levels": levels,
        "last_played_at": last_played.isoformat() if last_played else None,
    }
