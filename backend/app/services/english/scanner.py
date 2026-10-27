"""Resource Scanner — filesystem → courses / lessons / transcripts / vocabulary.

Idempotent by design: `lessons.file_path` is unique, so re-scanning only
updates metadata. Files that disappeared are flagged ``missing`` instead of
being deleted, so a user's progress and bookmarks are never lost.

Directory → hierarchy rules are documented in
`docs/english-learning-center/API.md` (Part A, Resource Import Specification).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    EnglishCourse,
    EnglishLesson,
    EnglishTranscript,
    EnglishVocabulary,
)
from app.services.media_center import settings_store as media_paths
from app.services.english import metadata as meta
from app.services.english import taxonomy
from app.services.english import subtitles
from app.services.english import vocabulary as vocab

logger = logging.getLogger("naspilot.english.scanner")

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

DEFAULT_CHANNEL = "默认"
DEFAULT_COURSE = "未分类"

#: Leading folders the Media Download Center uses to separate output modes.
#: They describe where a file was written, not what it teaches, so they are
#: stripped before deriving the channel/course identity.
MODE_SEGMENTS = {"video", "audio"}

#: Serialises scans process-wide. SQLite permits a single writer, so two scans
#: running at once (a manual POST while the watcher polls, say) would block each
#: other on every commit and, past ``busy_timeout``, surface as "database is
#: locked" on unrelated requests such as login. The guard lives here rather than
#: in the caller so no future call site can bypass it.
_SCAN_LOCK = asyncio.Lock()

#: Generic container folders some providers add. They add no meaning to a
#: lesson title, so they are not used as a title prefix.
GENERIC_SEGMENTS = {"videos", "streams", "shorts", "playlist", "playlists"}


@dataclass
class Asset:
    """All files that belong to one lesson (same basename, same directory)."""

    directory: Path
    stem: str
    media: Path | None = None
    subtitle: Path | None = None
    subtitle_lang: str | None = None
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
    #: Files whose size/mtime/classification were already up to date.
    lessons_skipped: int = 0
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
            "lessons_skipped": self.lessons_skipped,
            "skipped": self.skipped,
            "errors": self.errors[:20],
        }


def english_root() -> Path:
    """Root of the English library — the media library itself.

    By default this is the very same directory the Media Download Center writes
    into, so a downloaded episode becomes a lesson with no copying, no second
    volume and no extra configuration. Everything the user already has under
    ``/Media/Video`` or ``/Media/Audio`` is picked up as is.

    Setting ``ENGLISH_ROOT`` (env or .env) always wins, which is how the module
    can be pointed at a separate tree instead.
    """
    explicit = str(settings.ENGLISH_ROOT or "").strip()
    if explicit:
        return Path(explicit)
    try:
        return media_paths.root()
    except Exception:  # pragma: no cover - settings store is defensive already
        return media_paths.default_root()


def is_inside_root(path: Path, root: Path) -> bool:
    """True when ``path`` really lives under ``root`` (symlink-safe)."""
    try:
        real = Path(os.path.realpath(path))
        root_real = Path(os.path.realpath(root))
        return real == root_real or root_real in real.parents
    except OSError:
        return False


# ── Filesystem collection ────────────────────────────────────────────────


#: ``<name>.<lang>.srt`` — yt-dlp and most downloaders append a language tag,
#: which must be stripped or the subtitle never binds to its video.
_LANG_SUFFIX = re.compile(r"^(?P<base>.+?)\.(?P<lang>[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?)$")


def split_language(stem: str) -> tuple[str, str | None]:
    """``"Episode.en-US"`` → ``("Episode", "en-US")``; no tag → unchanged."""
    match = _LANG_SUFFIX.match(stem)
    if not match:
        return stem, None
    return match.group("base"), match.group("lang")


def _subtitle_rank(path: Path, lang: str | None) -> int:
    """Higher wins: English over other languages, srt over vtt."""
    score = 0
    if path.suffix.lower() == ".srt":
        score += 2
    low = (lang or "").lower()
    if low.startswith("en"):
        score += 4
    elif low.startswith("zh"):
        score += 1
    return score


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
                base, lang = split_language(stem)
                asset = bucket.setdefault(base, Asset(directory=directory, stem=base))
                # Prefer English, then .srt — see _subtitle_rank
                if asset.subtitle is None or _subtitle_rank(path, lang) > _subtitle_rank(
                    asset.subtitle, asset.subtitle_lang
                ):
                    asset.subtitle = path
                    asset.subtitle_lang = lang
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
    # "/Media/Video/<provider>/<channel>/..." → drop the output-mode folder so
    # the Media Download Center's own layout lands on provider=channel,
    # channel=course instead of producing a course literally called "Video".
    if parts and parts[0].lower() in MODE_SEGMENTS:
        parts = parts[1:]
    if not parts:
        return DEFAULT_CHANNEL, DEFAULT_COURSE, ""
    if len(parts) == 1:
        return parts[0], parts[0], ""
    extra = [p for p in parts[2:] if p.lower() not in GENERIC_SEGMENTS]
    return parts[0], parts[1], " ".join(extra)


def _pick_course_cover(directory: Path, assets: dict[str, Asset]) -> Path | None:
    for asset in assets.values():
        if asset.cover is not None:
            return asset.cover
    for name in ("cover.jpg", "cover.png", "cover.webp", "folder.jpg", "poster.jpg"):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _probe_duration(path: Path) -> int:
    """Duration in whole seconds via ffprobe (0 when it cannot be read).

    Reusing the media library means most lessons have no subtitle file, so the
    duration cannot come from the transcript. ffprobe is already in the image
    for the download center, so a single cheap call per file is enough — and
    the value is cached in the row, so an unchanged file is probed only once.
    """
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    try:
        return max(0, int(float((proc.stdout or "").strip() or 0)))
    except ValueError:
        return 0


def _is_unchanged(
    existing: EnglishLesson,
    asset: Asset,
    course: EnglishCourse,
    position: int,
) -> bool:
    """True when re-processing this file would produce exactly the same row.

    Deliberately strict — a wrong ``True`` means stale data, so anything that
    could change the outcome (file contents, subtitle sidecar, course, order,
    or the classification algorithm version) forces the full path.
    """
    if existing.status != "ready" or not existing.level:
        return False
    if existing.course_id != course.id or existing.position != position:
        return False
    # A subtitle added (or replaced) later must be picked up.
    if (existing.subtitle_path or "") != (str(asset.subtitle) if asset.subtitle else ""):
        return False
    features = existing.features if isinstance(existing.features, dict) else {}
    if features.get("v") != taxonomy.TAXONOMY_VERSION:
        return False
    try:
        stat = asset.media.stat()  # type: ignore[union-attr]
    except OSError:
        return False
    if existing.size_bytes != stat.st_size:
        return False
    published = existing.published_at
    if published is None:
        return False
    # _mtime() stores the UTC wall clock, but SQLite has no timezone type so it
    # comes back naive. Reading it as "now" is how the incremental check broke
    # for every row in a UTC+8 host: each timestamp looked 8 hours off and the
    # whole library was re-parsed on every scan. Treat a naive value as UTC.
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    # compare at whole-second resolution (SQLite round-trips may lose sub-second)
    if int(published.timestamp()) != int(stat.st_mtime):
        return False
    return True


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
    existing: EnglishLesson | None = None,
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

    # Classify this file on its own — see taxonomy.classify_lesson. The course
    # titles are only context for topic detection.
    text = subtitles.search_text(segments) if segments else ""
    turns = sum(
        1
        for index, seg in enumerate(segments)
        if seg.speaker is not None
        and (index == 0 or segments[index - 1].speaker != seg.speaker)
    )
    taxonomy_result = taxonomy.classify_lesson(
        title=title,
        course_titles=[course.title, course.channel],
        text=text,
        duration=duration,
        turns=turns,
    )

    try:
        size = asset.media.stat().st_size
    except OSError:
        size = 0

    if existing is None:
        # The scan prefetches every lesson by path, so this only runs for files
        # that are genuinely new (or appeared while we were walking).
        existing = (
            await db.execute(select(EnglishLesson).where(EnglishLesson.file_path == file_path))
        ).scalar_one_or_none()

    # ── Incremental fast path ────────────────────────────────────────────
    # Auto-scanning runs often, and re-parsing every subtitle + re-measuring
    # every file costs ~60s on a 400-lesson library. Nothing has to be redone
    # when the file itself, its subtitle and the classification algorithm are
    # all unchanged, so those rows are skipped outright.
    if existing is not None and _is_unchanged(existing, asset, course, position):
        report.lessons_skipped += 1
        return existing

    if not duration:
        # Reuse the cached value so an unchanged file is never probed twice.
        if existing is not None and existing.duration:
            duration = existing.duration
        else:
            duration = _probe_duration(asset.media)

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
            level=taxonomy_result.level,
            topics=taxonomy_result.topics,
            skills=taxonomy_result.skills,
            format=taxonomy_result.format,
            features=taxonomy_result.features,
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
        # A re-scan must refresh the classification: the transcript may have
        # only appeared now, and the measurement depends on it.
        lesson.level = taxonomy_result.level
        lesson.topics = taxonomy_result.topics
        lesson.skills = taxonomy_result.skills
        lesson.format = taxonomy_result.format
        lesson.features = taxonomy_result.features
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


async def library_stats(db: AsyncSession) -> tuple[Counter[str], int]:
    """Document frequency of every word across the whole library.

    Rarity has to be measured against the user's own material: a word that
    turns up in a third of the lessons is common English *for them*, even if a
    dictionary would file it as intermediate. Computed once per scan.
    """
    rows = (await db.execute(select(EnglishTranscript.search_text))).scalars().all()
    # End the read transaction before the CPU work: with BEGIN IMMEDIATE a
    # transaction owns the write lock, and measuring the whole library would
    # otherwise block every other writer for seconds.
    await db.commit()
    return await asyncio.to_thread(vocab.library_document_frequency, [r for r in rows if r])


def _turns_of(segments: list[dict[str, object]] | None) -> int:
    """Count speaker turns in a stored transcript."""
    if not segments:
        return 0
    turns = 0
    previous: object = object()
    for seg in segments:
        speaker = seg.get("spk")
        if isinstance(speaker, str) and speaker != previous:
            turns += 1
        previous = speaker
    return turns


def _content_words(text: str, limit: int = 200) -> list[str]:
    """Most frequent non-trivial words of a course, for topic detection."""
    counts = Counter(vocab.tokenize(text))
    ranked = [
        (word, count)
        for word, count in counts.most_common(limit * 4)
        if len(word) > 3 and not vocab.known_form(word)
    ]
    return [word for word, _ in ranked[:limit]]


async def refresh_course_taxonomy(
    db: AsyncSession,
    course: EnglishCourse,
    *,
    override: dict | None = None,
) -> dict[str, object]:
    """Aggregate the per-lesson classifications into course-level values.

    Each lesson already measured itself when it was upserted, so the course is
    just a summary of those rows — that keeps one measurement per file instead
    of re-reading every transcript here, and it guarantees the course badge is
    consistent with the badges shown on its lessons.

    ``level`` uses the **median** lesson level so a single easy intro or one
    very hard episode cannot mislabel the whole course; topics/skills use the
    most common labels; the format takes a majority vote.
    """
    rows = (
        await db.execute(
            select(EnglishLesson, EnglishTranscript)
            .outerjoin(EnglishTranscript, EnglishTranscript.lesson_id == EnglishLesson.id)
            .where(
                EnglishLesson.course_id == course.id,
                EnglishLesson.status == "ready",
            )
            .order_by(EnglishLesson.position.asc())
        )
    ).all()

    lesson_levels: list[str] = []
    lesson_topics: list[list[str] | None] = []
    lesson_skills: list[list[str] | None] = []
    lesson_formats: list[str | None] = []
    lesson_texts: list[str] = []
    stale: list[EnglishLesson] = []

    for lesson, transcript in rows:
        if not lesson.level:
            stale.append(lesson)
        lesson_levels.append(lesson.level or "")
        lesson_topics.append(list(lesson.topics or []))
        lesson_skills.append(list(lesson.skills or []))
        lesson_formats.append(lesson.format)
        lesson_texts.append((transcript.search_text if transcript else "") or "")

    # Rows written before per-lesson classification existed have no level yet;
    # classify them now so the aggregate is not built from empty values.
    for lesson in stale:
        transcript = (
            await db.execute(
                select(EnglishTranscript).where(EnglishTranscript.lesson_id == lesson.id)
            )
        ).scalar_one_or_none()
        measured = taxonomy.classify_lesson(
            title=lesson.title,
            course_titles=[course.title, course.channel],
            text=(transcript.search_text if transcript else "") or "",
            duration=int(lesson.duration or 0),
            turns=_turns_of(transcript.segments if transcript else None),
        )
        lesson.level = measured.level
        lesson.topics = measured.topics
        lesson.skills = measured.skills
        lesson.format = measured.format
        lesson.features = measured.features
        lesson_levels.append(measured.level)
        lesson_topics.append(list(measured.topics))
        lesson_skills.append(list(measured.skills))
        lesson_formats.append(measured.format)

    level = taxonomy.aggregate_level(lesson_levels)
    course_topics = taxonomy.aggregate_labels(lesson_topics)
    course_skills = taxonomy.aggregate_labels(lesson_skills, limit=3)
    media_format = taxonomy.aggregate_format(lesson_formats)

    # The course title carries information the lessons may not (e.g. "TED
    # Lectures" when every episode is just a talk title), so merge it in.
    title_topics = taxonomy.topics_for(
        titles=[course.title, course.channel],
        description=course.description,
        content_words=_content_words("\n".join(lesson_texts)),
    )
    merged_topics: list[str] = []
    for label in title_topics + course_topics:
        if label != "其他" and label not in merged_topics:
            merged_topics.append(label)
    course.topics = (merged_topics or course_topics or ["其他"])[:4]

    course.level = level
    course.skills = course_skills
    course.format = media_format

    # A hand-written course.json always wins over inference.
    override = override or {}
    if not override.get("category"):
        course.category = course.topics[0] if course.topics else course.category or "其他"
    if not override.get("difficulty"):
        course.difficulty = taxonomy.difficulty_for(level)

    return {
        "level": level,
        "difficulty": course.difficulty,
        "topics": course.topics,
        "skills": course.skills,
        "format": course.format,
        "lesson_levels": lesson_levels,
    }


async def refresh_course_vocabulary(
    db: AsyncSession,
    course: EnglishCourse,
    *,
    library_df: Counter[str] | None = None,
    library_size: int = 0,
) -> int:
    """Rebuild the vocabulary table for one course from its transcripts.

    Words are filtered against everyday English, so the result is the material
    this learner would actually need help with — not a frequency table.
    """
    rows = (
        await db.execute(
            select(EnglishTranscript.search_text)
            .join(EnglishLesson, EnglishLesson.id == EnglishTranscript.lesson_id)
            .where(EnglishLesson.course_id == course.id)
        )
    ).scalars().all()

    texts = [t for t in rows if t]
    # Release the write lock before scoring: the computation takes tens of
    # seconds on a large library and must not hold the database hostage.
    await db.commit()
    if texts:
        # Scoring the whole course corpus is CPU-bound and took ~55s of
        # straight-line Python on a 400-lesson library. Running it inline
        # froze the event loop for that long, so every HTTP request
        # (logins included) timed out. A worker thread keeps the API
        # responsive; the result is unchanged.
        result = await asyncio.to_thread(
            vocab.extract_new_words,
            texts,
            library_df=library_df,
            library_size=library_size,
            limit=int(settings.ENGLISH_VOCAB_LIMIT or 300),
        )
    else:
        result = vocab.VocabularyResult()
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


async def _scan_impl(
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
    # Courses whose lessons actually changed. Their classification and word
    # list are recomputed at the end; untouched courses keep the previous
    # values, which is what makes an idle scan cheap instead of a minute of
    # pointless CPU work.
    dirty_courses: set[int] = set()
    seen_files: set[str] = set()
    # One query for the whole library instead of one per file: the per-row
    # round-trip is what made an idle scan take ~10s instead of ~1s.
    existing_by_path = {
        lesson.file_path: lesson
        for lesson in (await db.execute(select(EnglishLesson))).scalars().all()
    }
    await db.commit()
    corpus_df: Counter[str] = Counter()
    corpus_size = 0

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
                before_changes = report.lessons_created + report.lessons_updated
                await _upsert_lesson(
                    db,
                    course=course,
                    asset=asset,
                    position=index,
                    title_prefix=prefix,
                    report=report,
                    existing=existing_by_path.get(str(asset.media)),
                )
                if report.lessons_created + report.lessons_updated > before_changes:
                    dirty_courses.add(course.id)
            except OperationalError as exc:
                # Lost the write lock. Rollback leaves the session unusable for
                # the loaded courses, and a partial scan is perfectly safe: it
                # is idempotent and the next pass finishes the job. Aborting
                # here also keeps us from marking files "missing" based on an
                # incomplete walk.
                await db.rollback()
                report.errors.append(f"{asset.media.name}: {exc}")
                logger.warning("Scan aborted after write contention on %s", asset.media)
                return report.as_dict()
            except Exception as exc:  # one bad file must not stop the scan
                report.errors.append(f"{asset.media.name}: {exc}")
                logger.warning("Scan failed for %s", asset.media, exc_info=True)
            # One transaction per lesson. All the slow work (reading the
            # subtitle, ffprobe, classification) happens before the first
            # statement, so the write lock is held for milliseconds rather than
            # seconds - which is what keeps concurrent requests waiting instead
            # of failing.
            await db.commit()
        await db.flush()

    # Anything on disk no longer present → missing (rows are kept)
    all_lessons = (await db.execute(select(EnglishLesson))).scalars().all()
    root_real = str(Path(os.path.realpath(base)))
    for lesson in all_lessons:
        in_root = lesson.file_path.startswith(root_real)
        if not in_root or lesson.file_path in seen_files:
            continue
        if lesson.status != "missing":
            lesson.status = "missing"
            report.lessons_missing += 1
            dirty_courses.add(lesson.course_id)
        # Aggregates are recomputed for `touched_courses` only, so every course
        # owning a missing lesson must be queued as well — otherwise its
        # lesson_count keeps showing the stale value (and would never drop to
        # zero when a whole course disappears).
        if lesson.course_id not in touched_courses:
            course = await db.get(EnglishCourse, lesson.course_id)
            if course is not None:
                touched_courses[course.id] = course

    if with_vocabulary and dirty_courses:
        # Library-wide word statistics are only used to score word lists, so an
        # unchanged library never pays for them.
        corpus_df, corpus_size = await library_stats(db)

    # Update course aggregates + cover
    # (must run AFTER missing detection so counts exclude gone files)
    await db.commit()
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

        if course_id not in dirty_courses:
            # Counts above are cheap and always refreshed; the expensive parts
            # cannot have changed.
            await db.commit()
            continue

        # Classification needs the transcripts, so it runs in the aggregation
        # pass — after every lesson of the course has been upserted.
        try:
            directory = Path(course.directory) if course.directory else None
            await refresh_course_taxonomy(
                db,
                course,
                override=meta.read_course_override(directory) if directory else {},
            )
        except Exception:
            logger.warning("Taxonomy failed for course %s", course_id, exc_info=True)

        if with_vocabulary and course.lesson_count:
            try:
                report.vocabulary_terms += await refresh_course_vocabulary(
                    db, course, library_df=corpus_df, library_size=corpus_size
                )
            except Exception:
                logger.warning("Vocabulary refresh failed for course %s", course_id, exc_info=True)
        # Commit per course: taxonomy + vocabulary are the two slowest writes
        # in the scan and there is no reason to hold the lock across courses.
        await db.commit()

    await db.commit()
    logger.info("English scan finished: %s", report.as_dict())
    return report.as_dict()


def fingerprint(root: Path | None = None) -> str:
    """Cheap signature of the library: file count, total size, newest mtime.

    Used by the auto-scan watcher to decide whether anything changed at all.
    Walking 800 files takes a few milliseconds, which is cheap enough to poll
    every minute; parsing their subtitles is not.
    """
    base = root or english_root()
    if not base.is_dir():
        return "missing"
    count = 0
    total = 0
    newest = 0.0
    for current, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in {"@eaDir", "#recycle"}]
        for name in filenames:
            if name.startswith("."):
                continue
            if Path(name).suffix.lower() not in MEDIA_EXTS | subtitles.SUBTITLE_EXTS:
                continue
            try:
                stat = os.stat(os.path.join(current, name))
            except OSError:
                continue
            count += 1
            total += stat.st_size
            newest = max(newest, stat.st_mtime)
    return f"{count}:{total}:{int(newest)}"


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


async def scan(
    db: AsyncSession,
    *,
    root: Path | None = None,
    with_vocabulary: bool = True,
) -> dict[str, object]:
    """Scan the English library, one scan at a time across the process."""
    if _SCAN_LOCK.locked():
        logger.info("Scan requested while another is running; waiting for it")
    async with _SCAN_LOCK:
        return await _scan_impl(db, root=root, with_vocabulary=with_vocabulary)


__all__ = [
    "AUDIO_EXTS",
    "IMAGE_EXTS",
    "MEDIA_EXTS",
    "VIDEO_EXTS",
    "ScanReport",
    "collect_assets",
    "english_root",
    "is_inside_root",
    "fingerprint",
    "library_stats",
    "refresh_course_taxonomy",
    "refresh_course_vocabulary",
    "scan",
    "stats",
]
