"""Bilibili 多 P 合集的发现逻辑（不联网）。

真实故障：`--flat-playlist` 对 B 站合集只返回 `{"_type": "url", "url": "...?p=3"}`，
没有 id 也没有标题。旧的 `_to_items` 要求 `id`/`video_id`，于是 **18 个分 P 全被丢弃**，
订阅能添加但永远发现 0 个视频 —— 表现就是"合集订阅怎么都下载不下来"。

这些测试锁定修复后的行为，并保证不会误伤 YouTube 这类本来就带 id 的来源。
"""

import unittest

from app.services.media_center.providers import (
    _derive_media_id,
    _entry_title,
    _sanitise_media_id,
)


class DeriveMediaIdTests(unittest.TestCase):
    def test_bilibili_part_gets_a_stable_id_matching_yt_dlp(self):
        # yt-dlp reports `BV1XWa36MEkP_p3` as the id for this page; the derived
        # value must agree or download history would double-queue every part.
        self.assertEqual(
            _derive_media_id("https://www.bilibili.com/video/BV1XWa36MEkP?p=3"),
            "BV1XWa36MEkP_p3",
        )

    def test_bilibili_without_a_page_number_uses_the_bv_only(self):
        self.assertEqual(
            _derive_media_id("https://www.bilibili.com/video/BV1XWa36MEkP/"),
            "BV1XWa36MEkP",
        )

    def test_youtube_identity_comes_from_the_query_not_the_watch_segment(self):
        # Regression guard: `/watch` is a path segment and must never win over `v`.
        self.assertEqual(
            _derive_media_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_derived_ids_always_satisfy_the_id_shape(self):
        urls = [
            "https://www.bilibili.com/video/BV1XWa36MEkP?p=12",
            "https://example.com/some/path/with space",
            "https://example.com/a/b/",
            "",
        ]
        for url in urls:
            derived = _derive_media_id(url)
            # Only the charset and length matter here: a one-character id is
            # genuinely unusable as an identity, so `_to_items` still drops it.
            self.assertLessEqual(len(derived), 64, f"too long for {url!r}")
            self.assertNotRegex(derived, r"[^\w-]", f"unsafe id for {url!r}")

    def test_every_part_gets_a_distinct_id(self):
        ids = {
            _derive_media_id(f"https://www.bilibili.com/video/BV1XWa36MEkP?p={page}")
            for page in range(1, 19)
        }
        self.assertEqual(len(ids), 18, "each part must be its own row")

    def test_sanitiser_keeps_the_id_shape(self):
        self.assertEqual(_sanitise_media_id("a/b c?d"), "a_b_c_d")
        self.assertLessEqual(len(_sanitise_media_id("x" * 200)), 64)


class EntryTitleTests(unittest.TestCase):
    def test_na_placeholder_is_not_a_title(self):
        self.assertEqual(_entry_title({"title": "NA"}, "BV1XWa36MEkP_p1"), "BV1XWa36MEkP_p1")
        self.assertEqual(_entry_title({}, "BV1XWa36MEkP_p1"), "BV1XWa36MEkP_p1")

    def test_real_title_is_kept(self):
        title = "完整全集【初中英语·听力磨耳朵】p01"
        self.assertEqual(_entry_title({"title": title}, "x"), title)


if __name__ == "__main__":
    unittest.main()
