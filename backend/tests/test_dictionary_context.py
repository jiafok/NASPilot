import json
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import EnglishVocabulary, EnglishWordLookup
from app.services.english import dictionary


class DictionaryContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(EnglishWordLookup.__table__.create)
            await connection.run_sync(EnglishVocabulary.__table__.create)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def test_context_cache_and_phrase_validation(self):
        answer = json.dumps({
            "senses": [{"pos": "v.", "meaning": "期待"}],
            "context_meaning": "这里表示期待，而不是朝前看。",
            "phrases": [
                {"phrase": "look forward to", "meaning": "期待", "explanation": "to 后接名词或动名词。"},
                {"phrase": "look after", "meaning": "照顾"},
                {"phrase": "to meeting", "meaning": "不包含目标词"},
            ],
        })
        with patch.object(dictionary.ai_service, "ask", AsyncMock(return_value=answer)) as ask:
            first = await dictionary.lookup(self.db, "look", sentence="I look forward to meeting you.")
            self.assertEqual([item["phrase"] for item in first["phrases"]], ["look forward to"])
            repeated = await dictionary.lookup(self.db, "look", sentence="I look forward to meeting you.")
            self.assertTrue(repeated["cached"])
            self.assertEqual(ask.await_count, 1)
            other = await dictionary.lookup(self.db, "look", sentence="Look at the sky.")
            self.assertEqual(other["phrases"], [])
            self.assertEqual(ask.await_count, 2)

    async def test_builtin_still_gets_context_analysis(self):
        answer = json.dumps({
            "senses": [{"pos": "adv.", "meaning": "真的"}],
            "context_meaning": "强调想念的程度。",
            "phrases": [],
        })
        with patch.object(dictionary.ai_service, "ask", AsyncMock(return_value=answer)) as ask:
            entry = await dictionary.lookup(self.db, "really", sentence="I really miss you.")
        ask.assert_awaited_once()
        self.assertEqual(entry["context_status"], "complete")
        self.assertEqual(entry["context_meaning"], "强调想念的程度。")

    async def test_failed_analysis_keeps_builtin_definition(self):
        with patch.object(dictionary.ai_service, "ask", AsyncMock(side_effect=RuntimeError("offline"))):
            entry = await dictionary.lookup(self.db, "really", sentence="I really miss you.")
        self.assertEqual(entry["context_status"], "unavailable")
        self.assertTrue(entry["senses"])


if __name__ == "__main__":
    unittest.main()