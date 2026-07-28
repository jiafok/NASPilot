"""AList Upload Plugin — automatic local scan + upload to AList.

Ported from alist_upload.py v4.3.3:
- AList login (plain / Bearer auto-detect)
- Recursive directory scan
- Streaming PUT upload (/api/fs/put)
- Async FS verification (/api/fs/list?refresh + /api/fs/get)
- Retry with exponential back-off
- delete-after-upload policy
- Upload history stored in config["state"]["history"]
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from math import floor, log, pow
from typing import Any
from urllib.parse import quote

import httpx

from app.plugins.registry import PluginBase, PluginMeta

logger = logging.getLogger("naspilot.plugin.alist_upload")

LOCAL_TZ = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(LOCAL_TZ).isoformat()


def _fmt_size(n: int) -> str:
    if n <= 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    i = int(floor(log(n, 1024)))
    i = min(i, len(units) - 1)
    return f"{n / pow(1024, i):.2f} {units[i]}"


def _encode_path(path: str) -> str:
    return quote(path, safe="")


def _normalize_list(value: Any) -> list[str]:
    """Normalize config values that may arrive as comma-separated strings.

    PluginConfigForm renders ``type: 'array'`` as a plain text Input,
    so values like ``/dir1, /dir2`` are stored as strings, not JSON arrays.
    """
    if isinstance(value, list):
        return [v.strip() for v in value if isinstance(v, str) and v.strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _build_upload_message(counts: dict[str, int], details: list[dict[str, Any]],
                          skipped_exists: int = 0, skipped_too_large: list[str] | None = None) -> str:
    """Build a detailed Feishu notification message for upload results."""
    parts = [f"共扫描 {counts['scanned']} 个文件"]
    if counts["uploaded"] > 0:
        uploaded = [d for d in details if d["status"] == "ok"]
        parts.append(f"✅ 上传成功: {counts['uploaded']} 个")
        for f in uploaded[:20]:
            parts.append(f"  · {f['name']} ({f['size_mb']} MB)")
        if len(uploaded) > 20:
            parts.append(f"  ... 及 {len(uploaded)-20} 个")
    if skipped_exists > 0:
        parts.append(f"⏭️ 已存在跳过: {skipped_exists} 个")
    if skipped_too_large:
        parts.append(f"⚠️ 超过大小上限跳过: {len(skipped_too_large)} 个")
        for f in skipped_too_large[:10]:
            parts.append(f"  · {f}")
    if counts["failed"] > 0:
        failed = [d for d in details if d["status"] == "fail"]
        parts.append(f"❌ 失败: {counts['failed']} 个")
        for f in failed[:10]:
            err = f.get("error", "")
            parts.append(f"  · {f['name']} ({f['size_mb']} MB) {err}")
    return "\n".join(parts)


def _collect_files(scan_dirs: list[str], extensions: list[str]) -> list[str]:
    """Recursively scan directories and return file paths matching extensions.

    Args:
        scan_dirs: list of local directories to scan.
        extensions: file extensions to include (e.g. ['mkv','mp4']). Empty = all.
    """
    exts = {e.lower().lstrip(".") for e in extensions} if extensions else None
    results: list[str] = []
    for scan_dir in scan_dirs:
        scan_dir = os.path.expanduser(scan_dir)
        if not os.path.isdir(scan_dir):
            logger.warning("Scan dir does not exist: %s", scan_dir)
            continue
        for root, _dirs, files in os.walk(scan_dir):
            for fname in files:
                if exts:
                    ext = os.path.splitext(fname)[1].lower().lstrip(".")
                    if ext not in exts:
                        continue
                results.append(os.path.join(root, fname))
    return results


def _refresh_emby(config: dict[str, Any]) -> bool:
    """Notify Emby to refresh its library. Mirrors original refresh_emby()."""
    emby_host = str(config.get("emby_host", "")).strip().rstrip("/")
    emby_key = str(config.get("emby_api_key", "")).strip()
    if not emby_host or not emby_key:
        logger.info("Emby refresh skipped — not configured")
        return False
    try:
        import httpx as _httpx
        r = _httpx.post(
            f"{emby_host}/emby/Library/Refresh",
            headers={"X-Emby-Token": emby_key}, timeout=10,
        )
        if r.status_code == 204:
            logger.info("Emby 库刷新请求已发送")
            return True
        logger.warning("Emby 刷新失败: HTTP %d", r.status_code)
        return False
    except Exception as exc:
        logger.warning("Emby 刷新异常: %s", exc)
        return False


class AListError(Exception):
    pass


class AListClient:
    """Async AList API client — mirrors the logic from alist_upload.py."""

    def __init__(self, base_url: str, username: str, password: str,
                 connect_timeout: float = 10.0, read_timeout: float = 120.0):
        self.base = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self._token: str = ""
        self._auth_scheme: str = "plain"   # plain | bearer
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.connect_timeout, read=self.read_timeout),
            follow_redirects=True,
        )
        await self.login()
        return self

    async def __aexit__(self, *_):
        if self._client:
            await self._client.aclose()

    def _auth_header(self) -> dict[str, str]:
        if not self._token:
            return {}
        if self._auth_scheme == "bearer":
            return {"Authorization": f"Bearer {self._token}"}
        return {"Authorization": self._token}

    async def login(self) -> None:
        assert self._client
        resp = await self._client.post(
            f"{self.base}/api/auth/login",
            json={"username": self.username, "password": self.password},
        )
        if resp.status_code != 200:
            raise AListError(f"Login HTTP {resp.status_code}")
        data = resp.json()
        if data.get("code") != 200:
            raise AListError(f"Login failed: {data.get('message')}")
        self._token = data["data"]["token"]
        # probe which auth scheme works
        test = await self._client.post(
            f"{self.base}/api/fs/list",
            json={"path": "/", "page": 1, "per_page": 1},
            headers={"Authorization": self._token},
        )
        if test.status_code == 200 and (test.json().get("code") == 200):
            self._auth_scheme = "plain"
        else:
            self._auth_scheme = "bearer"
        logger.debug("AList login OK, scheme=%s", self._auth_scheme)

    async def get_file_info(self, remote_path: str) -> dict[str, Any] | None:
        assert self._client
        try:
            resp = await self._client.post(
                f"{self.base}/api/fs/get",
                json={"path": remote_path},
                headers=self._auth_header(),
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            return data.get("data") if data.get("code") == 200 else None
        except Exception:
            return None

    async def mkdir_recursive(self, remote_dir: str) -> bool:
        assert self._client
        try:
            cur = ""
            for seg in [p for p in remote_dir.strip("/").split("/") if p]:
                cur += "/" + seg
                resp = await self._client.post(
                    f"{self.base}/api/fs/mkdir",
                    json={"path": cur},
                    headers=self._auth_header(),
                )
                if resp.status_code != 200:
                    return False
            return True
        except Exception:
            return False

    async def get_task_state(self, tid: str) -> dict | None:
        """Poll AList upload task state. Mirrors get_upload_task_info_smart."""
        assert self._client
        for endpoint in (f"/api/task/upload/info?tid={tid}",
                         f"/api/admin/task/upload/info?tid={tid}"):
            try:
                resp = await self._client.post(
                    f"{self.base}{endpoint}", headers=self._auth_header(),
                    timeout=self.connect_timeout,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 200 and data.get("data"):
                        return data["data"]
            except Exception:
                pass
        return None

    def _adaptive_window(self, file_size: int) -> tuple[int, int]:
        """Calculate wait window and tries — mirrors original _adaptive_window."""
        gb = max(0.001, file_size / (1024**3))
        base = 7200       # base wait (seconds)
        per_gb = 1000     # add per GB
        cap = 5 * 3600    # max cap (5 hours)
        wait = min(cap, base + int(gb * per_gb))
        tries = max(12, wait // 30)
        return wait, tries

    async def verify_task(self, remote_path: str, expected_size: int,
                          task: dict | None = None) -> tuple[bool, str]:
        """Full verification — mirrors _verify_task from original script.

        1. If task provided: poll until terminal state (success/fail), then FS verify
        2. If no task: FS verify with adaptive window
        3. Task failed → immediate failure (no FS needed)
        4. Task succeeded → proceed to FS verify with retries

        Returns (ok, message).
        """
        task_id = (task or {}).get("id") if task else None
        wait_secs, tries = self._adaptive_window(expected_size)

        # ── Phase 0: Track task to terminal state ──
        if task_id:
            logger.info("🔎 追踪任务: %s (%s), 最长等待 %ds",
                       task_id, os.path.basename(remote_path), wait_secs)
            end_ts = time.time() + wait_secs
            while time.time() < end_ts:
                info = await self.get_task_state(task_id)
                if info:
                    state_raw = info.get("state")
                    error_str = (info.get("error") or "").strip()
                    end_time = info.get("end_time")
                    if isinstance(state_raw, str):
                        st = state_raw.strip().lower()
                    elif isinstance(state_raw, (int, float)):
                        st = int(state_raw)
                    else:
                        st = None

                    # Task succeeded → proceed to FS verify
                    if (st == 2 or st == "succeeded") or (end_time and not error_str):
                        logger.info("✅ 任务完成: %s (state=%s)", task_id, st)
                        break
                    # Task failed → return immediately
                    if (isinstance(st, int) and st in (5, 6, 7)) or \
                       (isinstance(st, str) and st in ("failed", "error", "canceled", "cancelled", "stopped")) or \
                       error_str:
                        return False, f"任务失败: state={st}, error={error_str[:100]}"
                await asyncio.sleep(max(2, min(30, wait_secs / max(1, tries))))

        # ── Phase 1: FS verification ──
        # Cap tries at 30 per file — AList usually reflects uploads within seconds
        fs_tries = min(tries, 30)
        interval = max(2.0, min(30, (wait_secs // 4) / max(1, fs_tries)))
        name = os.path.basename(remote_path)
        parent = os.path.dirname(remote_path)
        for _ in range(fs_tries):
            try:
                resp = await self._client.post(
                    f"{self.base}/api/fs/list",
                    json={"path": parent, "page": 1, "per_page": 0, "refresh": True},
                    headers=self._auth_header(),
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 200:
                        for item in (data.get("data") or {}).get("content") or []:
                            if item.get("name") == name and not item.get("is_dir") and item.get("size") == expected_size:
                                return True, f"校验通过: {name} ({_fmt_size(expected_size)})"
            except Exception:
                pass
            # Fallback: direct get
            info = await self.get_file_info(remote_path)
            if info and info.get("size") == expected_size:
                return True, f"校验通过(direct): {name} ({_fmt_size(expected_size)})"
            await asyncio.sleep(interval)
        return False, f"校验超时: {name} (已查 {fs_tries} 次, 间隔 {int(interval)}s)"

    async def upload_put(self, local_path: str, remote_path: str,
                     max_retries: int = 3) -> tuple[str, str, Any]:
        """Upload a single file (PUT only, no verify). Returns (status, message, task_or_None).

        status is 'skip'|'pending'|'fail'. The task dict from AList response
        is returned for deferred task-tracking verification, mirroring the
        original script's upload→task-track→FS-verify pattern.
        """
        assert self._client
        size = os.path.getsize(local_path)
        filename = os.path.basename(local_path)
        remote_dir = os.path.dirname(remote_path)

        # Already exists with correct size → skip
        info = await self.get_file_info(remote_path)
        if info and info.get("size") == size:
            return "skip", f"已存在且大小相同: {filename} ({_fmt_size(size)})", None

        if not await self.mkdir_recursive(remote_dir):
            return "fail", f"创建目录失败: {remote_dir}", None

        last_err = ""
        for attempt in range(max_retries):
            try:
                headers = {
                    **self._auth_header(),
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(size),
                    "File-Path": _encode_path(remote_path),
                    "As-Task": "true",
                }
                async def _file_stream(_path: str, _chunk: int = 65536):
                    def _read_file_chunks():
                        with open(_path, "rb") as _f:
                            while True:
                                _data = _f.read(_chunk)
                                if not _data:
                                    break
                                yield _data
                    
                    # Run file reading in thread pool and yield chunks
                    for chunk in await asyncio.to_thread(_read_file_chunks):
                        yield chunk

                resp = await self._client.put(
                    f"{self.base}/api/fs/put",
                    content=_file_stream(local_path),
                    headers=headers,
                    timeout=httpx.Timeout(self.connect_timeout, read=max(self.read_timeout, 3600)),
                )
                if resp.status_code != 200:
                    last_err = f"HTTP {resp.status_code}"
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                    continue
                data = resp.json()
                if data.get("code") != 200:
                    last_err = data.get("message", "unknown")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                    continue

                # Extract AList task for tracking
                task = ((data.get("data") or {}).get("task")) or None
                if task and task.get("id"):
                    logger.info("📌 AList 任务: id=%s name=%s progress=%s",
                                task.get("id"), task.get("name", "?"), task.get("progress", "?"))

                return "pending", f"已上传: {filename} ({_fmt_size(size)})", task
            except httpx.ReadTimeout:
                logger.warning("Upload timeout (will verify): %s", filename)
                return "pending", f"上传超时, 待验证: {filename}", None
            except Exception as exc:
                last_err = str(exc) if str(exc) else type(exc).__name__
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

        return "fail", f"上传失败 (重试{max_retries}次): {last_err}", None


class AListUploadPlugin(PluginBase):
    META = PluginMeta(
        slug="alist_upload",
        name="AList 自动上传",
        description="本地扫描、规则匹配、自动上传、重试机制、上传历史",
        version="1.0.0",
        author="NASPilot",
        icon="📁",
        category="storage",
        entrypoint="app.plugins.builtin.alist_upload",
    )

    @property
    def default_config(self) -> dict[str, Any]:
        return {
            "alist_url": "",
            "username": "admin",
            "password": "",
            "scan_dirs": [],
            "remote_root": "/",
            "extensions": [],          # empty = all files
            "max_retries": 3,
            "delete_after_upload": False,
            "connect_timeout": 10,
            "read_timeout": 120,
            "max_file_size_gb": 0,     # 0 = no limit
            "min_free_space_gb": 0,   # 0 = skip check
            "verify_max_workers": 4,  # concurrent uploads + verification
            "verify_wait_secs": 7200,     # base wait window (seconds)
            "verify_per_gb_addon": 1000,  # additional seconds per GB
            "verify_wait_cap_secs": 18000, # max wait cap (seconds, 5h)
            "emby_host": "",             # optional Emby server URL
            "emby_api_key": "",          # optional Emby API key
        }

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "alist_url": {"type": "string", "title": "AList 地址"},
                "username": {"type": "string", "title": "用户名"},
                "password": {"type": "string", "title": "密码"},
                "scan_dirs": {"type": "array", "items": {"type": "string"}, "title": "扫描目录"},
                "remote_root": {"type": "string", "title": "远程根路径"},
                "extensions": {"type": "array", "items": {"type": "string"}, "title": "文件扩展名过滤（空=全部）"},
                "max_retries": {"type": "integer", "title": "最大重试次数"},
                "delete_after_upload": {"type": "boolean", "title": "上传成功后删除本地文件"},
                "max_file_size_gb": {"type": "number", "title": "文件大小上限(GB)，0=不限"},
                "min_free_space_gb": {"type": "number", "title": "远程最小剩余空间(GB)，0=不检查"},
                "verify_max_workers": {"type": "integer", "title": "最大并发上传数"},
                "emby_host": {"type": "string", "title": "Emby 地址（可选）"},
                "emby_api_key": {"type": "string", "title": "Emby API Key（可选）"},
            },
        }

    async def on_enable(self) -> None:
        logger.info("AList Upload plugin enabled")

    async def on_disable(self) -> None:
        logger.info("AList Upload plugin disabled")

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        import traceback
        try:
            return await self._run_impl(**kwargs)
        except Exception as exc:
            # httpx timeout exceptions often have empty str(exc), fall back to type name
            err_msg = str(exc) if str(exc) else type(exc).__name__
            logger.error("AList Upload run failed: %s", err_msg)
            return {"status": "error", "error": err_msg[:500], "scanned": 0, "uploaded": 0, "skipped": 0, "failed": 0, "deleted": 0}

    async def _run_impl(self, **kwargs: Any) -> dict[str, Any]:
        cfg = self.config
        logger.info("启动")
        alist_url = cfg.get("alist_url", "").strip()
        if not alist_url:
            logger.warning("AList URL is not configured")
            return {"status": "failed", "error": "AList URL is not configured"}

        scan_dirs = _normalize_list(cfg.get("scan_dirs"))
        if not scan_dirs:
            logger.warning("No scan_dirs configured")
            return {"status": "failed", "error": "No scan_dirs configured"}

        remote_root = cfg.get("remote_root", "/").rstrip("/")
        extensions = _normalize_list(cfg.get("extensions"))
        max_retries = int(cfg.get("max_retries", 3))
        delete_after = bool(cfg.get("delete_after_upload", False))
        max_file_size_gb = float(cfg.get("max_file_size_gb", 0) or 0)
        max_file_size_bytes = int(max_file_size_gb * 1024**3) if max_file_size_gb > 0 else 0
        verify_max_workers = int(cfg.get("verify_max_workers", 4) or 4)

        logger.info("Scanning dirs=%d, remote=%s, max_size=%sGB", len(scan_dirs), remote_root,
                    max_file_size_gb if max_file_size_gb else "unlimited")
        files = await asyncio.to_thread(_collect_files, scan_dirs, extensions)
        logger.info("AList scan found %d file(s)", len(files))

        # Filter by file size limit
        skipped_too_large: list[str] = []
        filtered = files
        if max_file_size_bytes > 0:
            filtered = []
            for f in files:
                try:
                    sz = os.path.getsize(f)
                    if sz <= max_file_size_bytes:
                        filtered.append(f)
                    else:
                        logger.info("Skipping (too large): %s (%s)", os.path.basename(f), _fmt_size(sz))
                        skipped_too_large.append(os.path.basename(f))
                except OSError:
                    pass
            files = filtered
            logger.info("After size filter: %d file(s), skipped too large: %d", len(files), len(skipped_too_large))

        if not files:
            logger.info("No new files to upload")

        results: list[dict[str, Any]] = []
        counts = {"scanned": len(files) + len(skipped_too_large), "uploaded": 0, "skipped": 0, "failed": 0, "deleted": 0}

        # Find the common base so relative paths look sane
        base_dir = scan_dirs[0] if len(scan_dirs) == 1 else ""

        async with AListClient(
            alist_url,
            cfg.get("username", "admin"),
            cfg.get("password", ""),
            connect_timeout=float(cfg.get("connect_timeout", 10)),
            read_timeout=float(cfg.get("read_timeout", 120)),
        ) as client:
            # Semaphore to limit concurrent uploads (matching original verify_max_workers)
            upload_sem = asyncio.Semaphore(verify_max_workers)
            logger.info("启动并发上传: %d 文件, %d 并发", len(files), verify_max_workers)
            verify_tasks: list[asyncio.Task] = []
            # Collect detailed per-file results for notification
            notify_details: list[dict[str, Any]] = []

            async def _upload_and_verify(lp: str, rp: str, rl: str, fn: str):
                """Upload (PUT) then schedule verification — runs concurrently."""
                sz = os.path.getsize(lp)
                logger.info("[START] %s (%s)", fn, _fmt_size(sz))
                async with upload_sem:
                    status, msg, task_info = await client.upload_put(lp, rp, max_retries=max_retries)

                    if status == "skip":
                        logger.info("[SKIP] %s — %s", fn, msg)
                        results.append({"file": rl, "status": "skip", "message": msg, "time": _now_iso()})
                        counts["skipped"] += 1
                        notify_details.append({"name": fn, "size_mb": round(sz/1048576,1), "status": "skip"})
                    elif status == "fail":
                        logger.info("[FAIL] %s — %s", fn, msg)
                        results.append({"file": rl, "status": "fail", "message": msg, "time": _now_iso()})
                        counts["failed"] += 1
                        notify_details.append({"name": fn, "size_mb": round(sz/1048576,1), "status": "fail", "error": msg[:100]})
                    else:
                        # pending — verify concurrently
                        logger.info("[UPLOADED] %s — pending verification", fn)
                        async def _verify(_sz=sz, _fn=fn, _lp=lp, _rp=rp, _rl=rl):
                            try:
                                verified, msg_v = await client.verify_task(_rp, _sz, task=task_info)
                            except Exception as exc:
                                verified, msg_v = False, f"校验异常: {exc}"
                            status_v = "ok" if verified else "fail"
                            logger.info("[%s] %s — %s", status_v.upper(), _fn, msg_v)
                            results.append({"file": _rl, "status": status_v, "message": msg_v, "time": _now_iso()})
                            if verified:
                                counts["uploaded"] += 1
                                notify_details.append({"name": _fn, "size_mb": round(_sz/1048576,1), "status": "ok"})
                                if delete_after:
                                    try:
                                        os.remove(_lp)
                                        counts["deleted"] += 1
                                        logger.info("已删除本地: %s", _fn)
                                    except OSError as exc:
                                        logger.warning("delete failed: %s: %s", _lp, exc)
                            else:
                                counts["failed"] += 1
                                notify_details.append({"name": _fn, "size_mb": round(_sz/1048576,1), "status": "fail"})
                        verify_tasks.append(asyncio.create_task(_verify()))

            upload_tasks = []
            for local_path in files:
                if base_dir and local_path.startswith(base_dir):
                    rel = local_path[len(base_dir):].lstrip("/\\").replace("\\", "/")
                else:
                    rel = os.path.basename(local_path)
                remote_path = f"{remote_root}/{rel}"
                filename = os.path.basename(local_path)
                upload_tasks.append(asyncio.create_task(
                    _upload_and_verify(local_path, remote_path, rel, filename)
                ))

            # Wait for all uploads to complete
            if upload_tasks:
                await asyncio.gather(*upload_tasks, return_exceptions=True)
                logger.info("全部上传完成")

            # Wait for all verifications
            if verify_tasks:
                logger.info("等待 %d 个并发校验完成...", len(verify_tasks))
                await asyncio.gather(*verify_tasks, return_exceptions=True)
                logger.info("并发校验全部完成")

        # Persist history (last 200 entries)
        state = cfg.setdefault("state", {})
        history: list[dict[str, Any]] = state.setdefault("history", [])
        history.extend(results)
        if len(history) > 200:
            state["history"] = history[-200:]

        # ── Send Feishu notification with detailed lists ──
        skipped_exists = sum(1 for d in notify_details if d["status"] == "skip")
        await self.notify(
            title="📁 AList 上传结果",
            message=_build_upload_message(counts, notify_details,
                                         skipped_exists=skipped_exists,
                                         skipped_too_large=skipped_too_large),
            level="info" if counts["failed"] == 0 else "warn",
        )

        # ── Emby refresh (if configured and uploads happened) ──
        if counts["uploaded"] > 0:
            asyncio.ensure_future(asyncio.to_thread(_refresh_emby, cfg))

        return {"status": "ok", **counts, "results": results[-50:]}
