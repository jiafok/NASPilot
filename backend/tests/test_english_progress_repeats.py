import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.core.database import Base
from app.models import EnglishCourse, EnglishLesson, EnglishProgress, User
from app.services.english import query


class EnglishProgressRepeatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as session:
            self.user = User(username="repeat-test", hashed_password="unused", display_name="Repeat test")
            course = EnglishCourse(channel="test", title="Test course", slug="test-course")
            session.add_all([self.user, course])
            await session.flush()
            lesson = EnglishLesson(course_id=course.id, title="Test lesson", file_path="test.mp4", duration=100)
            session.add(lesson)
            await session.commit()
            self.user_id = self.user.id
            self.lesson_id = lesson.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_repeat_target_counts_only_full_play_events_and_moves_to_history(self):
        async with self.sessions() as session:
            selected = await query.upsert_progress(
                session, self.user_id, lesson_id=self.lesson_id, position=100, repeat_target=3
            )
            self.assertFalse(selected["completed"])
            self.assertEqual(selected["completed_plays"], 0)

            for expected_plays in (1, 2):
                progress = await query.upsert_progress(
                    session,
                    self.user_id,
                    lesson_id=self.lesson_id,
                    position=100,
                    completed=True,
                    repeat_target=3,
                )
                self.assertFalse(progress["completed"])
                self.assertEqual(progress["completed_plays"], expected_plays)

            completed = await query.upsert_progress(
                session,
                self.user_id,
                lesson_id=self.lesson_id,
                position=100,
                completed=True,
                repeat_target=3,
            )
            self.assertTrue(completed["completed"])
            self.assertEqual(completed["completed_plays"], 3)

            payload = await query.home_payload(session, self.user)
            self.assertEqual(payload["continue_learning"], [])
            self.assertEqual(payload["recent_completed"][0]["lesson_id"], self.lesson_id)
            self.assertEqual(payload["recent_completed"][0]["completed_plays"], 3)

    async def test_raising_target_reopens_completed_lesson(self):
        async with self.sessions() as session:
            await query.upsert_progress(
                session, self.user_id, lesson_id=self.lesson_id, position=100, completed=True
            )
            reopened = await query.upsert_progress(
                session, self.user_id, lesson_id=self.lesson_id, position=100, repeat_target=2
            )
            self.assertFalse(reopened["completed"])
            self.assertEqual(reopened["completed_plays"], 1)

    async def test_single_play_threshold_records_one_and_end_does_not_double_count(self):
        async with self.sessions() as session:
            threshold = await query.upsert_progress(
                session, self.user_id, lesson_id=self.lesson_id, position=96
            )
            self.assertTrue(threshold["completed"])
            self.assertEqual(threshold["completed_plays"], 1)
            ended = await query.upsert_progress(
                session, self.user_id, lesson_id=self.lesson_id, position=100, completed=True
            )
            self.assertEqual(ended["completed_plays"], 1)


if __name__ == "__main__":
    unittest.main()