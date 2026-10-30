"""下载产出的字幕/配图必须能被英语课程识别（离线）。

用户要求：不管从哪里下载，都默认下载字幕和配图。

这些测试锁定下载侧写出的文件名，能被英语扫描器正确绑定到同一节课：

* yt-dlp 写出的字幕名带语言后缀 —— `<标题> [id].zh-Hans.srt`
* 下载选项默认开启 `write_subs` / `write_thumbnail`
* 字幕语言默认覆盖英文、中文和 B 站 AI 轨

如果不满足，课程会变成"没有字幕的一节课"：不能点词查义、没有整句翻译。
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.english.scanner import collect_assets, split_language
from app.services.media_center import engine
from app.services.media_center.base import DownloadOptions


class SubtitleLanguageTests(unittest.TestCase):
    def test_language_tag_is_split_off_the_stem(self):
        self.assertEqual(split_language("Episode.en-US"), ("Episode", "en-US"))
        self.assertEqual(split_language("Episode"), ("Episode", None))

    def test_bilibili_and_youtube_tags_are_recognised(self):
        # Bilibili CC + AI track, YouTube's regional English variants.
        for stem, lang in [
            ("标题 [BV1XWa36MEkP_p3].zh-Hans", "zh-Hans"),
            ("标题 [BV1XWa36MEkP_p3].ai-zh", "ai-zh"),
            ("Title [abc123].en", "en"),
            ("Title [abc123].en-US", "en-US"),
        ]:
            base, parsed = split_language(stem)
            self.assertEqual(parsed, lang, stem)
            self.assertIn("标题" if stem.startswith("标题") else "Title", base)


class AssetBindingTests(unittest.TestCase):
    def test_downloaded_subtitle_and_thumbnail_bind_to_the_lesson(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stem = "完整全集 p01 [BV1XWa36MEkP_p1]"
            # Exactly what yt-dlp writes for the app's output template.
            (root / f"{stem}.mp4").write_bytes(b"")
            (root / f"{stem}.zh-Hans.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
            (root / f"{stem}.jpg").write_bytes(b"")

            assets = collect_assets(root)
            buckets = assets[root]
            self.assertIn(stem, buckets)
            asset = buckets[stem]
            self.assertEqual(asset.media, root / f"{stem}.mp4")
            self.assertEqual(asset.subtitle, root / f"{stem}.zh-Hans.srt")
            self.assertEqual(asset.subtitle_lang, "zh-Hans")
            self.assertEqual(asset.cover, root / f"{stem}.jpg")

    def test_thumbnail_that_does_not_share_the_stem_still_becomes_a_cover(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Episode.mp4").write_bytes(b"")
            (root / "folder.jpg").write_bytes(b"")
            asset = collect_assets(root)[root]["Episode"]
            self.assertIsNotNone(asset.cover, "a lone directory image should back the lesson")


class DownloadDefaultsTests(unittest.TestCase):
    def test_subtitles_and_thumbnails_are_on_by_default(self):
        options = DownloadOptions()
        self.assertTrue(options.write_subs)
        self.assertTrue(options.write_thumbnail)

    def test_default_languages_cover_english_and_chinese(self):
        langs = DownloadOptions().sub_langs.split(",")
        # Exact codes only: yt-dlp reads each entry as a regex, so a wildcard
        # like `en.*` also pulls every auto-translated track (en-de, en-fr, …)
        # and YouTube answers that flood with HTTP 429.
        for code in ("en", "en-US", "en-GB", "zh", "zh-Hans", "ai-zh"):
            self.assertIn(code, langs, f"{code} missing from {langs}")
        self.assertFalse([c for c in langs if "*" in c], f"wildcards in {langs}")

    def test_argv_requests_manual_and_automatic_subtitles(self):
        # Regression guard: only passing `--write-auto-sub` meant manually
        # uploaded captions (Bilibili CC, most YouTube captions) never downloaded.
        args = engine.build_download_args(
            "https://example.com/v",
            "%(title)s.%(ext)s",
            DownloadOptions(),
        )
        self.assertIn("--write-subs", args)
        self.assertIn("--write-auto-subs", args)
        self.assertIn("--write-thumbnail", args)
        self.assertEqual(args[args.index("--convert-subs") + 1], "srt")

    def test_language_flag_comes_from_the_options(self):
        args = engine.build_download_args(
            "https://example.com/v", "%(title)s.%(ext)s", DownloadOptions()
        )
        self.assertEqual(args[args.index("--sub-langs") + 1], DownloadOptions().sub_langs)


if __name__ == "__main__":
    unittest.main()
