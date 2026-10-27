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
import re
import subprocess
from collections import Counter
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


async def library_stats(db: AsyncSession) -> tuple[Counter[str], int]:
    """Document frequency of every word across the whole library.

    Rarity has to be measured against the user's own material: a word that
    turns up in a third of the lessons is common English *for them*, even if a
    dictionary would file it as intermediate. Computed once per scan.
    """
    rows = (await db.execute(select(EnglishTranscript.search_text))).scalars().all()
    return vocab.library_document_frequency([r for r in rows if r])


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
    """Recompute every classification dimension for one course.

    Difficulty is measured from the transcripts (sentence length, rare-word
    ratio, speech rate); topics and skills come from titles plus content words;
    the media form comes from the title and the speaker-turn pattern.
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

    lesson_texts: list[str] = []
    lesson_durations: list[int] = []
    lesson_turns: list[int] = []
    titles: list[str] = [course.title, course.channel]
    lesson_titles: list[str] = []
    for lesson, transcript in rows:
        titles.append(lesson.title)
        lesson_titles.append(lesson.title)
        text = (transcript.search_text if transcript else "") or ""
        lesson_texts.append(text)
        lesson_durations.append(int(lesson.duration or 0))
        lesson_turns.append(_turns_of(transcript.segments if transcript else None))

    content = _content_words("\n".join(lesson_texts))
    result = taxonomy.classify_course(
        titles=titles,
        description=course.description,
        lesson_texts=lesson_texts,
        lesson_durations=lesson_durations,
        lesson_turns=lesson_turns,
        lesson_titles=lesson_titles,
        course_titles=[course.title, course.channel],
        content_words=content,
    )

    course.level = result.level
    course.topics = result.topics
    course.skills = result.skills
    course.format = result.format

    # A hand-written course.json always wins over inference.
    override = override or {}
    if not override.get("category"):
        course.category = result.topics[0] if result.topics else course.category or "其他"
    if not override.get("difficulty"):
        course.difficulty = result.difficulty
    return result.as_dict()


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
    if texts:
        result = vocab.extract_new_words(
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
    corpus_df: Counter[str] = Counter()
    corpus_size = 0
    if with_vocabulary:
        corpus_df, corpus_size = await library_stats(db)

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
        if not in_root or lesson.file_path in seen_files:
            continue
        if lesson.status != "missing":
            lesson.status = "missing"
            report.lessons_missing += 1
        # Aggregates are recomputed for `touched_courses` only, so every course
        # owning a missing lesson must be queued as well — otherwise its
        # lesson_count keeps showing the stale value (and would never drop to
        # zero when a whole course disappears).
        if lesson.course_id not in touched_courses:
            course = await db.get(EnglishCourse, lesson.course_id)
            if course is not None:
                touched_courses[course.id] = course

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
    "library_stats",
    "refresh_course_taxonomy",
    "refresh_course_vocabulary",
    "scan",
    "stats",
]
