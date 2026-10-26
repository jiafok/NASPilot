"""Read-side aggregation for the English Learning Open API.

Keeping the query/serialization logic here means `api/v1/english.py` stays a
thin HTTP layer, and the same payloads can later be reused by a scheduled
"send me my weekly summary" job without duplicating SQL.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
        "description": course.description,
        "tags": list(course.tags or []),
        "lesson_count": course.lesson_count,
        "total_seconds": course.total_seconds,
        "source": course.source,
        "cover": cover_url(course),
        "is_favorite": favorite,
        "updated_at": course.updated_at.isoformat() if course.updated_at else None,
    }


async def list_courses(
    db: AsyncSession,
    user_id: int,
    *,
    q: str | None = None,
    channel: str | None = None,
    category: str | None = None,
    difficulty: str | None = None,
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


async def course_filters(db: AsyncSession) -> dict[str, list[str]]:
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
    return {
        "channels": [c for c in channels if c],
        "categories": [c for c in categories if c],
        "difficulties": [d for d in difficulties if d],
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
            "has_cover": bool(lesson.cover_path),
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
        "stream_url": media_url(lesson),
        "subtitle_url": subtitle_url(lesson),
        "has_subtitle": transcript is not None and bool(transcript.segment_count),
        "segment_count": transcript.segment_count if transcript else 0,
        "cover": f"/api/stream/cover/{lesson.course_id}",
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
) -> dict[str, Any] | None:
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
        row = EnglishProgress(user_id=user_id, lesson_id=lesson_id, position=0.0)
        db.add(row)

    was_completed = bool(row.completed)
    row.position = max(0.0, float(position or 0))
    row.listen_seconds = (row.listen_seconds or 0) + max(0, int(listen_seconds or 0))
    row.last_played_at = _now()
    if completed is None:
        row.completed = was_completed or (
            lesson.duration > 0 and row.position >= lesson.duration * 0.95
        )
    else:
        row.completed = bool(completed)

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
        }
        for progress, lesson, course in rows
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
