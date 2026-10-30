"""合集/多P 下载不能互相覆盖。

报告："一个合集 20 个，全部显示成功，实际只有一个，这一个也播放不了"。

真实原因（逐层验证过）：

1. 分P 的 `%(title)s` 是"整个合集标题 + pNN"，中文每字 3 字节，255 字节的
   文件系统上限早已超出；被截断的正好是结尾的 ` pNN`。
2. 于是 20 个分P渲染出**同一个路径**，后下载的覆盖前一个；每次 yt-dlp 都
   退出 0，所以 UI 全部显示成功。
3. 并发（默认 2）同时写同一路径，留下的是被两个进程搅过的损坏文件 ——
   就是"播放不了"的那个。

修复：可能撞名的条目把 id 放进文件名，并在模板里按字节预算截断标题。
这里的测试锁定命名规则本身，不需要网络。
"""

import os
import re
import unittest
from types import SimpleNamespace
from typing import Any

from app.services.media_center.paths import (
    PLAIN_TITLE_CHARS,
    UNIQUE_TITLE_CHARS,
    output_template,
)
from app.services.media_center.providers import BilibiliProvider, YoutubeProvider

#: 255 bytes is the limit on ext4 / NTFS / most NAS shares.
MAX_NAME_BYTES = 255

#: A realistic collection title: 76 Chinese characters = 228 bytes on its own.
COLLECTION_TITLE = (
    "完整全集【初中英语·听力磨耳朵】高频听力9000句 中英发音 "
    "油管爆火听力磨耳朵训练视频 日积月累坚持 听力能力飞速提升（附完整资料）"
)


def render(template: str, *, title: str, media_id: str, ext: str = "mp4") -> str:
    """Approximate yt-dlp's rendering of the fields the app actually uses."""
    out = template
    out = re.sub(r"%\(title\)\.(\d+)s", lambda m: title[: int(m.group(1))], out)
    out = out.replace("%(title)s", title)
    out = out.replace("%(id)s", media_id)
    return out.replace("%(ext)s", ext)


def subscription(**overrides: Any):
    base = dict(
        name="听力9000句",
        type="video",
        media_mode="video",
        audio_only=False,
        video_height=360,
        audio_format="mp3",
        number_prefix=False,
        download_path="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class UniqueNameTests(unittest.TestCase):
    def test_multipart_parts_are_recognised(self):
        provider = BilibiliProvider(subscription())
        item = SimpleNamespace(
            media_id="BV1XWa36MEkP_p7",
            url="https://www.bilibili.com/video/BV1XWa36MEkP?p=7",
        )
        self.assertTrue(provider._needs_unique_name(item))

    def test_a_part_number_in_the_url_alone_is_enough(self):
        provider = BilibiliProvider(subscription())
        item = SimpleNamespace(media_id="whatever", url="https://host/v/abc?p=9")
        self.assertTrue(provider._needs_unique_name(item))

    def test_playlist_and_channel_always_get_unique_names(self):
        item = SimpleNamespace(media_id="abc123", url="https://host/v/abc123")
        for kind in ("playlist", "channel"):
            provider = BilibiliProvider(subscription(type=kind))
            self.assertTrue(provider._needs_unique_name(item), kind)

    def test_a_single_video_keeps_its_plain_name(self):
        # Regression guard: the id must NOT leak into ordinary downloads.
        provider = YoutubeProvider(subscription())
        item = SimpleNamespace(
            media_id="dQw4w9WgXcQ", url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        self.assertFalse(provider._needs_unique_name(item))

    def test_every_part_of_a_collection_renders_a_distinct_name(self):
        sub = subscription()
        provider = BilibiliProvider(sub)
        names = []
        for page in range(1, 21):
            media_id = f"BV1XWa36MEkP_p{page}"
            item = SimpleNamespace(
                media_id=media_id, url=f"https://www.bilibili.com/video/BV1XWa36MEkP?p={page}"
            )
            template = output_template(
                sub, "bilibili", "chan", "playlist", index=page,
                unique_name=provider._needs_unique_name(item),
            )
            rendered = render(template, title=COLLECTION_TITLE, media_id=media_id)
            names.append(os.path.basename(rendered))

        self.assertEqual(len(set(names)), 20, "20 parts must produce 20 file names")
        for name in names:
            self.assertLessEqual(
                len(name.encode("utf-8")), MAX_NAME_BYTES, f"too long for the filesystem: {name}"
            )

    def test_the_part_number_survives_truncation(self):
        # The whole bug: the collection title is long enough that ` pNN` was
        # being truncated away, leaving every part with an identical name.
        sub = subscription()
        template = output_template(
            sub, "bilibili", "chan", "playlist", index=3, unique_name=True
        )
        rendered = render(template, title=COLLECTION_TITLE + " p03", media_id="BV1XWa36MEkP_p3")
        name = os.path.basename(rendered)
        self.assertIn("[BV1XWa36MEkP_p3]", name)
        self.assertLessEqual(len(name.encode("utf-8")), MAX_NAME_BYTES)

    def test_plain_names_are_still_byte_safe(self):
        sub = subscription()
        template = output_template(sub, "youtube", "chan", None, index=None, unique_name=False)
        rendered = render(template, title=COLLECTION_TITLE, media_id="dQw4w9WgXcQ")
        name = os.path.basename(rendered)
        self.assertLessEqual(len(name.encode("utf-8")), MAX_NAME_BYTES)
        self.assertNotIn("[", name, "ordinary downloads keep the plain name")

    def test_both_title_budgets_stay_under_the_byte_ceiling(self):
        # 3 bytes per Chinese character, plus room for " [BV1XWa36MEkP_p20].mp4"
        self.assertLess(UNIQUE_TITLE_CHARS * 3 + 23, MAX_NAME_BYTES)
        self.assertLess(PLAIN_TITLE_CHARS * 3 + 4, MAX_NAME_BYTES)


class TrimSafetyTests(unittest.TestCase):
    def test_the_yt_dlp_trim_cannot_cut_the_id_off(self):
        # `--trim-filenames` is a last-resort cap. It once truncated the rendered
        # name (82 chars -> 44 at a value of 85) and removed the very id that
        # kept the parts apart, so it must sit far above the template budget.
        from app.services.media_center import engine
        from app.services.media_center.base import DownloadOptions

        args = engine.build_download_args(
            "https://example.com/v", "%(title)s.%(ext)s", DownloadOptions()
        )
        trim = int(args[args.index("--trim-filenames") + 1])
        budget = UNIQUE_TITLE_CHARS + len(" [BV1XWa36MEkP_p20]") + len(".mp4")
        self.assertGreater(trim, budget, "the trim would eat the id again")


if __name__ == "__main__":
    unittest.main()
