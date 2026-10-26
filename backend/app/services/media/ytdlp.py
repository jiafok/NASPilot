"""yt-dlp / ffmpeg integration layer.

Everything goes through ``asyncio.create_subprocess_exec`` — no browser
automation, no shell interpolation.

Responsibilities
----------------
* locate ``yt-dlp`` (binary or ``python -m yt_dlp``) and ``ffmpeg``
* best-effort auto install of ``yt-dlp`` on first start (Docker image ships it)
* enumerate a channel / playlist as JSON
* download one video while streaming live progress
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

logger = logging.getLogger("naspilot.youtube.ytdlp")

MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_OUTPUT_LINES = 400
DEFAULT_DOWNLOAD_TIMEOUT = 7200  # 2h hard cap per item

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


# ── Enumeration ──────────────────────────────────────────────────────────

#: env vars checked (in order) when YOUTUBE_PROXY is not set
_PROXY_ENV_VARS = ("YOUTUBE_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")


def proxy_url() -> str | None:
    """Resolve the proxy for yt-dlp / network calls (``None`` = direct).

    Priority: ``YOUTUBE_PROXY`` setting, then the standard proxy env vars, so
    a container started with ``-e HTTPS_PROXY=...`` just works.
    """
    explicit = (settings.YOUTUBE_PROXY or "").strip()
    if explicit:
        return explicit
    for name in _PROXY_ENV_VARS[1:]:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


#: hosts probed by :func:`check_network` — one is the API, one is the CDN
_PROBE_HOSTS = (
    ("youtube_api", "https://www.youtube.com/"),
    ("youtube_cdn", "https://redirector.googlevideo.com/"),
)


async def _http_probe(url: str, timeout: int = 12) -> int | None:
    """Return the HTTP status for ``url`` (``None`` when the connection failed)."""
    cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", str(timeout)]
    proxy = proxy_url()
    if proxy:
        cmd += ["--proxy", proxy]
    cmd.append(url)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout + 5)
    except (FileNotFoundError, asyncio.TimeoutError, Exception):
        return None
    text = out.decode("utf-8", errors="replace").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


async def check_network() -> dict[str, Any]:
    """Probe YouTube API + CDN reachability through the configured proxy.

    Enumeration only needs the API host; the actual download needs the CDN
    (``googlevideo.com``), which is the part usually blocked.
    """
    results: dict[str, Any] = {
        "proxy": proxy_url(),
        "checks": {},
        "ok": False,
        "detail": None,
    }
    for name, url in _PROBE_HOSTS:
        status = await _http_probe(url)
        results["checks"][name] = {"url": url, "http_status": status, "reachable": status is not None}

    api_ok = results["checks"]["youtube_api"]["reachable"]
    cdn_ok = results["checks"]["youtube_cdn"]["reachable"]
    results["ok"] = bool(api_ok and cdn_ok)
    if results["ok"]:
        results["detail"] = "可以直连 YouTube API 与视频 CDN"
    elif api_ok and not cdn_ok:
        results["detail"] = (
            "只能访问 YouTube API，无法访问视频 CDN（googlevideo.com）："
            "能列出视频清单但下载会失败，请配置 YOUTUBE_PROXY"
        )
    elif not api_ok:
        results["detail"] = "无法访问 YouTube，请检查网络或配置 YOUTUBE_PROXY"
    return results


def _common_args() -> list[str]:
    args = ["--no-warnings", "--no-color", "--ignore-config"]
    if settings.YOUTUBE_COOKIES_FILE and os.path.isfile(settings.YOUTUBE_COOKIES_FILE):
        args += ["--cookies", settings.YOUTUBE_COOKIES_FILE]
    proxy = proxy_url()
    if proxy:
        args += ["--proxy", proxy]
    if settings.YOUTUBE_JS_RUNTIMES:
        args += ["--js-runtimes", settings.YOUTUBE_JS_RUNTIMES]
    return args


_NETWORK_HINTS: tuple[tuple[str, str], ...] = (
    ("ssl", "SSL/TLS 握手被中断，当前网络无法直连 YouTube"),
    ("unexpected_eof", "SSL/TLS 握手被中断，当前网络无法直连 YouTube"),
    ("timed out", "连接 YouTube 超时"),
    ("timeout", "连接 YouTube 超时"),
    ("connection reset", "与 YouTube 的连接被重置"),
    ("unable to download api page", "无法访问 YouTube 页面"),
    ("unable to download webpage", "无法访问 YouTube 页面"),
    ("http error 429", "被 YouTube 限流（429），建议稍后重试或配置 cookies"),
    ("sign in to confirm", "YouTube 要求登录验证，请配置 YOUTUBE_COOKIES_FILE"),
    ("private video", "视频为私有"),
)


def network_hint(err: str | None) -> str | None:
    """Return a human hint when the failure looks like a network/YouTube block."""
    lowered = (err or "").lower()
    for needle, hint in _NETWORK_HINTS:
        if needle in lowered:
            return hint
    return None


def enumeration_error_message(err: str) -> str:
    """Build an actionable message for a failed enumeration."""
    tail = (err or "").strip()[-600:] or "yt-dlp 未返回数据"
    hint = network_hint(err)
    if hint:
        return (
            f"{hint}。若该网络无法直连 YouTube，请设置 YOUTUBE_PROXY"
            f"（例如 http://192.168.1.10:7890）。yt-dlp: {tail}"
        )
    return f"yt-dlp 枚举失败: {tail}"


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


async def dump_playlist_json(url: str, limit: int | None = None, timeout: int = 180) -> dict[str, Any]:
    """Run ``yt-dlp --flat-playlist --dump-single-json`` and parse the result."""
    cmd = [
        *ytdlp_command(),
        *_common_args(),
        "--flat-playlist",
        "--dump-single-json",
        "--skip-download",
        "--extractor-retries",
        "3",
    ]
    if limit and limit > 0:
        cmd += ["--playlist-end", str(limit)]
    cmd.append(url)

    logger.info("Enumerating media: %s", url)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("yt-dlp 未安装，请检查 Docker 镜像或启用自动安装") from exc

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"列出订阅内容超时（>{timeout}s）")

    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace").strip()
    if len(out) > MAX_JSON_BYTES:
        raise RuntimeError("订阅内容过大，无法解析")
    if not out.strip():
        raise RuntimeError(enumeration_error_message(err))
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"无法解析 yt-dlp 输出: {exc}") from exc
    if not isinstance(data, dict):
        # yt-dlp prints a bare ``null`` when the extractor fails.
        raise RuntimeError(enumeration_error_message(err))
    return data


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

def build_download_args(
    url: str,
    outtmpl: str,
    *,
    media_mode: str = "video",
    video_height: int | None = None,
    audio_format: str | None = None,
    print_file: str | None = None,
) -> list[str]:
    """Build the yt-dlp argv for one video.

    ``media_mode``:
      video → MP4 (height cap = ``video_height``, merged)
      audio → audio only (``-x --audio-format <audio_format>``)
      both  → MP4 only here; the provider derives the audio file afterwards
              with :func:`extract_audio` (yt-dlp cannot emit both in one run).

    ``audio_format``: ``mp3`` (lossy re-encode), ``best`` (keep the original
    stream, i.e. zero-loss remux), or an explicit container
    (``opus`` / ``m4a`` / ``flac`` / ``wav``).
    """
    height = video_height if video_height is not None else int(settings.YOUTUBE_MAX_HEIGHT or 0)
    afmt = (audio_format or settings.YOUTUBE_AUDIO_FORMAT or "mp3").strip().lower()
    args: list[str] = [
        *ytdlp_command(),
        *_common_args(),
        "--newline",
        "--no-quiet",
        "--no-simulate",
        "--no-playlist",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        # subtitles (auto generated, English variants) → .srt
        "--write-auto-sub",
        "--sub-langs",
        settings.YOUTUBE_SUB_LANGS,
        "--convert-subs",
        "srt",
        # cover art → .jpg
        "--write-thumbnail",
        "--convert-thumbnails",
        "jpg",
        # Keep the original title as the file name; only extremely long titles
        # are trimmed so NAS file systems never reject the name.
        "--trim-filenames",
        "180",
    ]

    if media_mode == "audio":
        args += [
            # Audio-only downloads should not pull the video stream.
            "-f",
            "bestaudio/best",
            "-x",
            "--audio-format",
            afmt,
        ]
        # ``--audio-quality`` only affects lossy codecs; ignored when yt-dlp
        # stream-copies (``best`` / matching codec).
        if settings.YOUTUBE_AUDIO_QUALITY:
            args += ["--audio-quality", str(settings.YOUTUBE_AUDIO_QUALITY)]
    else:
        if height > 0:
            selector = (
                f"bestvideo[height<={height}]+bestaudio/"
                f"best[height<={height}]/best"
            )
        else:
            selector = "bestvideo+bestaudio/best"
        args += ["-f", selector, "--merge-output-format", "mp4"]

    args += ["-o", outtmpl]
    if print_file:
        args += ["--print-to-file", "after_move:filepath", print_file]
    args.append(url)
    return args


async def download(
    url: str,
    outtmpl: str,
    *,
    media_mode: str = "video",
    video_height: int | None = None,
    audio_format: str | None = None,
    on_line: Any | None = None,
    timeout: int = DEFAULT_DOWNLOAD_TIMEOUT,
) -> tuple[int, str, list[str]]:
    """Download one video, streaming output lines to ``on_line``.

    Returns ``(exit_code, tail_output, produced_files)``.
    """
    tmp_path: str | None = None
    fd, tmp_path = tempfile.mkstemp(prefix="ytdlp-out-", suffix=".txt", dir=str(settings.DATA_DIR))
    os.close(fd)
    args = build_download_args(
        url,
        outtmpl,
        media_mode=media_mode,
        video_height=video_height,
        audio_format=audio_format,
        print_file=tmp_path,
    )

    logger.info("Downloading: %s -> %s", url, outtmpl)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return 127, "yt-dlp 未安装或不可执行", []

    tail: list[str] = []

    async def _pump() -> None:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue
            tail.append(line)
            if len(tail) > MAX_OUTPUT_LINES:
                del tail[: len(tail) - MAX_OUTPUT_LINES]
            if on_line is not None:
                try:
                    await on_line(line)
                except Exception:  # progress callback must never break download
                    logger.debug("progress callback failed", exc_info=True)

    try:
        await asyncio.wait_for(asyncio.gather(_pump(), proc.wait()), timeout=timeout)
        code = proc.returncode or 0
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        code = 124
        tail.append(f"下载超时（>{timeout}s）")

    produced: list[str] = []
    if tmp_path:
        try:
            if os.path.exists(tmp_path):
                with open(tmp_path, encoding="utf-8", errors="replace") as fh:
                    produced = [ln.strip() for ln in fh if ln.strip()]
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return code, "\n".join(tail[-60:]), produced


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


async def extract_mp3(src: str, dst: str | None = None, timeout: int = 1800) -> tuple[int, str]:
    """Backward-compatible wrapper: derive an MP3 next to ``src``."""
    code, msg, produced = await extract_audio(src, "mp3", timeout=timeout)
    if code == 0 and dst and produced and produced != dst:
        try:
            os.replace(produced, dst)
            produced = dst
        except OSError:
            pass
    return code, msg
