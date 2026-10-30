"""视频内嵌字幕抽取（软字幕）。

用户问题："视频内部有字幕，能抽取出来吗"。

两种"内部有字幕"要分开处理：

* **软字幕**：MP4 的 `mov_text`、MKV 的 `subrip`/`ass` 等真实字幕轨 —— 可以
  精确抽取，时间轴原样保留。这些测试锁定该行为。
* **硬字幕**：烧进画面的像素文字（例如 B 站那节课），必须 OCR，本模块明确
  不处理；这里只保证"位图字幕轨不会被当成文字抽出来"。

ffmpeg/ffprobe 缺失时跳过需要编码的用例，纯逻辑用例始终运行。
"""

import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.services.english import embedded, subtitles

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

SRT_BODY = (
    "1\n00:00:01,000 --> 00:00:03,500\nHello, this is a test subtitle.\n\n"
    "2\n00:00:04,000 --> 00:00:06,200\nSecond line here.\n"
)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe not installed")
class EmbeddedExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_embedded_track_is_extracted_with_exact_timings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "Lesson One.mp4"
            source_srt = root / "source.srt"
            source_srt.write_text(SRT_BODY, encoding="utf-8")

            subprocess.run(
                [
                    "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=8",
                    "-i", str(source_srt),
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:s", "mov_text",
                    "-metadata:s:s:0", "language=eng",
                    "-shortest", str(media),
                ],
                check=True, capture_output=True,
            )

            produced = await embedded.extract_sidecar(media)
            self.assertIsNotNone(produced, "an embedded English track must be extracted")
            # ISO 639-2 `eng` → `en`, so the scanner ranks it as English.
            self.assertEqual(produced.name, "Lesson One.en.srt")

            segments = subtitles.parse_file(produced)
            self.assertEqual([seg.text for seg in segments], [
                "Hello, this is a test subtitle.",
                "Second line here.",
            ])
            self.assertEqual([(seg.start, seg.end) for seg in segments], [(1000, 3500), (4000, 6200)])

    async def test_existing_sidecar_is_never_redone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "Lesson.mp4"
            source_srt = root / "source.srt"
            source_srt.write_text(SRT_BODY, encoding="utf-8")
            subprocess.run(
                [
                    "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=4",
                    "-i", str(source_srt),
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:s", "mov_text", "-shortest", str(media),
                ],
                check=True, capture_output=True,
            )
            (root / "Lesson.srt").write_text(SRT_BODY, encoding="utf-8")

            self.assertIsNone(await embedded.extract_sidecar(media))

    async def test_file_without_subtitle_track_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "Plain.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=3",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    str(media),
                ],
                check=True, capture_output=True,
            )
            self.assertEqual(embedded.probe_tracks(media), [])  # sync helper
            self.assertIsNone(await embedded.extract_sidecar(media))


class TrackSelectionTests(unittest.TestCase):
    def test_english_text_track_wins(self):
        picked = embedded._pick_track([
            {"index": 2, "codec_name": "subrip", "language": "chi"},
            {"index": 3, "codec_name": "subrip", "language": "eng"},
        ])
        self.assertEqual(picked["index"], 3)

    def test_bitmap_tracks_are_never_picked(self):
        # A picture of a subtitle is not a transcript; OCR would be required and
        # this module does not pretend to do it.
        self.assertIsNone(embedded._pick_track([
            {"index": 2, "codec_name": "hdmv_pgs_subtitle", "language": "eng"},
            {"index": 3, "codec_name": "dvd_subtitle", "language": "eng"},
        ]))

    def test_bitmap_is_skipped_even_when_a_text_track_follows(self):
        picked = embedded._pick_track([
            {"index": 2, "codec_name": "hdmv_pgs_subtitle", "language": "eng"},
            {"index": 4, "codec_name": "ass", "language": "chi"},
        ])
        self.assertEqual(picked["index"], 4)

    def test_language_tags_are_normalised(self):
        self.assertEqual(embedded._language_tag({"language": "eng"}), "en")
        self.assertEqual(embedded._language_tag({"language": "chi"}), "zh")
        self.assertEqual(embedded._language_tag({"language": "zh-Hans"}), "zh")
        self.assertEqual(embedded._language_tag({"language": "EN"}), "en")
        self.assertEqual(embedded._language_tag({}), "und")


if __name__ == "__main__":
    unittest.main()
