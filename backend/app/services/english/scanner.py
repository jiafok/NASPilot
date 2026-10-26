"""Resource Scanner — filesystem → courses / lessons / transcripts / vocabulary.

Idempotent by design: `lessons.file_path` is unique, so re-scanning only
updates metadata. Files that disappeared are flagged ``missing`` instead of
being deleted, so a user's progress and bookmarks are never lost.

Directory → hierarchy rules are documented in
`docs/english-learning-center/API.md` (Part A, Resource Import Specification).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    EnglishCourse,
    EnglishLesson,
    EnglishTranscript,
    EnglishVocabulary,
)
from app.services.english import metadata as meta
from app.services.english import subtitles
from app.services.english import vocabulary as vocab

logger = logging.getLogger("naspilot.english.scanner")

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

DEFAULT_CHANNEL = "默认"
DEFAULT_COURSE = "未分类"


@dataclass
class Asset:
    """All files that belong to one lesson (same basename, same directory)."""

    directory: Path
    stem: str
    media: Path | None = None
    subtitle: Path | None = None
    cover: Path | None = None


@dataclass
class ScanReport:
    root: str = ""
    exists: bool = False
    courses_created: int = 0
    courses_updated: int = 0
    lessons_created: int = 0
    lessons_updated: int = 0
    lessons_missing: int = 0
    transcripts_parsed: int = 0
    vocabulary_terms: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "exists": self.exists,
            "courses_created": self.courses_created,
            "courses_updated": self.courses_updated,
            "lessons_created": self.lessons_created,
            "lessons_updated": self.lessons_updated,
            "lessons_missing": self.lessons_missing,
            "transcripts_parsed": self.transcripts_parsed,
            "vocabulary_terms": self.vocabulary_terms,
            "skipped": self.skipped,
            "errors": self.errors[:20],
        }


def english_root() -> Path:
    return Path(settings.ENGLISH_ROOT)


def is_inside_root(path: Path, root: Path) -> bool:
    """True when ``path`` really lives under ``root`` (symlink-safe)."""
    try:
        real = Path(os.path.realpath(path))
        root_real = Path(os.path.realpath(root))
        return real == root_real or root_real in real.parents
    except OSError:
        return False


# ── Filesystem collection ────────────────────────────────────────────────


def collect_assets(root: Path) -> dict[Path, dict[str, Asset]]:
    """Walk ``root`` and group media/subtitle/cover files by directory+basename."""
    by_dir: dict[Path, dict[str, Asset]] = {}

    for current, dirnames, filenames in os.walk(root):
        # Skip hidden and temporary directories
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d not in {"@eaDir", "#recycle"}
        )
        directory = Path(current)
        bucket = by_dir.setdefault(directory, {})

        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = directory / name
            ext = path.suffix.lower()
            stem = path.stem

            if ext in MEDIA_EXTS:
                asset = bucket.setdefault(stem, Asset(directory=directory, stem=stem))
                # Prefer video over a derived audio file with the same name
                if asset.media is None or (
                    ext in VIDEO_EXTS and asset.media.suffix.lower() not in VIDEO_EXTS
                ):
                    asset.media = path
            elif ext in subtitles.SUBTITLE_EXTS:
                asset = bucket.setdefault(stem, Asset(directory=directory, stem=stem))
                # .srt wins over .vtt
                if asset.subtitle is None or ext == ".srt":
                    asset.subtitle = path
            elif ext in IMAGE_EXTS:
                asset = bucket.setdefault(stem, Asset(directory=directory, stem=stem))
                if asset.cover is None or ext == ".jpg":
                    asset.cover = path

    # Drop buckets with no media file (image/subtitle-only directories)
    for directory in list(by_dir):
        cleaned = {k: v for k, v in by_dir[directory].items() if v.media is not None}
        if cleaned:
            by_dir[directory] = cleaned
        else:
            del by_dir[directory]
    return by_dir


def _course_identity(root: Path, directory: Path) -> tuple[str, str, str]:
    """Map a directory to ``(channel, course_name, extra_prefix)``."""
    try:
        rel = directory.relative_to(root)
    except ValueError:
        return DEFAULT_CHANNEL, DEFAULT_COURSE, ""

    parts = [p for p in rel.parts if p not in ("", ".")]
    if not parts:
        return DEFAULT_CHANNEL, DEFAULT_COURSE, ""
    if len(parts) == 1:
        return parts[0], parts[0], ""
    return parts[0], parts[1], " ".join(parts[2:])


def _pick_course_cover(directory: Path, assets: dict[str, Asset]) -> Path | None:
    for asset in assets.values():
        if asset.cover is not None:
            return asset.cover
    for name in ("cover.jpg", "cover.png", "cover.webp", "folder.jpg", "poster.jpg"):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


# ── Database sync ────────────────────────────────────────────────────────


async def _get_or_create_course(
    db: AsyncSession,
    *,
    channel: str,
    title: str,
    directory: Path,
    report: ScanReport,
    override: dict,
) -> EnglishCourse:
    slug = meta.slugify(title)
    existing = (
        await db.execute(
            select(EnglishCourse).where(
                EnglishCourse.channel == channel, EnglishCourse.slug == slug
            )
        )
    ).scalar_one_or_none()

    category, difficulty = meta.classify([channel, title])
    category = str(override.get("category") or category)
    difficulty = str(override.get("difficulty") or difficulty)
    final_title = str(override.get("title") or title)

    if existing is None:
        course = EnglishCourse(
            channel=channel,
            title=final_title,
            slug=slug,
            category=category,
            difficulty=difficulty,
            description=override.get("description"),
            directory=str(directory),
            source=override.get("source") or meta.guess_source([channel, title]),
            tags=list(override.get("tags") or []),
        )
        db.add(course)
        await db.flush()
        report.courses_created += 1
        return course

    existing.title = final_title
    existing.category = category
    existing.difficulty = difficulty
    existing.directory = str(directory)
    if override.get("description"):
        existing.description = str(override["description"])
    if override.get("source"):
        existing.source = str(override["source"])
    if override.get("tags"):
        existing.tags = list(override["tags"])
    report.courses_updated += 1
    return existing


async def _upsert_lesson(
    db: AsyncSession,
    *,
    course: EnglishCourse,
    asset: Asset,
    position: int,
    title_prefix: str,
    report: ScanReport,
) -> EnglishLesson:
    assert asset.media is not None
    file_path = str(asset.media)
    ext = asset.media.suffix.lower()
    media_type = "video" if ext in VIDEO_EXTS else "audio"

    _, clean = meta.lesson_position(asset.media.name)
    title = f"{title_prefix} {clean}".strip() if title_prefix else clean
    title = title[:255] or asset.stem[:255]

    segments = subtitles.parse_file(asset.subtitle) if asset.subtitle else []
    duration = subtitles.duration_ms(segments) // 1000

    try:
        size = asset.media.stat().st_size
    except OSError:
        size = 0

    existing = (
        await db.execute(select(EnglishLesson).where(EnglishLesson.file_path == file_path))
    ).scalar_one_or_none()

    if existing is None:
        lesson = EnglishLesson(
            course_id=course.id,
            title=title,
            file_path=file_path,
            media_type=media_type,
            media_ext=ext,
            subtitle_path=str(asset.subtitle) if asset.subtitle else None,
            subtitle_format=asset.subtitle.suffix.lstrip(".").lower() if asset.subtitle else None,
            cover_path=str(asset.cover) if asset.cover else None,
            duration=duration,
            size_bytes=size,
            position=position,
            status="ready",
            published_at=_mtime(asset.media),
        )
        db.add(lesson)
        await db.flush()
        report.lessons_created += 1
    else:
        lesson = existing
        lesson.course_id = course.id
        lesson.title = title
        lesson.media_type = media_type
        lesson.media_ext = ext
        lesson.subtitle_path = str(asset.subtitle) if asset.subtitle else None
        lesson.subtitle_format = (
            asset.subtitle.suffix.lstrip(".").lower() if asset.subtitle else None
        )
        lesson.cover_path = str(asset.cover) if asset.cover else None
        lesson.duration = duration
        lesson.size_bytes = size
        lesson.position = position
        lesson.status = "ready"
        lesson.published_at = _mtime(asset.media)
        report.lessons_updated += 1

    await _upsert_transcript(db, lesson=lesson, segments=segments, report=report)
    return lesson


async def _upsert_transcript(
    db: AsyncSession,
    *,
    lesson: EnglishLesson,
    segments: list[subtitles.Segment],
    report: ScanReport,
) -> None:
    existing = (
        await db.execute(select(EnglishTranscript).where(EnglishTranscript.lesson_id == lesson.id))
    ).scalar_one_or_none()

    if not segments:
        if existing is not None:
            existing.segments = []
            existing.segment_count = 0
            existing.search_text = ""
        return

    payload = subtitles.to_json(segments)
    text = subtitles.search_text(segments)

    if existing is None:
        db.add(
            EnglishTranscript(
                lesson_id=lesson.id,
                language="en",
                segment_count=len(payload),
                segments=payload,
                search_text=text,
            )
        )
    else:
        existing.segment_count = len(payload)
        existing.segments = payload
        existing.search_text = text
    report.transcripts_parsed += 1


async def refresh_course_vocabulary(db: AsyncSession, course: EnglishCourse) -> int:
    """Rebuild the vocabulary table for one course from its transcripts."""
    rows = (
        await db.execute(
            select(EnglishTranscript.search_text)
            .join(EnglishLesson, EnglishLesson.id == EnglishTranscript.lesson_id)
            .where(EnglishLesson.course_id == course.id)
        )
    ).scalars().all()

    texts = [t for t in rows if t]
    result = vocab.extract_many(texts, min_word_freq=2) if texts else vocab.VocabularyResult()
    items = result.merged(limit=int(settings.ENGLISH_VOCAB_LIMIT or 300))

    # Replace this course's word list (meanings are re-attached by the AI job)
    old = (
        await db.execute(select(EnglishVocabulary).where(EnglishVocabulary.course_id == course.id))
    ).scalars().all()
    preserved = {row.word: (row.meaning, row.example) for row in old}
    for row in old:
        await db.delete(row)
    await db.flush()

    for item in items:
        word = str(item["word"])
        meaning, example = preserved.get(word, (None, None))
        db.add(
            EnglishVocabulary(
                course_id=course.id,
                word=word,
                kind=str(item["kind"]),
                frequency=int(item["frequency"]),
                difficulty=str(item["difficulty"]),
                meaning=meaning,
                example=example,
            )
        )
    return len(items)


async def scan(
    db: AsyncSession,
    *,
    root: Path | None = None,
    with_vocabulary: bool = True,
) -> dict[str, object]:
    """Scan the English library and synchronize the database."""
    base = root or english_root()
    report = ScanReport(root=str(base))

    if not base.is_dir():
        report.errors.append(f"目录不存在: {base}")
        logger.warning("English root missing: %s", base)
        return report.as_dict()

    report.exists = True
    by_dir = collect_assets(base)

    touched_courses: dict[int, EnglishCourse] = {}
    seen_files: set[str] = set()

    for directory, assets in sorted(by_dir.items(), key=lambda kv: str(kv[0])):
        channel, course_name, prefix = _course_identity(base, directory)
        override = meta.read_course_override(directory)
        course = await _get_or_create_course(
            db,
            channel=meta.clean_title(channel) or channel,
            title=meta.clean_title(course_name) or course_name,
            directory=directory,
            report=report,
            override=override,
        )
        touched_courses[course.id] = course
        if override.get("cover"):
            candidate = directory / str(override["cover"])
            if candidate.is_file():
                course.cover_path = str(candidate)

        # Order lessons by leading index, then by name
        ordered = sorted(
            assets.values(),
            key=lambda a: (*meta.lesson_position(a.media.name if a.media else a.stem),),
        )
        for index, asset in enumerate(ordered, start=1):
            if asset.media is None:
                continue
            seen_files.add(str(asset.media))
            try:
                await _upsert_lesson(
                    db,
                    course=course,
                    asset=asset,
                    position=index,
                    title_prefix=prefix,
                    report=report,
                )
            except Exception as exc:  # one bad file must not stop the scan
                report.errors.append(f"{asset.media.name}: {exc}")
                logger.warning("Scan failed for %s", asset.media, exc_info=True)
        await db.flush()

    # Anything on disk no longer present → missing (rows are kept)
    all_lessons = (await db.execute(select(EnglishLesson))).scalars().all()
    root_real = str(Path(os.path.realpath(base)))
    for lesson in all_lessons:
        in_root = lesson.file_path.startswith(root_real)
        if in_root and lesson.file_path not in seen_files and lesson.status != "missing":
            lesson.status = "missing"
            report.lessons_missing += 1

    # Update course aggregates + cover
    # (must run AFTER missing detection so counts exclude gone files)
    for course_id, course in touched_courses.items():
        lessons = (
            await db.execute(select(EnglishLesson).where(EnglishLesson.course_id == course_id))
        ).scalars().all()
        ready = [ls for ls in lessons if ls.status == "ready"]
        course.lesson_count = len(ready)
        course.total_seconds = sum(ls.duration or 0 for ls in ready)
        if not course.cover_path and course.directory:
            directory = Path(course.directory)
            if directory.is_dir():
                assets = by_dir.get(directory, {})
                cover = _pick_course_cover(directory, assets)
                if cover is not None:
                    course.cover_path = str(cover)

        if with_vocabulary and course.lesson_count:
            try:
                report.vocabulary_terms += await refresh_course_vocabulary(db, course)
            except Exception:
                logger.warning("Vocabulary refresh failed for course %s", course_id, exc_info=True)

    await db.commit()
    logger.info("English scan finished: %s", report.as_dict())
    return report.as_dict()


async def stats(db: AsyncSession) -> dict[str, object]:
    """Lightweight counts for the admin/statistics endpoint."""
    from sqlalchemy import func

    courses = (await db.execute(select(func.count()).select_from(EnglishCourse))).scalar() or 0
    lessons = (
        await db.execute(
            select(func.count()).select_from(EnglishLesson).where(EnglishLesson.status == "ready")
        )
    ).scalar() or 0
    missing = (
        await db.execute(
            select(func.count())
            .select_from(EnglishLesson)
            .where(EnglishLesson.status == "missing")
        )
    ).scalar() or 0
    transcripts = (
        await db.execute(select(func.count()).select_from(EnglishTranscript))
    ).scalar() or 0
    words = (await db.execute(select(func.count()).select_from(EnglishVocabulary))).scalar() or 0
    seconds = (
        await db.execute(
            select(func.coalesce(func.sum(EnglishLesson.duration), 0)).where(
                EnglishLesson.status == "ready"
            )
        )
    ).scalar() or 0

    root = english_root()
    return {
        "root": str(root),
        "exists": root.is_dir(),
        "courses": courses,
        "lessons": lessons,
        "lessons_missing": missing,
        "transcripts": transcripts,
        "vocabulary": words,
        "total_seconds": int(seconds),
    }


__all__ = [
    "AUDIO_EXTS",
    "IMAGE_EXTS",
    "MEDIA_EXTS",
    "VIDEO_EXTS",
    "ScanReport",
    "collect_assets",
    "english_root",
    "is_inside_root",
    "refresh_course_vocabulary",
    "scan",
    "stats",
]
