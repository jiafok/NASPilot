"""Download engine — yt-dlp / ffmpeg invocation for the Media Download Center.

Reuses the primitives from ``app.services.media_center.ytdlp_core`` (binary lookup,
progress parsing, audio extraction) but builds its own argument list so every
subscription can carry its own proxy, quality and rate limit.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.services.media_center import ytdlp_core as _yt

logger = logging.getLogger("naspilot.media.engine")

DEFAULT_TIMEOUT = 7200

# exit codes with our own meaning
RC_ABORTED = -3  # paused / cancelled by the user


@dataclass(slots=True)
class EngineResult:
    rc: int
    tail: str
    files: list[str]
    aborted: bool = False


# ══════════════════════════════════════════════════════════════════════
#  Dependency detection
# ══════════════════════════════════════════════════════════════════════


def ytdlp_cmd() -> list[str]:
    return _yt.ytdlp_command()


def ytdlp_installed() -> bool:
    return _yt.ytdlp_installed()


def ffmpeg_installed() -> bool:
    return _yt.ffmpeg_installed()


async def dependency_versions() -> tuple[str | None, str | None]:
    return await _yt.get_ytdlp_version(), await _yt.get_ffmpeg_version()


# ══════════════════════════════════════════════════════════════════════
#  Argument building
# ══════════════════════════════════════════════════════════════════════


def _compatible_format_selector(height: int) -> str:
    """H.264 + AAC first, best-quality as the fallback.

    Why: `bestvideo` is AV1 on YouTube. Android WebView cannot decode AV1, so
    the phone painted garbage frames and the learner had to wait for an on-demand
    H.264 transcode before watching anything. avc1/mp4a downloads play directly
    on every device.

    Every step falls back, so a video with no H.264 rendition still downloads
    instead of failing: progressive avc1 → any avc1 → any video → any format.
    """
    limit = f"[height<={height}]" if height > 0 else ""
    return "/".join(
        [
            f"bv*{limit}[vcodec^=avc1]+ba[acodec^=mp4a]",
            f"bv*{limit}[vcodec^=avc1]+ba",
            f"b{limit}[vcodec^=avc1]",
            f"bv*{limit}+ba",
            f"b{limit}",
            "b",
        ]
    )


def _format_selector(opts) -> str:
    height = int(opts.video_height or 0)
    if settings.MEDIA_PREFER_COMPATIBLE_CODECS:
        return _compatible_format_selector(height)
    if height > 0:
        return (
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}]/best"
        )
    return "bestvideo+bestaudio/best"


def build_download_args(
    url: str,
    outtmpl: str,
    opts,
    *,
    mode: str | None = None,
    print_file: str | None = None,
    extra_proxy_args: list[str] | None = None,
) -> list[str]:
    """Build the yt-dlp argv for a single item."""
    effective = mode or opts.media_mode
    args: list[str] = [*ytdlp_cmd(), "--no-warnings", "--no-color", "--ignore-config"]

    # ── network policy ──
    if extra_proxy_args:
        args += extra_proxy_args
    elif opts.proxy:
        args += ["--proxy", opts.proxy]

    args += [
        "--newline",
        "--no-quiet",
        "--no-simulate",
        "--no-playlist",
        "--retries", "3",
        "--fragment-retries", "3",
        # resume partial downloads (断点续传)
        "--continue",
        # keep the original title; only trim absurdly long names
        "--trim-filenames", "180",
    ]

    if opts.rate_limit:
        args += ["--limit-rate", str(opts.rate_limit)]

    if opts.write_subs:
        args += [
            "--write-auto-sub",
            "--sub-langs", opts.sub_langs or "en.*",
            "--convert-subs", "srt",
        ]
    if opts.write_thumbnail:
        args += ["--write-thumbnail", "--convert-thumbnails", "jpg"]

    if effective == "audio":
        args += [
            "-f", "bestaudio/best",
            "-x",
            "--audio-format", opts.audio_format or "mp3",
        ]
        if settings.MEDIA_AUDIO_QUALITY:
            args += ["--audio-quality", str(settings.MEDIA_AUDIO_QUALITY)]
    else:
        args += ["-f", _format_selector(opts), "--merge-output-format", "mp4"]

    args += ["-o", outtmpl]
    if print_file:
        args += ["--print-to-file", "after_move:filepath", print_file]
    args.append(url)
    return args


# ══════════════════════════════════════════════════════════════════════
#  Listing
# ══════════════════════════════════════════════════════════════════════


async def dump_json(
    url: str,
    *,
    limit: int | None = None,
    opts=None,
    timeout: int = 180,
    provider: str | None = None,
) -> dict:
    """``yt-dlp --flat-playlist --dump-single-json`` with the item's network policy."""
    cmd = [
        *ytdlp_cmd(), "--no-warnings", "--no-color", "--ignore-config",
        "--flat-playlist", "--dump-single-json", "--skip-download",
        "--extractor-retries", "3",
    ]
    if opts is not None and opts.proxy:
        cmd += ["--proxy", opts.proxy]
    if limit and limit > 0:
        cmd += ["--playlist-end", str(limit)]
    cmd.append(url)

    logger.info("Listing media: %s", url)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("yt-dlp 未安装，无法列出内容") from exc

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"列出内容超时（>{timeout}s）")

    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace").strip()
    if not out.strip():
        raise RuntimeError(error_message(err, provider, prefix="获取内容失败"))
    import json as _json

    try:
        data = _json.loads(out)
    except _json.JSONDecodeError as exc:
        raise RuntimeError(f"无法解析 yt-dlp 输出: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(error_message(err, provider, prefix="获取内容失败"))
    return data


# ══════════════════════════════════════════════════════════════════════
#  Download
# ══════════════════════════════════════════════════════════════════════


async def download(
    url: str,
    outtmpl: str,
    opts,
    *,
    mode: str | None = None,
    on_line=None,
    timeout: int = DEFAULT_TIMEOUT,
) -> EngineResult:
    """Run yt-dlp for one item, streaming output to ``on_line``.

    ``on_line`` may return ``False`` to abort (pause/cancel) — the process is
    terminated and :data:`RC_ABORTED` is returned.
    """
    tmp_path: str | None = None
    fd, tmp_path = tempfile.mkstemp(prefix="media-out-", suffix=".txt", dir=str(settings.DATA_DIR))
    os.close(fd)
    args = build_download_args(url, outtmpl, opts, mode=mode, print_file=tmp_path)

    Path(outtmpl.replace("%(title)s", "x").replace("%(ext)s", "x")).parent.mkdir(
        parents=True, exist_ok=True
    )
    logger.info("Downloading: %s -> %s", url, outtmpl)

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        _cleanup(tmp_path)
        return EngineResult(rc=127, tail="yt-dlp 不可执行", files=[])

    tail: list[str] = []
    aborted = False

    async def _pump() -> None:
        nonlocal aborted
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue
            tail.append(line)
            if len(tail) > 400:
                del tail[: len(tail) - 400]
            if on_line is not None:
                try:
                    verdict = await on_line(line)
                except Exception:
                    logger.debug("line hook failed", exc_info=True)
                    verdict = None
                if verdict is False:
                    aborted = True
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    break

    try:
        await asyncio.wait_for(asyncio.gather(_pump(), proc.wait()), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        tail.append(f"下载超时（>{timeout}s）")

    produced = _read_produced(tmp_path)
    _cleanup(tmp_path)

    rc = RC_ABORTED if aborted else (proc.returncode or 0)
    return EngineResult(rc=rc, tail="\n".join(tail[-80:]), files=produced, aborted=aborted)


def _read_produced(tmp_path: str | None) -> list[str]:
    if not tmp_path or not os.path.exists(tmp_path):
        return []
    try:
        with open(tmp_path, encoding="utf-8", errors="replace") as fh:
            return [ln.strip() for ln in fh if ln.strip()]
    finally:
        _cleanup(tmp_path)


def _cleanup(path: str | None) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


# ══════════════════════════════════════════════════════════════════════
#  Audio extraction (both mode) — reuse the shared, copy-first implementation
# ══════════════════════════════════════════════════════════════════════


async def extract_audio(src: str, fmt: str) -> tuple[int, str, str | None]:
    return await _yt.extract_audio(src, fmt)


async def probe_audio_codec(path: str) -> str | None:
    return await _yt.probe_audio_codec(path)


VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".flv"}
AUDIO_EXTS = {".mp3", ".m4a", ".opus", ".aac", ".flac", ".wav", ".ogg"}


def pick_output(files: list[str], *, audio_only: bool) -> str | None:
    """Choose the primary media file from yt-dlp's produced list."""
    wanted = AUDIO_EXTS if audio_only else VIDEO_EXTS
    for path in reversed(files):
        if os.path.splitext(path)[1].lower() in wanted and os.path.isfile(path):
            return path
    for path in reversed(files):
        if os.path.isfile(path) and os.path.splitext(path)[1].lower() not in {".jpg", ".png", ".srt", ".vtt", ".json"}:
            return path
    return None


# ══════════════════════════════════════════════════════════════════════
#  Plain HTTP download (podcast RSS episodes / direct audio files)
# ══════════════════════════════════════════════════════════════════════


async def http_download(
    url: str,
    dest: str,
    *,
    proxy: str | None = None,
    on_progress=None,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[int, str, int]:
    """Resumable HTTP(S) download. Returns ``(rc, message, bytes_written)``.

    Uses ``Range`` so an interrupted download continues (断点续传).
    """
    import httpx

    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    existing = os.path.getsize(dest) if os.path.isfile(dest) else 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}

    total = 0
    written = existing
    try:
        async with httpx.AsyncClient(
            proxy=proxy, follow_redirects=True, timeout=httpx.Timeout(60.0, read=120.0)
        ) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code == 416:  # already complete
                    return 0, "already downloaded", existing
                if resp.status_code not in (200, 206):
                    return resp.status_code, f"HTTP {resp.status_code}", existing
                length = int(resp.headers.get("content-length") or 0)
                total = length + existing
                mode = "ab" if resp.status_code == 206 else "wb"
                if mode == "wb":
                    written = 0
                with open(dest, mode) as fh:
                    async for chunk in resp.aiter_bytes(256 * 1024):
                        fh.write(chunk)
                        written += len(chunk)
                        if on_progress is not None and total > 0:
                            pct = min(100.0, written * 100.0 / total)
                            await on_progress(pct, None, written, total, None, "downloading")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}", written
    return 0, "ok", written


# ══════════════════════════════════════════════════════════════════════
#  Health / network probe
# ══════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════
#  Per-provider network diagnostics
# ══════════════════════════════════════════════════════════════════════

#: probe targets per provider — (key, human label, url)
PROVIDER_PROBES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "youtube": (
        ("api", "YouTube 页面/API", "https://www.youtube.com/"),
        ("cdn", "视频 CDN (googlevideo)", "https://redirector.googlevideo.com/"),
    ),
    "bilibili": (
        ("api", "Bilibili API", "https://api.bilibili.com/x/web-interface/nav"),
        ("cdn", "视频 CDN (bilivideo)", "https://upos-sz-mirrorcos.bilivideo.com/"),
        ("img", "图片 CDN (hdslb)", "https://i0.hdslb.com/"),
    ),
    "soundcloud": (("api", "SoundCloud", "https://soundcloud.com/"),),
    "vimeo": (("api", "Vimeo", "https://vimeo.com/"),),
    "twitch": (("api", "Twitch", "https://www.twitch.tv/"),),
    "x": (("api", "X (Twitter)", "https://x.com/"),),
    "podcast": (("dns", "公共 DNS 解析", "https://1.1.1.1/"),),
    "custom": (("dns", "公共 DNS 解析", "https://1.1.1.1/"),),
}

PROVIDER_NAMES: dict[str, str] = {
    "youtube": "YouTube",
    "bilibili": "Bilibili",
    "podcast": "Podcast RSS",
    "soundcloud": "SoundCloud",
    "vimeo": "Vimeo",
    "twitch": "Twitch",
    "x": "X (Twitter)",
    "custom": "任意 URL",
}


async def probe_url(url: str, proxy: str | None = None, timeout: int = 12) -> int | None:
    """HTTP status for ``url`` through ``proxy`` (``None`` = connection failed)."""
    cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", str(timeout)]
    if proxy:
        cmd += ["--proxy", proxy]
    elif proxy == "":
        cmd += ["--noproxy", "*"]
    cmd.append(url)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout + 6)
    except (FileNotFoundError, asyncio.TimeoutError, Exception):
        return None
    text = out.decode("utf-8", errors="replace").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def provider_display(provider: str | None) -> str:
    return PROVIDER_NAMES.get((provider or "custom").lower(), provider or "该来源")


async def check_provider(provider: str | None, *, url: str | None = None, proxy: str | None = None) -> dict:
    """Reachability report for one provider (optionally through its proxy)."""
    key = (provider or "custom").lower()
    name = provider_display(key)
    probes = PROVIDER_PROBES.get(key, PROVIDER_PROBES["custom"])

    checks: dict[str, dict] = {}
    if key in ("podcast", "custom") and url:
        checks["source"] = {
            "label": f"{name} 订阅地址",
            "url": url,
            "http_status": await probe_url(url, proxy),
        }
    for pk, label, target in probes:
        checks[pk] = {
            "label": label,
            "url": target,
            "http_status": await probe_url(target, proxy),
        }
    for item in checks.values():
        item["reachable"] = item["http_status"] is not None

    api_keys = [k for k in checks if k in ("api", "dns", "source")]
    cdn_keys = [k for k in checks if k == "cdn"]
    api_ok = all(checks[k]["reachable"] for k in api_keys) if api_keys else True
    cdn_ok = all(checks[k]["reachable"] for k in cdn_keys) if cdn_keys else True
    ok = bool(api_ok and cdn_ok)

    fix = "为该订阅设置代理（编辑订阅 → 出网方式 → HTTP/SOCKS5），或设置环境变量 MEDIA_PROXY_URL"
    if ok:
        detail = f"{name} 网络正常"
    elif api_ok and not cdn_ok:
        detail = f"能访问 {name} 页面/API，但无法访问视频 CDN：可以列出清单，下载会失败。{fix}"
    else:
        detail = f"无法访问 {name}：请检查网络，或{fix}"

    return {
        "provider": key,
        "display_name": name,
        "proxy": proxy,
        "ok": ok,
        "detail": detail,
        "checks": checks,
    }


#: How long a full provider sweep stays valid. The health endpoint is hit on
#: every page load; without this each visit re-probed six providers (~30s of
#: sequential timeouts on a blocked network) and the UI sat there waiting.
NET_CACHE_SECONDS = 120.0
_net_cache: dict[str, tuple[float, dict]] = {}


def invalidate_network_cache() -> None:
    """Drop the cached sweep (e.g. after the proxy settings change)."""
    _net_cache.clear()


async def check_all_providers(proxy: str | None = None, *, force: bool = False) -> dict:
    """Probe every implemented provider (used by the health endpoint).

    Probes run concurrently and the result is cached for
    :data:`NET_CACHE_SECONDS`; pass ``force=True`` for an explicit
    "test now" action.
    """
    from app.services.media_center.base import all_providers

    cache_key = (proxy or "").strip()
    now = time.monotonic()
    if not force:
        cached = _net_cache.get(cache_key)
        if cached is not None and now - cached[0] < NET_CACHE_SECONDS:
            return cached[1]

    targets = [
        key
        for key, cls in sorted(all_providers().items())
        if getattr(cls, "implemented", True) and key not in ("podcast", "custom")
    ]
    # Sequential probing took the sum of every timeout; a blocked source could
    # stall the whole page for half a minute.
    probed = await asyncio.gather(
        *(check_provider(key, proxy=proxy) for key in targets),
        return_exceptions=True,
    )
    results: dict[str, dict] = {}
    for key, outcome in zip(targets, probed):
        if isinstance(outcome, BaseException):
            logger.warning("Provider probe failed for %s: %s", key, outcome)
            continue
        results[key] = outcome

    youtube_ok = results.get("youtube", {}).get("ok", False)
    payload = {
        "ok": youtube_ok,
        "proxy": proxy,
        "detail": "YouTube 可直连" if youtube_ok else "YouTube 无法直连（其他来源见 providers）",
        "checks": results.get("youtube", {}).get("checks", {}),
        "providers": results,
    }
    _net_cache[cache_key] = (now, payload)
    return payload


async def check_network(proxy: str | None = None, provider: str | None = None, url: str | None = None) -> dict:
    """Backwards-compatible wrapper: single provider, or all of them."""
    if provider:
        return await check_provider(provider, url=url, proxy=proxy)
    return await check_all_providers(proxy=proxy)


_NET_WORDS = (
    "ssl",
    "certificate",
    "unexpected_eof",
    "timed out",
    "timeout",
    "connection reset",
    "connection refused",
    "unable to download api page",
    "unable to download webpage",
    "failed to resolve",
    "temporary failure in name resolution",
    "network is unreachable",
)


def network_hint(text: str | None, provider: str | None = None) -> str | None:
    """Provider-aware hint for connection-level failures.

    The message must name the *actual* source — reporting "YouTube" for a
    Bilibili failure is what confused users.
    """
    lowered = (text or "").lower()
    name = provider_display(provider)
    if any(word in lowered for word in _NET_WORDS):
        return (
            f"无法连接到 {name}（SSL/TLS 或网络被中断）。"
            f"请为该订阅设置代理（编辑订阅 → 出网方式 → HTTP/SOCKS5），"
            f"或设置环境变量 MEDIA_PROXY_URL"
        )
    if "http error 429" in lowered or "too many requests" in lowered:
        return f"{name} 限流（429），请稍后重试或降低同步频率"
    if "http error 403" in lowered:
        return f"{name} 拒绝访问（403），可能需要 cookies 或代理"
    if "sign in to confirm" in lowered or "login required" in lowered:
        return f"{name} 要求登录验证，请配置 cookies"
    return None


def error_message(text: str | None, provider: str | None = None, *, prefix: str = "获取内容失败") -> str:
    """Build an actionable, provider-correct error message."""
    tail = (text or "").strip()[-600:]
    hint = network_hint(text, provider)
    if hint:
        return f"{hint}。yt-dlp: {tail}" if tail else hint
    return f"{prefix}: {tail}" if tail else prefix


def parse_progress_line(line: str) -> dict | None:
    """``[download] 12.3% of 1.2GiB at 5MiB/s ETA 00:12`` → structured dict."""
    return _yt.parse_progress_line(line)


def is_processing_line(line: str) -> bool:
    """True when yt-dlp switched to local post-processing."""
    return _yt.is_processing_line(line)


__all__ = [
    "AUDIO_EXTS",
    "EngineResult",
    "RC_ABORTED",
    "VIDEO_EXTS",
    "NET_CACHE_SECONDS",
    "PROVIDER_PROBES",
    "invalidate_network_cache",
    "build_download_args",
    "check_all_providers",
    "check_network",
    "check_provider",
    "error_message",
    "probe_url",
    "provider_display",
    "dependency_versions",
    "dump_json",
    "download",
    "extract_audio",
    "ffmpeg_installed",
    "http_download",
    "is_processing_line",
    "network_hint",
    "parse_progress_line",
    "pick_output",
    "probe_audio_codec",
    "ytdlp_cmd",
    "ytdlp_installed",
]
