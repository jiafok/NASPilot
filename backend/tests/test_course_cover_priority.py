import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.core.database import Base
from app.models import EnglishCourse
from app.services.english import scanner


class CourseCoverTests(unittest.IsolatedAsyncioTestCase):
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