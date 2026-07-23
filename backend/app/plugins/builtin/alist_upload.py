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

logger = logging.getLogger("naspilot.plugin.alist")

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

    async def verify_file(self, remote_path: str, expected_size: int,
                          wait_secs: float = 120.0, tries: int = 12) -> bool:
        assert self._client
        interval = max(1.0, wait_secs / max(1, tries))
        parent = os.path.dirname(remote_path)
        name = os.path.basename(remote_path)
        for _ in range(tries):
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
                                return True
            except Exception:
                pass
            # fallback: direct get
            info = await self.get_file_info(remote_path)
            if info and info.get("size") == expected_size:
                return True
            await asyncio.sleep(interval)
        return False

    async def upload(self, local_path: str, remote_path: str,
                     max_retries: int = 3) -> tuple[str, str]:
        """Upload a single file. Returns (status, message) where status is 'ok'|'skip'|'fail'."""
        assert self._client
        size = os.path.getsize(local_path)
        filename = os.path.basename(local_path)
        remote_dir = os.path.dirname(remote_path)

        # Already exists with correct size → skip
        info = await self.get_file_info(remote_path)
        if info and info.get("size") == size:
            return "skip", f"已存在且大小相同: {filename} ({_fmt_size(size)})"

        if not await self.mkdir_recursive(remote_dir):
            return "fail", f"创建目录失败: {remote_dir}"

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
                with open(local_path, "rb") as fh:
                    content = fh.read()

                resp = await self._client.put(
                    f"{self.base}/api/fs/put",
                    content=content,
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

                # Verify upload
                verified = await self.verify_file(remote_path, size, wait_secs=120, tries=12)
                if verified:
                    return "ok", f"上传成功: {filename} ({_fmt_size(size)})"
                last_err = "上传后校验失败"
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
            except Exception as exc:
                last_err = str(exc)
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

        return "fail", f"上传失败 (重试{max_retries}次): {last_err}"


def _collect_files(scan_dirs: list[str], extensions: list[str]) -> list[str]:
    """Recursively collect files matching extensions from scan_dirs."""
    files: list[str] = []
    ext_set = {e.lower().lstrip(".") for e in extensions} if extensions else None
    for base in scan_dirs:
        if not os.path.isdir(base):
            logger.warning("scan_dir not found: %s", base)
            continue
        for root, _dirs, fnames in os.walk(base):
            for fname in fnames:
                if ext_set:
                    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
                    if ext not in ext_set:
                        continue
                files.append(os.path.join(root, fname))
    return files


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
            logger.exception("AList Upload run failed")
            return {"status": "error", "error": str(exc)[:500], "scanned": 0, "uploaded": 0, "skipped": 0, "failed": 0, "deleted": 0}

    async def _run_impl(self, **kwargs: Any) -> dict[str, Any]:
        cfg = self.config
        logger.info("启动")
        alist_url = cfg.get("alist_url", "").strip()
        if not alist_url:
            logger.warning("AList URL is not configured")
            return {"status": "failed", "error": "AList URL is not configured"}

        scan_dirs: list[str] = cfg.get("scan_dirs") or []
        if not scan_dirs:
            logger.warning("No scan_dirs configured")
            return {"status": "failed", "error": "No scan_dirs configured"}

        remote_root = cfg.get("remote_root", "/").rstrip("/")
        extensions: list[str] = cfg.get("extensions") or []
        max_retries = int(cfg.get("max_retries", 3))
        delete_after = bool(cfg.get("delete_after_upload", False))

        logger.info("Scanning dirs=%d, remote=%s", len(scan_dirs), remote_root)
        files = await asyncio.to_thread(_collect_files, scan_dirs, extensions)
        logger.info("AList scan found %d file(s)", len(files))
        if not files:
            logger.info("No new files to upload")

        results: list[dict[str, Any]] = []
        counts = {"scanned": len(files), "uploaded": 0, "skipped": 0, "failed": 0, "deleted": 0}

        # Find the common base so relative paths look sane
        base_dir = scan_dirs[0] if len(scan_dirs) == 1 else ""

        async with AListClient(
            alist_url,
            cfg.get("username", "admin"),
            cfg.get("password", ""),
            connect_timeout=float(cfg.get("connect_timeout", 10)),
            read_timeout=float(cfg.get("read_timeout", 120)),
        ) as client:
            for local_path in files:
                if base_dir and local_path.startswith(base_dir):
                    rel = local_path[len(base_dir):].lstrip("/\\").replace("\\", "/")
                else:
                    rel = os.path.basename(local_path)
                remote_path = f"{remote_root}/{rel}"

                status, msg = await client.upload(local_path, remote_path, max_retries=max_retries)
                logger.info("[%s] %s — %s", status.upper(), os.path.basename(local_path), msg)
                results.append({"file": rel, "status": status, "message": msg, "time": _now_iso()})

                if status == "ok":
                    counts["uploaded"] += 1
                    if delete_after:
                        try:
                            os.remove(local_path)
                            counts["deleted"] += 1
                        except OSError as exc:
                            logger.warning("delete failed: %s: %s", local_path, exc)
                elif status == "skip":
                    counts["skipped"] += 1
                else:
                    counts["failed"] += 1

        # Persist history (last 200 entries)
        state = cfg.setdefault("state", {})
        history: list[dict[str, Any]] = state.setdefault("history", [])
        history.extend(results)
        if len(history) > 200:
            state["history"] = history[-200:]

        return {"status": "ok", **counts, "results": results[-50:]}
