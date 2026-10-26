"""English Learning Center — ORM models.

Eight tables, added to the existing NASPilot SQLite database (no separate DB,
no changes to existing tables). See `docs/english-learning-center/DATABASE.md`.

Hierarchy::

    EnglishCourse (Channel → Course)
      └── EnglishLesson   (one media file + optional subtitle / cover)
            ├── EnglishTranscript   (1:1, parsed srt/vtt segments)
            └── EnglishBookmark     (per user, per sentence)

    EnglishVocabulary   (per course, optional per lesson)
    EnglishProgress     (per user × lesson)
    EnglishFavorite     (per user × course)
    EnglishDailyStat    (per user × day)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models import TimestampMixin


class EnglishCourse(TimestampMixin, Base):
    """A course = one directory under a channel directory."""

    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("channel", "slug", name="uq_courses_channel_slug"),
        Index("ix_courses_category", "category"),
        Index("ix_courses_difficulty", "difficulty"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(191), nullable=False, default="默认")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(191), nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="其他")
    difficulty: Mapped[str] = mapped_column(String(16), default="mixed")
    description: Mapped[str | None] = mapped_column(Text)
    cover_path: Mapped[str | None] = mapped_column(String(1024))
    directory: Mapped[str | None] = mapped_column(String(1024), index=True)
    lesson_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    source: Mapped[str | None] = mapped_column(String(64))
    tags: Mapped[list[str] | None] = mapped_column(JSON, default=list)

    lessons: Mapped[list["EnglishLesson"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )


class EnglishLesson(TimestampMixin, Base):
    """One media file. `file_path` is the idempotency key for scanning."""

    __tablename__ = "lessons"
    __table_args__ = (
        UniqueConstraint("file_path", name="uq_lessons_file_path"),
        Index("ix_lessons_course_position", "course_id", "position"),
        Index("ix_lessons_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    media_type: Mapped[str] = mapped_column(String(16), default="video")
    media_ext: Mapped[str] = mapped_column(String(16), default=".mp4")
    subtitle_path: Mapped[str | None] = mapped_column(String(1024))
    subtitle_format: Mapped[str | None] = mapped_column(String(8))
    cover_path: Mapped[str | None] = mapped_column(String(1024))
    duration: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(16), default="ready", server_default="ready")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    course: Mapped[EnglishCourse] = relationship(back_populates="lessons")
    transcript: Mapped["EnglishTranscript | None"] = relationship(
        back_populates="lesson", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )


class EnglishTranscript(TimestampMixin, Base):
    """Parsed subtitle for one lesson (1:1)."""

    __tablename__ = "transcripts"
    __table_args__ = (UniqueConstraint("lesson_id", name="uq_transcripts_lesson"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lesson_id: Mapped[int] = mapped_column(
        ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    language: Mapped[str] = mapped_column(String(16), default="en")
    segment_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: ``[{"i": 0, "start": 0, "end": 2400, "text": "Hello."}]`` (ms)
    segments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=list)
    #: All sentences joined — backs the MVP `LIKE` search.
    search_text: Mapped[str | None] = mapped_column(Text)

    lesson: Mapped[EnglishLesson] = relationship(back_populates="transcript")


class EnglishVocabulary(TimestampMixin, Base):
    """A word or phrase extracted from a course (or one lesson)."""

    __tablename__ = "vocabulary"
    __table_args__ = (
        UniqueConstraint("course_id", "word", name="uq_vocabulary_course_word"),
        Index("ix_vocabulary_frequency", "frequency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    lesson_id: Mapped[int | None] = mapped_column(
        ForeignKey("lessons.id", ondelete="SET NULL"), index=True
    )
    word: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="word")
    frequency: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    difficulty: Mapped[str] = mapped_column(String(16), default="mixed")
    meaning: Mapped[str | None] = mapped_column(Text)
    example: Mapped[str | None] = mapped_column(Text)


class EnglishProgress(TimestampMixin, Base):
    """Playback position per user × lesson."""

    __tablename__ = "user_progress"
    __table_args__ = (UniqueConstraint("user_id", "lesson_id", name="uq_progress_user_lesson"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lesson_id: Mapped[int] = mapped_column(
        ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    listen_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EnglishFavorite(TimestampMixin, Base):
    """Course favourite per user."""

    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "course_id", name="uq_favorites_user_course"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )


class EnglishBookmark(TimestampMixin, Base):
    """Sentence-level bookmark per user × lesson."""

    __tablename__ = "bookmarks"
    __table_args__ = (
        Index("ix_bookmarks_user_lesson", "user_id", "lesson_id"),
        UniqueConstraint("user_id", "lesson_id", "position", name="uq_bookmarks_user_lesson_pos"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lesson_id: Mapped[int] = mapped_column(
        ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    at_seconds: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    note: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)


class EnglishDailyStat(TimestampMixin, Base):
    """Per-user, per-day learning aggregate."""

    __tablename__ = "learning_statistics"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_stats_user_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    listen_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lessons_completed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    words_learned: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


__all__ = [
    "EnglishBookmark",
    "EnglishCourse",
    "EnglishDailyStat",
    "EnglishFavorite",
    "EnglishLesson",
    "EnglishProgress",
    "EnglishTranscript",
    "EnglishVocabulary",
]
