import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.core.database import Base
from app.models import EnglishCourse, EnglishLesson
from app.services.english import scanner, taxonomy


class DifficultyEvidenceTests(unittest.TestCase):
    def test_missing_or_chinese_transcript_is_unrated(self):
        for text in ("", "这是中文翻译。" * 50, "Hello there."):
            result = taxonomy.classify_lesson(title="Lesson", text=text)
            self.assertEqual(result.level, "未评级")
            self.assertEqual(result.difficulty, "mixed")

    def test_english_transcripts_are_measured_individually(self):
        easy = taxonomy.classify_lesson(title="Lesson", text="I like my cat. " * 20, duration=100)
        hard = taxonomy.classify_lesson(
            title="Lesson",
            text=("Epistemological paradigms necessitate sophisticated methodological scrutiny "
                  "whereas interdisciplinary investigations contextualize unprecedented "
                  "phenomenological abstractions within multifaceted theoretical frameworks. ") * 20,
            duration=100,
        )
        self.assertIn(easy.level, taxonomy.LEVELS)
        self.assertIn(hard.level, taxonomy.LEVELS)
        self.assertNotEqual(easy.level, hard.level)

    def test_course_ignores_unrated_lessons(self):
        self.assertEqual(taxonomy.aggregate_level(["未评级", "A2"]), "A2")
        self.assertEqual(taxonomy.aggregate_level(["未评级"]), "未评级")


class CourseCoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_lesson_title_replaces_old_directory_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "Video" / "bilibili" / "Channel" / "Long collection name"
            nested.mkdir(parents=True)
            (nested / "01 Listening [BV123_p1].mp4").write_bytes(b"video")
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    with patch.object(scanner, "_probe_duration", return_value=60):
                        await scanner.scan(session, root=root, with_vocabulary=False)
                        lesson = (await session.execute(select(EnglishLesson))).scalar_one()
                        self.assertEqual(lesson.title, "01 Listening")
                        lesson.title = "Long collection name 01 Listening"
                        await session.commit()
                        await scanner.scan(session, root=root, with_vocabulary=False)
                        self.assertEqual(lesson.title, "01 Listening")
            finally:
                await engine.dispose()

    async def test_folder_art_then_first_lesson_cover(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_dir = root / "Channel" / "Course"
            course_dir.mkdir(parents=True)
            for name in ("002 - Second", "001 - First"):
                (course_dir / f"{name}.mp4").write_bytes(b"video")
                (course_dir / f"{name}.jpg").write_bytes(b"image")
            artwork = course_dir / "cover.jpg"
            artwork.write_bytes(b"course image")
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                with patch.object(scanner, "_probe_duration", return_value=60):
                    await scanner.scan(session, root=root, with_vocabulary=False)
                    course = (await session.execute(select(EnglishCourse))).scalar_one()
                    self.assertEqual(course.cover_path, str(artwork))
                    artwork.unlink()
                    await scanner.scan(session, root=root, with_vocabulary=False)
                    self.assertEqual(course.cover_path, str(course_dir / "001 - First.jpg"))
                    self.assertEqual(course.lesson_count, 2)
            await engine.dispose()

    async def test_nested_lessons_use_course_folder_cover(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course_dir = root / "Channel" / "Course"
            nested = course_dir / "Unit One"
            nested.mkdir(parents=True)
            (nested / "001 - Lesson.mp4").write_bytes(b"video")
            (nested / "001 - Lesson.jpg").write_bytes(b"image")
            artwork = course_dir / "folder.jpg"
            artwork.write_bytes(b"course image")
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                with patch.object(scanner, "_probe_duration", return_value=60):
                    await scanner.scan(session, root=root, with_vocabulary=False)
                course = (await session.execute(select(EnglishCourse))).scalar_one()
                self.assertEqual(course.directory, str(course_dir))
                self.assertEqual(course.cover_path, str(artwork))
            await engine.dispose()