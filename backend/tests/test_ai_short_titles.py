"""下载文件名用 AI 摘要：8 个字以内。

真实标题没法当文件名：B 站合集的分P 名字是"整个合集标题 + pNN + 分P标题"，
落盘 186 字节，还会变成英语库里的课时名。

这些测试锁定四件事：

* 结果总在 8 个字符以内（含 `01 ` 这样的分P 前缀）；
* 同一合集的不同分P 不会得到同一个名字（否则又会互相覆盖）；
* AI 不可用时中文标题仍会被本地兜底缩短，英文标题保持原样（不截成
  `5Everyda` 这种碎片）；
* 文件名里的 `%` 不会被 yt-dlp 当成模板字段。

不联网：AI 用注入的假实现。
"""

import asyncio
import os
import unittest
from types import SimpleNamespace
from typing import Awaitable, Callable

from app.services.media_center import titles
from app.services.media_center.paths import output_template

COLLECTION = (
    "完整全集【初中英语·听力磨耳朵】高频听力9000句 中英发音 油管爆火听力磨耳朵训练视频 "
    "日积月累坚持 听力能力飞速提升（附完整资料）"
)
PART_1 = f"{COLLECTION} p01 1_听力9000句-第1部分 初级听力短句 1"
PART_7 = f"{COLLECTION} p07 第7部分 中级听力"
ENGLISH = "How To Be Strong When Life Is Crazy | English Podcast"


def stub(answer: str) -> Callable[[str, int], Awaitable[str]]:
    """A summariser that behaves like a chatty model."""

    async def _go(_text: str, _budget: int) -> str:
        return answer

    return _go


def offline() -> Callable[[str, int], Awaitable[str]]:
    async def _go(_text: str, _budget: int) -> str:
        raise RuntimeError("AI unavailable")

    return _go


async def shorten(title: str, *, index: int | None = None,
                  answer: str | None = None, max_chars: int = 8) -> str:
    titles.clear_cache()
    return await titles.short_title(
        title,
        index=index,
        max_chars=max_chars,
        summarizer=stub(answer) if answer is not None else offline(),
    )


class ShortTitleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        titles.clear_cache()

    async def test_result_fits_the_eight_character_budget(self):
        short = await shorten(PART_1, index=1, answer="初级听力")
        self.assertEqual(short, "01 初级听力")
        self.assertLessEqual(len(short), 8)

    async def test_parts_of_one_collection_get_different_names(self):
        # The collision bug: identical names let each part overwrite the last.
        first = await shorten(PART_1, index=1, answer="初级听力")
        seventh = await shorten(PART_7, index=7, answer="中级听力")
        self.assertNotEqual(first, seventh)
        self.assertEqual((first, seventh), ("01 初级听力", "07 中级听力"))

    async def test_the_summariser_sees_only_the_part_specific_text(self):
        # Summarising the shared collection title made every part identical.
        seen: list[str] = []

        async def capture(text: str, _budget: int) -> str:
            seen.append(text)
            return "中级听力"

        await titles.short_title(PART_7, index=7, summarizer=capture)
        self.assertTrue(seen, "the summariser must be called")
        self.assertNotIn("完整全集", seen[0])
        self.assertIn("中级听力", seen[0])

    async def test_model_decorations_are_removed(self):
        for answer in ('“初级听力”', "《初级听力》", "【初级听力】", '"初级听力"', "初级听力。"):
            titles.clear_cache()
            self.assertEqual(
                await shorten(PART_1, index=1, answer=answer), "01 初级听力", answer
            )

    async def test_too_long_model_answer_is_truncated_not_discarded(self):
        # Truncating the model's text beats falling back to the heuristic.
        short = await shorten(PART_1, index=1, answer="初级听力短句练习")
        self.assertLessEqual(len(short), 8)
        self.assertTrue(short.startswith("01 初级"), short)
        self.assertNotIn("短句练习", short, "must be truncated to the budget")

    async def test_an_english_echo_is_rejected(self):
        # A model that answers in English ignored the instruction; the local
        # heuristic must take over rather than the English string being used.
        short = await shorten(PART_1, index=1, answer="Listening Practice")
        self.assertLessEqual(len(short), 8)
        self.assertRegex(short, r"[\u4e00-\u9fff]", "must still be Chinese")
        self.assertNotIn("Listening", short)

    async def test_chinese_title_still_shortens_without_ai(self):
        short = await shorten(PART_1, index=1)
        self.assertTrue(short, "a Chinese title must never come back empty")
        self.assertLessEqual(len(short), 8)
        self.assertTrue(short.startswith("01 "))

    async def test_english_title_is_left_alone_without_ai(self):
        # Measured: the heuristic turned this into `5Everyda`. Keeping the real
        # title is strictly better, and "" tells the caller to do exactly that.
        self.assertEqual(await shorten(ENGLISH), "")

    async def test_leading_numbering_is_not_kept(self):
        short = await shorten("1_听力9000句-第1部分 初级听力短句 1", answer="初级听力")
        self.assertEqual(short, "初级听力")
        self.assertNotRegex(short, r"^\d")


class CacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        titles.clear_cache()

    async def test_a_model_answer_is_cached(self):
        calls = 0

        async def counting(_text: str, _budget: int) -> str:
            nonlocal calls
            calls += 1
            return "初级听力"

        await titles.short_title(PART_1, index=1, summarizer=counting)
        await titles.short_title(PART_1, index=1, summarizer=counting)
        self.assertEqual(calls, 1, "the second call must hit the cache")

    async def test_the_local_fallback_is_not_cached(self):
        # Caching it made a later call that DID reach the AI return the offline
        # answer instead of the model's better one.
        await titles.short_title(PART_1, index=1, summarizer=offline())
        second = await titles.short_title(PART_1, index=1, summarizer=stub("初级听力"))
        self.assertEqual(second, "01 初级听力")


class FileNameTests(unittest.TestCase):
    def subscription(self, **overrides):
        base = dict(
            name="听力9000句", type="video", media_mode="video", audio_only=False,
            number_prefix=False, download_path="/media",
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_the_short_title_replaces_the_template_title(self):
        template = output_template(
            self.subscription(), "bilibili", "chan", "pl",
            index=1, unique_name=True, title_override="01 初级听力",
        )
        self.assertIn("01 初级听力", template)
        self.assertNotIn("%(title)", template)
        self.assertTrue(template.endswith(".%(ext)s"))

    def test_the_id_is_kept_so_parts_cannot_collide(self):
        template = output_template(
            self.subscription(), "bilibili", "chan", "pl",
            index=1, unique_name=True, title_override="01 初级听力",
        )
        self.assertIn("%(id)s", template)

    def test_a_plain_download_has_no_id_noise(self):
        template = output_template(
            self.subscription(), "youtube", "chan", None,
            index=None, unique_name=False, title_override="英语口语练习",
        )
        self.assertIn("英语口语练习", template)
        self.assertNotIn("%(id)s", template)

    def test_a_percent_sign_cannot_become_a_template_field(self):
        # yt-dlp would read `%(id)s` inside the summary as a field.
        template = output_template(
            self.subscription(), "youtube", "chan", None,
            index=None, unique_name=False, title_override="100%搞定%(id)s",
        )
        self.assertNotIn("100%", template)
        self.assertNotIn("%(id)s", template)

    def test_path_separators_are_neutralised(self):
        template = output_template(
            self.subscription(), "youtube", "chan", None,
            index=None, unique_name=False, title_override="a/b:c*d",
        )
        name = os.path.basename(template)
        for bad in ('/', ":", "*"):
            self.assertNotIn(bad, name)

    def test_twenty_parts_produce_twenty_names(self):
        sub = self.subscription()
        names = set()
        for page in range(1, 21):
            template = output_template(
                sub, "bilibili", "chan", "pl", index=page, unique_name=True,
                title_override=f"{page:02d} 初级听力",
            )
            names.add(template)
        self.assertEqual(len(names), 20)


class ConfigTests(unittest.TestCase):
    def test_defaults_are_eight_characters_and_enabled(self):
        from app.core.config import settings

        self.assertTrue(settings.MEDIA_AI_TITLE)
        self.assertEqual(settings.MEDIA_TITLE_MAX_CHARS, 8)


if __name__ == "__main__":
    unittest.main()
