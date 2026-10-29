"""H.264/AAC fallback stream for lessons the phone cannot decode.

The library is **AV1 video + Opus audio**. A desktop Chromium decodes AV1 in
software, so the same lesson plays fine on a laptop — but Android WebView cannot
decode AV1 on most devices, and Opus inside MP4 is equally patchy. What the
learner sees is "花屏": green/garbled blocks, or a black picture with sound, and
often no `error` event at all (the stall notice in the player catches that case).

The honest fix is a one-off transcode into H.264 + AAC:

* the output is cached under ``DATA_DIR/transcode`` (a directory the Docker
  backup plugin already excludes), so the second viewer of a lesson gets an
  instant stream and only the first one waits;
* the cache entry records the source's size + mtime, so re-downloading a lesson
  transparently invalidates its transcode;
* job state lives in-process. A restart mid-transcode leaves a ``.part`` file
  which the next run overwrites; the client simply starts the job again.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger("naspilot.english.compat")

#: Video codecs an Android WebView can be relied on to decode.
SAFE_VIDEO = {"h264", "vp8", "vp9"}
#: Audio codecs that play everywhere; Opus-in-MP4 is the common failure.
SAFE_AUDIO = {"aac", "mp3"}

_PART_SUFFIX = ".part"

#: ffprobe results, keyed by path + signature, so probing is done once.
_probe_cache: dict[str, dict[str, Any]] = {}
#: Transcode jobs, keyed by the destination cache path.
_jobs: dict[str, dict[str, Any]] = {}


# ══════════════════════════════════════════════════════════════════════
#  Paths & probing
# ══════════════════════════════════════════════════════════════════════


def cache_dir() -> Path:
    path = Path(settings.DATA_DIR) / "transcode"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_path(lesson_id: int) -> Path:
    return cache_dir() / f"lesson-{lesson_id}.mp4"


def _signature(src: Path) -> str:
    stat = src.stat()
    return f"{stat.st_size}-{stat.st_mtime_ns}"


def _signature_file(dest: Path) -> Path:
    return dest.with_name(dest.name + ".src")


def tools_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


async def _run(cmd: list[str], *, timeout: float) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "timeout"
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace")


async def probe(src: Path) -> dict[str, Any]:
    """Return ``{video, audio, duration, probe_error}`` for a media file."""
    try:
        key = f"{src}:{_signature(src)}"
    except OSError as exc:
        return {"video": "", "audio": "", "duration": 0.0, "probe_error": str(exc)}

    cached = _probe_cache.get(key)
    if cached is not None:
        return cached

    info: dict[str, Any] = {"video": "", "audio": "", "duration": 0.0, "probe_error": ""}
    if not tools_available():
        info["probe_error"] = "ffprobe 未安装"
        _probe_cache[key] = info
        return info

    code, out = await _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(src),
        ],
        timeout=45,
    )
    if code != 0:
        info["probe_error"] = out[-300:] or f"ffprobe 退出码 {code}"
        _probe_cache[key] = info
        return info

    try:
        data = json.loads(out)
    except ValueError:
        info["probe_error"] = "ffprobe 输出无法解析"
        _probe_cache[key] = info
        return info

    for stream in data.get("streams") or []:
        kind = stream.get("codec_type")
        if kind == "video" and not info["video"]:
            info["video"] = str(stream.get("codec_name") or "")
        elif kind == "audio" and not info["audio"]:
            info["audio"] = str(stream.get("codec_name") or "")
    try:
        info["duration"] = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        info["duration"] = 0.0

    _probe_cache[key] = info
    return info


def needs_compat(info: dict[str, Any]) -> bool:
    """True when a mainstream Android WebView is likely to fail on this file."""
    video = (info.get("video") or "").lower()
    audio = (info.get("audio") or "").lower()
    if video and video not in SAFE_VIDEO:
        return True
    if audio and audio not in SAFE_AUDIO:
        return True
    return False


def _cache_is_current(dest: Path, src: Path) -> bool:
    if not dest.is_file():
        return False
    sig = _signature_file(dest)
    if not sig.is_file():
        return False
    try:
        return sig.read_bytes().decode("utf-8").strip() == _signature(src)
    except OSError:
        return False


# ══════════════════════════════════════════════════════════════════════
#  Jobs
# ══════════════════════════════════════════════════════════════════════


async def status(lesson_id: int, src: Path) -> dict[str, Any]:
    """Everything the client needs to decide what to play.

    ``state`` is one of ``not-needed`` (the phone can decode it as-is),
    ``idle`` / ``running`` / ``ready`` / ``failed`` / ``unavailable``.
    """
    info = await probe(src)
    dest = cache_path(lesson_id)

    payload: dict[str, Any] = {
        "lesson_id": lesson_id,
        "video_codec": info["video"],
        "audio_codec": info["audio"],
        "duration": info["duration"],
        "needs_transcode": needs_compat(info),
        "tools_available": tools_available(),
        "state": "idle",
        "progress": 0,
        "error": info["probe_error"],
    }

    if not payload["needs_transcode"]:
        payload["state"] = "not-needed"
        payload["progress"] = 100
        return payload

    if _cache_is_current(dest, src):
        payload.update(state="ready", progress=100, error="")
        return payload

    if not tools_available():
        payload.update(state="unavailable", error="服务器未安装 ffmpeg，无法生成兼容视频")
        return payload

    job = _jobs.get(str(dest))
    if job:
        payload.update(
            state=job["state"], progress=job["progress"], error=job.get("error") or ""
        )
        return payload

    return payload


async def start(lesson_id: int, src: Path) -> dict[str, Any]:
    """Begin (or join) the transcode job for this lesson."""
    info = await probe(src)
    dest = cache_path(lesson_id)

    if not needs_compat(info) or _cache_is_current(dest, src):
        return await status(lesson_id, src)

    if not tools_available():
        return await status(lesson_id, src)

    job = _jobs.get(str(dest))
    if job and job["state"] in {"running", "ready"}:
        return await status(lesson_id, src)

    _jobs[str(dest)] = {"state": "running", "progress": 0, "error": ""}
    asyncio.create_task(_transcode(lesson_id, src, dest))
    # Let the task set its first progress line before the client reads status.
    await asyncio.sleep(0)
    return await status(lesson_id, src)


async def _transcode(lesson_id: int, src: Path, dest: Path) -> None:
    job = _jobs[str(dest)]
    part = dest.with_name(dest.name + _PART_SUFFIX)
    duration = float((await probe(src)).get("duration") or 0)

    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-i",
        str(src),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        # 540p cap: the phone cannot show more, and every dropped row of
        # pixels is encode time the learner waits for. `min()` keeps a small
        # source from being upscaled.
        "-vf",
        "scale=-2:'min(540,ih)'",
        # Baseline-compatible H.264 + AAC: decodable by every Android WebView.
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "main",
        "-level",
        "4.0",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        # The temp name ends in `.part`, so the container cannot be inferred
        # from the extension — without this ffmpeg refuses to start (exit 234).
        "-f",
        "mp4",
        str(part),
    ]

    logger.info("compat transcode started for lesson %s", lesson_id)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if proc.stdout is not None:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if text.startswith("out_time_ms=") and duration > 0:
                    try:
                        seconds = int(text.split("=", 1)[1]) / 1_000_000
                    except (TypeError, ValueError):
                        continue
                    job["progress"] = max(0, min(99, int(seconds / duration * 100)))
        code = await proc.wait()
        if code != 0 or not part.is_file():
            job.update(state="failed", error=f"转码失败（ffmpeg 退出码 {code}）")
            part.unlink(missing_ok=True)
            logger.warning("compat transcode failed for lesson %s (exit %s)", lesson_id, code)
            return

        part.replace(dest)
        try:
            _signature_file(dest).write_bytes(_signature(src).encode("utf-8"))
        except OSError:
            # The cache still works, it just re-transcodes once more later.
            logger.warning("could not write compat cache signature for lesson %s", lesson_id)
        job.update(state="ready", progress=100, error="")
        logger.info("compat transcode ready for lesson %s", lesson_id)
    except asyncio.CancelledError:  # pragma: no cover - shutdown
        job.update(state="idle", error="")
        raise
    except Exception as exc:  # noqa: BLE001 - never crash the server task
        logger.exception("compat transcode crashed for lesson %s", lesson_id)
        job.update(state="failed", error=str(exc))


def forget(lesson_id: int) -> None:
    """Drop the cache entry and any job for a lesson (used by admin rescan)."""
    dest = cache_path(lesson_id)
    _jobs.pop(str(dest), None)
    dest.unlink(missing_ok=True)
    _signature_file(dest).unlink(missing_ok=True)
    dest.with_name(dest.name + _PART_SUFFIX).unlink(missing_ok=True)


__all__ = [
    "SAFE_AUDIO",
    "SAFE_VIDEO",
    "cache_dir",
    "cache_path",
    "forget",
    "needs_compat",
    "probe",
    "start",
    "status",
    "tools_available",
]
