"""生词本跨设备同步 — service-level guarantees the phone and laptop depend on.

The Stage-0 client stored saved words in ``localStorage`` only, so two devices
on the same account showed two different word books. These tests pin down what
the server now guarantees:

* the same word saved twice stays one row (the client retries when offline);
* one learner's word book never leaks into another's;
* an old ``added_at`` from a device migration is preserved;
* deleting removes only the caller's row;
* deleting a user leaves no orphaned words behind.
"""

import unittest

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — registers every table on Base.metadata
from app.core.database import Base
from app.models import EnglishCourse, EnglishLesson, EnglishSavedWord, User
from app.services.english import words


class SavedWordSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")

        # The production engine turns foreign keys on, which is what makes
        # `ON DELETE CASCADE` real. Mirror that here or the cascade test is a
        # false pass.
        @event.listens_for(self.engine.sync_engine, "connect")
        def _fk_on(dbapi_connection, _record):  # pragma: no cover - driver hook
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as session:
            alice = User(username="alice", hashed_password="unused")
            bob = User(username="bob", hashed_password="unused")
            course = EnglishCourse(channel="test", title="Test course", slug="test-course")
            session.add_all([alice, bob, course])
            await session.flush()
            lesson = EnglishLesson(
                course_id=course.id, title="Test lesson", file_path="test.mp4", duration=100
            )
            session.add(lesson)
            await session.commit()
            self.alice_id, self.bob_id, self.lesson_id = alice.id, bob.id, lesson.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_saving_the_same_word_twice_keeps_one_row(self):
        async with self.sessions() as session:
            first = await words.save_word(
                session,
                self.alice_id,
                word="Serendipity",
                phonetic="/seren-dip-ity/",
                meaning="意外发现",
                lesson_id=self.lesson_id,
                sentence="A moment of serendipity.",
            )
            self.assertTrue(first["created"])
            self.assertEqual(first["word"], "Serendipity")
            self.assertEqual(first["key"], "serendipity")

            # Same word, different casing: the phone retrying must not duplicate.
            second = await words.save_word(session, self.alice_id, word="  serendipity ")
            self.assertFalse(second["created"])
            self.assertEqual(second["key"], "serendipity")
            # The retry carried no definition and must not erase the good one.
            self.assertEqual(second["meaning"], "意外发现")
            self.assertEqual(second["addedAt"], first["addedAt"])

            listed = await words.list_words(session, self.alice_id)
            self.assertEqual(listed["total"], 1)

    async def test_word_books_are_isolated_per_user(self):
        async with self.sessions() as session:
            await words.save_word(session, self.alice_id, word="budget", meaning="预算")
            await words.save_word(session, self.bob_id, word="invoice", meaning="发票")

            alice = await words.list_words(session, self.alice_id)
            bob = await words.list_words(session, self.bob_id)

            self.assertEqual([item["key"] for item in alice["items"]], ["budget"])
            self.assertEqual([item["key"] for item in bob["items"]], ["invoice"])
            self.assertTrue(await words.has_word(session, self.alice_id, "budget"))
            self.assertFalse(await words.has_word(session, self.bob_id, "budget"))

    async def test_migrated_local_word_keeps_its_original_timestamp(self):
        async with self.sessions() as session:
            # 2024-01-01T00:00:00Z — the phone migrating its old localStorage list.
            saved = await words.save_word(
                session, self.alice_id, word="legacy", added_at=1704067200000
            )
            self.assertEqual(saved["addedAt"], 1704067200000)

            # Newest first: a word added now must sort above the migrated one.
            await words.save_word(session, self.alice_id, word="fresh")
            listed = await words.list_words(session, self.alice_id)
            self.assertEqual([item["key"] for item in listed["items"]], ["fresh", "legacy"])

    async def test_delete_only_touches_the_callers_row(self):
        async with self.sessions() as session:
            await words.save_word(session, self.alice_id, word="budget")
            await words.save_word(session, self.bob_id, word="budget")

            removed = await words.remove_word(session, self.alice_id, "Budget")
            self.assertTrue(removed["removed"])
            self.assertEqual((await words.list_words(session, self.alice_id))["total"], 0)
            # Bob still has his own row.
            self.assertEqual((await words.list_words(session, self.bob_id))["total"], 1)

            again = await words.remove_word(session, self.alice_id, "budget")
            self.assertFalse(again["removed"])

    async def test_unknown_lesson_does_not_block_saving(self):
        async with self.sessions() as session:
            saved = await words.save_word(session, self.alice_id, word="orphan", lesson_id=999999)
            self.assertIsNotNone(saved)
            self.assertIsNone(saved["lessonId"])

    async def test_blank_word_is_rejected(self):
        async with self.sessions() as session:
            self.assertIsNone(await words.save_word(session, self.alice_id, word="   "))
            self.assertEqual((await words.list_words(session, self.alice_id))["total"], 0)

    async def test_deleting_the_user_cascades_their_words(self):
        async with self.sessions() as session:
            await words.save_word(session, self.bob_id, word="temp")
            bob = await session.get(User, self.bob_id)
            await session.delete(bob)
            await session.commit()
            remaining = (await session.execute(select(EnglishSavedWord))).scalars().all()
            self.assertEqual(list(remaining), [])


if __name__ == "__main__":
    unittest.main()
