"""yt-dlp / ffmpeg primitives shared by the media center.

Everything goes through ``asyncio.create_subprocess_exec`` — no browser
automation, no shell interpolation.

Responsibilities
----------------
* locate ``yt-dlp`` (binary or ``python -m yt_dlp``) and ``ffmpeg``
* best-effort auto install of ``yt-dlp`` on first start (Docker image ships it)
* audio extraction / probing (stream copy when the codec already matches)
* progress-line parsing

Enumeration, downloading and per-provider diagnostics live in
:mod:`app.services.media_center.engine`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from typing import Any

from app.core.config import settings

logger = logging.getLogger("naspilot.media.ytdlp")


# ── Locating the binaries ────────────────────────────────────────────────

_cmd_cache: list[str] | None = None


def _module_available() -> bool:
    try:
        import yt_dlp  # noqa: F401

        return True
    except Exception:
        return False


def ytdlp_command(refresh: bool = False) -> list[str]:
    """Return the command prefix used to invoke yt-dlp."""
    global _cmd_cache
    if _cmd_cache is not None and not refresh:
        return list(_cmd_cache)

    binary = shutil.which("yt-dlp")
    if binary:
        _cmd_cache = [binary]
    elif _module_available():
        _cmd_cache = [sys.executable, "-m", "yt_dlp"]
    else:
        # Last resort: let the OS report "not found" with a clear error.
        _cmd_cache = ["yt-dlp"]
    return list(_cmd_cache)


def ytdlp_installed() -> bool:
    return bool(shutil.which("yt-dlp")) or _module_available()


def ffmpeg_installed() -> bool:
    return shutil.which("ffmpeg") is not None


async def _run_capture(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except Exception as exc:  # pragma: no cover - defensive
        return 1, str(exc)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "timeout"
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


async def get_ytdlp_version() -> str | None:
    if not ytdlp_installed():
        return None
    code, out = await _run_capture([*ytdlp_command(), "--version"], timeout=30)
    if code != 0:
        return None
    return out.strip().splitlines()[0].strip() if out.strip() else None


async def get_ffmpeg_version() -> str | None:
    if not ffmpeg_installed():
        return None
    code, out = await _run_capture(["ffmpeg", "-version"], timeout=20)
    if code != 0 or not out.strip():
        return None
    first = out.splitlines()[0]
    match = re.search(r"ffmpeg version (\S+)", first)
    return match.group(1) if match else first.strip()[:120]


async def dependency_health() -> dict[str, Any]:
    """Structured dependency report (used by ``/health/ytdlp``)."""
    yt_installed = ytdlp_installed()
    ff_installed = ffmpeg_installed()
    yt_version = await get_ytdlp_version() if yt_installed else None
    ff_version = await get_ffmpeg_version() if ff_installed else None

    cmd = ytdlp_command() if yt_installed else []
    if yt_installed and ff_installed:
        status = "ok"
        detail = None
    elif yt_installed:
        status = "degraded"
        detail = "ffmpeg 未安装：无法合并音视频、转换字幕/封面、提取 MP3"
    else:
        status = "error"
        detail = "yt-dlp 未安装：无法同步任何订阅"

    return {
        "ytdlp_installed": yt_installed,
        "ytdlp_version": yt_version,
        "ytdlp_path": " ".join(cmd) if cmd else None,
        "ffmpeg_installed": ff_installed,
        "ffmpeg_version": ff_version,
        "status": status,
        "detail": detail,
        "proxy": proxy_url(),
    }


async def ensure_dependencies(auto_install: bool = True) -> dict[str, Any]:
    """Detect (and optionally install) yt-dlp at application start.

    ffmpeg cannot be installed without root/apt from inside the container, so
    it is baked into the Docker image; here we only report it.
    """
    if not ytdlp_installed() and auto_install:
        logger.warning("yt-dlp not found — attempting automatic install via pip")
        try:
            code, out = await _run_capture(
                [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--upgrade", "yt-dlp"],
                timeout=300,
            )
            if code == 0:
                ytdlp_command(refresh=True)
                logger.info("yt-dlp installed successfully")
            else:
                logger.error("yt-dlp auto-install failed (exit %s): %s", code, out[-800:])
        except Exception:
            logger.exception("yt-dlp auto-install crashed")

    # ffmpeg cannot be pip-installed; install it from the distro repo when we
    # have root (dev/mounted-image setups), otherwise just report it.
    if not ffmpeg_installed() and auto_install:
        await _install_ffmpeg()

    health = await dependency_health()
    if health["status"] != "ok":
        logger.warning("Media dependencies degraded: %s", health["detail"])
    else:
        logger.info(
            "Media dependencies ready — yt-dlp %s, ffmpeg %s",
            health["ytdlp_version"],
            health["ffmpeg_version"],
        )
    return health


# ── Proxy ────────────────────────────────────────────────────────────────

#: standard proxy env vars checked (in order) after ``MEDIA_PROXY_URL``
_PROXY_ENV_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")


def proxy_url() -> str | None:
    """Resolve the proxy for yt-dlp / network calls (``None`` = direct).

    Priority: ``MEDIA_PROXY_URL``, then the standard proxy env vars, so a
    container started with ``-e HTTPS_PROXY=...`` just works.
    """
    explicit = (settings.MEDIA_PROXY_URL or "").strip()
    if explicit:
        return explicit
    for name in _PROXY_ENV_VARS:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


#: hosts probed by :func:`check_network` — one is the API, one is the CDN


async def _install_ffmpeg() -> bool:
    """Best-effort ``apt-get install ffmpeg`` when the process runs as root."""
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None or geteuid() != 0:
        logger.info("ffmpeg missing and not running as root — skipping auto-install")
        return False
    if not shutil.which("apt-get"):
        logger.info("ffmpeg missing and apt-get unavailable — skipping auto-install")
        return False

    logger.warning("ffmpeg not found — attempting automatic install via apt-get")
    try:
        code, out = await _run_capture(
            ["bash", "-lc", "apt-get update -qq && apt-get install -y --no-install-recommends ffmpeg"],
            timeout=900,
        )
        if code == 0 and ffmpeg_installed():
            logger.info("ffmpeg installed successfully")
            return True
        logger.error("ffmpeg auto-install failed (exit %s): %s", code, out[-600:])
    except Exception:
        logger.exception("ffmpeg auto-install crashed")
    return False


# ── Progress parsing ─────────────────────────────────────────────────────

_UNIT = {"B": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4}

_PROGRESS_RE = re.compile(
    r"\[download\]\s+(?P<pct>\d+(?:\.\d+)?)%\s+of\s+~?\s*(?P<size>[\d.]+)\s*(?P<unit>[KMGTP]?i?B)",
    re.IGNORECASE,
)
_SPEED_RE = re.compile(r"\bat\s+(?P<speed>[\d.]+)\s*(?P<unit>[KMGTP]?i?B)/s", re.IGNORECASE)
_ETA_RE = re.compile(r"\bETA\s+(?P<eta>[\d:]+)", re.IGNORECASE)


def _to_bytes(value: float, unit: str) -> int:
    return int(value * _UNIT.get(unit.upper(), 1))


def parse_progress_line(line: str) -> dict[str, Any] | None:
    """Parse a yt-dlp download progress line into structured values."""
    match = _PROGRESS_RE.search(line)
    if not match:
        return None
    pct = float(match.group("pct"))
    total = _to_bytes(float(match.group("size")), match.group("unit"))
    speed_match = _SPEED_RE.search(line)
    speed = f"{speed_match.group('speed')}{speed_match.group('unit')}/s" if speed_match else None
    eta_match = _ETA_RE.search(line)
    return {
        "progress": max(0.0, min(100.0, pct)),
        "total_bytes": total,
        "downloaded_bytes": int(total * pct / 100),
        "speed": speed,
        "eta": eta_match.group("eta") if eta_match else None,
    }


_PROCESSING_PREFIXES = (
    "[Merger]",
    "[ExtractAudio]",
    "[VideoConvertor]",
    "[VideoRemuxer]",
    "[FixupM3u8]",
    "[Metadata]",
    "[EmbedThumbnail]",
    "[MoveFiles]",
    "[SubtitlesConvertor]",
    "[ThumbnailsConvertor]",
)


# ── Download ─────────────────────────────────────────────────────────────


def is_processing_line(line: str) -> bool:
    """True when yt-dlp moved from transfer to local post-processing."""
    return line.startswith(_PROCESSING_PREFIXES)


#: container produced for each audio request
AUDIO_EXT = {
    "mp3": ".mp3",
    "opus": ".opus",
    "m4a": ".m4a",
    "flac": ".flac",
    "wav": ".wav",
    "aac": ".m4a",
}
#: source codec → (container ext, whether it can be stream-copied)
_COPY_CONTAINER = {
    "aac": ".m4a",
    "opus": ".opus",
    "vorbis": ".ogg",
    "flac": ".flac",
    "alac": ".m4a",
    "mp3": ".mp3",
}

_FFMPEG_ENCODERS = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "0"],
    "opus": ["-c:a", "libopus", "-b:a", "160k"],
    "m4a": ["-c:a", "aac", "-q:a", "2"],
    "aac": ["-c:a", "aac", "-q:a", "2"],
    "flac": ["-c:a", "flac"],
    "wav": ["-c:a", "pcm_s16le"],
}


async def probe_audio_codec(path: str, timeout: int = 30) -> str | None:
    """Return the first audio stream's codec name (``aac`` / ``opus`` / …)."""
    if not shutil.which("ffprobe"):
        return None
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1", path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (FileNotFoundError, asyncio.TimeoutError, Exception):
        return None
    codec = out.decode("utf-8", errors="replace").strip().splitlines()
    return codec[0].strip().lower() if codec else None


async def extract_audio(src: str, fmt: str = "mp3", timeout: int = 1800) -> tuple[int, str, str | None]:
    """Derive an audio-only file from ``src`` (the merged MP4), same base name.

    ``fmt``:
      * ``best`` → stream-copy into the source codec's native container
        (zero re-encode, byte-identical audio to the MP4)
      * ``mp3`` / ``opus`` / ``m4a`` / ``flac`` / ``wav`` → that container;
        stream-copied when the source codec already matches, transcoded otherwise

    Returns ``(exit_code, message, produced_path)``.
    """
    fmt = (fmt or "mp3").strip().lower()
    if not os.path.isfile(src):
        return 1, f"source file missing: {src}", None
    if not ffmpeg_installed():
        return 127, "ffmpeg 未安装，无法生成音频文件", None

    codec = await probe_audio_codec(src)
    copy_ext = _COPY_CONTAINER.get(codec or "")
    want_copy = fmt == "best" or (fmt in ("mp3", "opus", "m4a", "aac", "flac", "wav")
                                 and AUDIO_EXT.get(fmt) == copy_ext)

    if fmt == "best":
        if copy_ext is None:
            # Unknown codec → fall back to MP3 transcode
            out_ext, codec_args, mode = ".mp3", _FFMPEG_ENCODERS["mp3"], "transcode"
        else:
            out_ext, codec_args, mode = copy_ext, ["-c:a", "copy"], "copy"
    elif want_copy:
        out_ext, codec_args, mode = copy_ext or AUDIO_EXT.get(fmt, ".mp3"), ["-c:a", "copy"], "copy"
    else:
        out_ext, codec_args, mode = AUDIO_EXT.get(fmt, ".mp3"), _FFMPEG_ENCODERS.get(fmt, _FFMPEG_ENCODERS["mp3"]), "transcode"

    dst = os.path.splitext(src)[0] + out_ext
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vn", *codec_args, dst]

    logger.info("Extracting audio (%s, %s/%s): %s -> %s", mode, fmt, codec, src, dst)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError:
        return 127, "ffmpeg 不可执行", None
    except Exception as exc:  # pragma: no cover - defensive
        return 1, str(exc), None

    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"ffmpeg 转码超时（>{timeout}s）", None

    code = proc.returncode or 0
    err = stderr.decode("utf-8", errors="replace").strip()
    if code == 0 and not os.path.isfile(dst):
        return 1, "ffmpeg 报告成功但未生成文件", None
    if code != 0:
        return code, err[-800:], None
    return 0, f"{mode} ({codec} -> {out_ext.lstrip('.')})", dst


