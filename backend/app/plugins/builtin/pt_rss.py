"""PT RSS Auto Download Plugin.

This is a dependency-light, async port of the external `pt_rss_auto.py`
script. It keeps the same operational model but stores state in the plugin
configuration payload so the Web UI can manage it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET

import httpx

from app.plugins.registry import PluginBase, PluginMeta

logger = logging.getLogger("naspilot.plugin.pt_rss")

LOCAL_TZ = timezone(timedelta(hours=8))
MAX_RETRIES = 3
RETRY_DELAY = 5

STATUS_PENDING_FREE = "pending_free"
STATUS_EXPIRED_FREE = "expired_free"
STATUS_ADDED = "added"
STATUS_COMPLETED = "completed"
STATUS_EVICTED = "evicted"

OLD_STATUS_MAP = {
    "evicted_by_rss": STATUS_EVICTED,
    "deleted_space_seed": STATUS_EVICTED,
    "deleted_space_stuck": STATUS_EVICTED,
    "deleted_stuck": STATUS_EVICTED,
    "deleted_rss_missing": STATUS_EVICTED,
}

FINAL_STATUSES = {STATUS_COMPLETED, STATUS_EXPIRED_FREE, STATUS_EVICTED}
HARD_FINAL_STATUSES = {STATUS_EVICTED, STATUS_EXPIRED_FREE}


def utc_now() -> datetime:
    return datetime.now(LOCAL_TZ)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def hours_since_iso(ts_iso: str) -> float:
    try:
        dt = datetime.fromisoformat(ts_iso)
    except (TypeError, ValueError):
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return (utc_now() - dt).total_seconds() / 3600


def days_since(timestamp: float) -> float:
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return (utc_now() - dt).total_seconds() / 86400


def normalize_title(value: str) -> str:
    if not value:
        return ""
    value = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def extract_tid(url: str) -> str | None:
    return parse_qs(urlparse(url).query).get("tid", [None])[0]


def extract_tid_from_tags(tags_str: str) -> str | None:
    if not tags_str:
        return None
    for tag in tags_str.split(","):
        tag = tag.strip()
        if tag.startswith("rss_tid:"):
            return tag.replace("rss_tid:", "")
    return None


def has_tag(torrent: dict, tag: str) -> bool:
    raw = torrent.get("tags", "")
    if isinstance(raw, list):
        tags = [str(x).strip() for x in raw if str(x).strip()]
    elif isinstance(raw, str):
        tags = [x.strip() for x in raw.split(",") if x.strip()]
    else:
        tags = []
    return tag in tags


def _state(config: dict[str, Any]) -> dict[str, Any]:
    state = config.setdefault("state", {})
    state.setdefault("processed", {})
    state.setdefault("daily", {"date": None, "stats": {}, "details": {"added_items": [], "deleted_items": []}})
    return state


def _processed(config: dict[str, Any]) -> dict[str, Any]:
    return _state(config)["processed"]


def _daily(config: dict[str, Any]) -> dict[str, Any]:
    daily = _state(config)["daily"]
    daily.setdefault("stats", {})
    daily.setdefault("details", {})
    daily["details"].setdefault("added_items", [])
    daily["details"].setdefault("deleted_items", [])
    return daily


def _default_stats() -> dict[str, int]:
    return {
        "added": 0,
        "expired_free": 0,
        "deleted_stuck": 0,
        "deleted_seed": 0,
        "deleted_rss_missing": 0,
        "deleted_space": 0,
    }


def _ensure_daily_defaults(config: dict[str, Any]) -> None:
    daily = _daily(config)
    stats = daily.setdefault("stats", {})
    for key, value in _default_stats().items():
        stats.setdefault(key, value)


def _migrate_old_status(rec: dict[str, Any]) -> bool:
    old = rec.get("status", "")
    if old in OLD_STATUS_MAP:
        rec["status"] = OLD_STATUS_MAP[old]
        if "evicted_reason" not in rec:
            rec["evicted_reason"] = old
        if "evicted_time" not in rec and "deleted_time" in rec:
            rec["evicted_time"] = rec["deleted_time"]
        return True
    return False


def is_final_status(status: str) -> bool:
    return bool(status) and (status in FINAL_STATUSES or status in OLD_STATUS_MAP)


def is_hard_final_status(status: str) -> bool:
    return bool(status) and (status in HARD_FINAL_STATUSES or status in OLD_STATUS_MAP)


def _fs_sign(ts: str, secret: str) -> str:
    return base64.b64encode(hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()).decode()


def _append_limited(items: list[dict[str, Any]], item: dict[str, Any], keep: int = 50) -> None:
    items.append(item)
    if len(items) > keep:
        del items[:-keep]


def parse_rss_xml(content: bytes) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return entries

    ns = {"atom": "http://www.w3.org/2005/Atom"}

    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        url = ""
        enclosure = item.find("enclosure")
        if enclosure is not None and enclosure.get("url"):
            url = enclosure.get("url", "").strip()
        if not url:
            url = link
        if title and url:
            entries.append({"title": title, "url": url})

    if entries:
        return entries

    for item in root.findall(".//atom:entry", ns):
        title = (item.findtext("atom:title", default="", namespaces=ns) or "").strip()
        url = ""
        for link in item.findall("atom:link", ns):
            rel = link.get("rel", "")
            href = link.get("href", "").strip()
            if href and (rel in ("enclosure", "alternate", "") or not url):
                url = href
        if title and url:
            entries.append({"title": title, "url": url})

    return entries


@dataclass
class RSSItem:
    title: str
    url: str
    tid: str
    source: str


class QBError(Exception):
    pass


class AsyncQBClient:
    def __init__(self, cfg: dict[str, Any], timeout: int = 10,
                 fallback_download_dir: str | None = None,
                 fallback_local_dir: str | None = None):
        self.cfg = cfg
        self.base = cfg["url"].rstrip("/")
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))
        self._last_free_space: float | None = None
        self._space_reliable = False
        self._last_space_source = "unknown"
        self.fallback_paths = [p for p in [fallback_local_dir, fallback_download_dir] if p]

    async def __aenter__(self):
        await self.login()
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    async def login(self) -> None:
        for attempt in range(MAX_RETRIES):
            try:
                r = await self.client.post(
                    f"{self.base}/api/v2/auth/login",
                    data={"username": self.cfg.get("username", ""), "password": self.cfg.get("password", "")},
                )
                if r.status_code in (200, 204):
                    return
                if attempt == MAX_RETRIES - 1:
                    raise QBError(f"Login failed: {r.status_code} {r.text[:200]}")
            except httpx.RequestError as exc:
                if attempt == MAX_RETRIES - 1:
                    raise QBError(f"Login error: {exc}")
            await asyncio.sleep(RETRY_DELAY)

    async def _request(self, method: str, path: str, *, data: dict[str, Any] | None = None,
                       files: dict[str, Any] | None = None) -> httpx.Response:
        for attempt in range(MAX_RETRIES):
            try:
                resp = await self.client.request(method, f"{self.base}{path}", data=data, files=files)
                if resp.status_code == 200:
                    return resp
                if attempt == MAX_RETRIES - 1:
                    raise QBError(f"{method} {path} status={resp.status_code}")
            except httpx.RequestError as exc:
                if attempt == MAX_RETRIES - 1:
                    raise QBError(f"{method} {path} failed: {exc}")
            await asyncio.sleep(RETRY_DELAY)
        raise QBError(f"{method} {path} failed")

    async def torrents(self) -> list[dict[str, Any]]:
        return (await self._request("GET", "/api/v2/torrents/info")).json()

    async def add(self, url: str, savepath: str, tags: str | None = None) -> bool:
        data: dict[str, Any] = {"urls": url, "savepath": savepath}
        if tags:
            data["tags"] = tags
        resp = await self._request("POST", "/api/v2/torrents/add", data=data)
        body = (resp.text or "").strip().lower()
        if "fail" in body or "error" in body:
            raise QBError(f"qB rejected: {resp.text[:200]}")
        return True

    async def add_file(self, torrent_url: str, savepath: str, tags: str | None = None) -> bool:
        timeout = int(self.cfg.get("download_timeout", 120))
        torrent_data = await asyncio.to_thread(_curl_download, torrent_url, timeout)
        if not torrent_data or len(torrent_data) < 50:
            raise QBError(f"torrent file too small: {len(torrent_data)} bytes")
        if not torrent_data.startswith(b"d"):
            raise QBError(f"response not a torrent (starts with {torrent_data[:10]!r})")

        form_data: dict[str, Any] = {"savepath": savepath}
        if tags:
            form_data["tags"] = tags
        files = {"torrents": ("seed.torrent", torrent_data, "application/x-bittorrent")}
        resp = await self._request("POST", "/api/v2/torrents/add", data=form_data, files=files)
        body = (resp.text or "").strip().lower()
        if "fail" in body or "error" in body:
            raise QBError(f"qB rejected: {resp.text[:200]}")
        return True

    async def delete(self, torrent_hash: str, delete_files: bool = True) -> None:
        await self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={"hashes": torrent_hash, "deleteFiles": "true" if delete_files else "false"},
        )

    async def add_tags(self, torrent_hash: str, tags: str) -> None:
        if not tags:
            return
        await self._request("POST", "/api/v2/torrents/addTags", data={"hashes": torrent_hash, "tags": tags})

    def _local_free_space_gb(self) -> float | None:
        seen: set[str] = set()
        for path in self.fallback_paths:
            if not path or path in seen:
                continue
            seen.add(path)
            try:
                if not os.path.exists(path):
                    continue
                return shutil.disk_usage(path).free / 1024 / 1024 / 1024
            except Exception:
                continue
        return None

    def space_reliable(self) -> bool:
        return self._space_reliable

    def last_space_source(self) -> str:
        return self._last_space_source

    async def free_space_gb(self) -> float:
        md = (await self._request("GET", "/api/v2/sync/maindata")).json()
        server_state = md.get("server_state", {})
        raw_bytes = server_state.get("free_space_on_disk", None)

        def use_fallback(reason: str) -> float:
            fallback = self._local_free_space_gb()
            if fallback is not None and fallback > 0:
                self._last_free_space = fallback
                self._space_reliable = True
                self._last_space_source = f"local_fallback:{reason}"
                return fallback
            if self._last_free_space is not None and self._last_free_space > 0:
                self._space_reliable = True
                self._last_space_source = f"cached:{reason}"
                return self._last_free_space
            self._space_reliable = False
            self._last_space_source = f"unreliable:{reason}"
            return 0.0

        if raw_bytes is None:
            return use_fallback("missing")

        try:
            raw_bytes = float(raw_bytes)
        except (TypeError, ValueError):
            return use_fallback("non_numeric")

        space = raw_bytes / 1024 / 1024 / 1024
        if space <= 0:
            return use_fallback("invalid")

        self._last_free_space = space
        self._space_reliable = True
        self._last_space_source = "qb_api"
        return space


def _curl_download(url: str, timeout: int) -> bytes:
    cmd = ["curl", "-L", "--fail", "--silent", "--show-error", "--http1.1", "--max-time", str(timeout), url]
    result = subprocess.run(cmd, capture_output=True, check=True)
    return result.stdout


def build_passkey_download_url(config: dict[str, Any], tid: str) -> str | None:
    passkey = str(config.get("mt_passkey", "")).strip()
    if not passkey:
        return None
    domain = str(config.get("site_domain", "m-team.cc")).strip().rstrip("/")
    return f"https://{domain}/download.php?id={tid}&passkey={passkey}"


def parse_rss_xml(content: bytes) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return entries

    ns = {"atom": "http://www.w3.org/2005/Atom"}

    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        url = ""
        enclosure = item.find("enclosure")
        if enclosure is not None and enclosure.get("url"):
            url = enclosure.get("url", "").strip()
        if not url:
            url = link
        if title and url:
            entries.append({"title": title, "url": url})

    if entries:
        return entries

    for item in root.findall(".//atom:entry", ns):
        title = (item.findtext("atom:title", default="", namespaces=ns) or "").strip()
        url = ""
        for link in item.findall("atom:link", ns):
            rel = link.get("rel", "")
            href = link.get("href", "").strip()
            if href and (rel in ("enclosure", "alternate", "") or not url):
                url = href
        if title and url:
            entries.append({"title": title, "url": url})

    return entries


def parse_rss_with_retry(url: str, timeout: int = 30) -> list[dict[str, str]]:
    for attempt in range(MAX_RETRIES):
        try:
            resp = httpx.get(url, timeout=httpx.Timeout(15.0, read=float(timeout)), follow_redirects=True)
            resp.raise_for_status()
            entries = parse_rss_xml(resp.content)
            if entries:
                return entries
            if attempt == MAX_RETRIES - 1:
                raise RuntimeError("RSS parsed but no entries")
        except Exception:
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(RETRY_DELAY)
    return []


@dataclass
class RSSItem:
    title: str
    url: str
    tid: str
    source: str


class PTRSSPlugin(PluginBase):
    META = PluginMeta(
        slug="pt_rss",
        name="PT RSS 自动下载",
        description="RSS 订阅、qBittorrent 集成、Free 检测、做种策略、空间管理、RSS 驱逐",
        version="2.1.0",
        author="NASPilot",
        icon="🎥",
        category="pt",
        entrypoint="app.plugins.builtin.pt_rss",
    )

    @property
    def default_config(self) -> dict[str, Any]:
        return {
            "rss_urls": [],
            "qbittorrent": {"url": "", "username": "", "password": ""},
            "download_dir": "",
            "min_free_gb": 50,
            "max_active_downloads": 15,
            "free_check": False,
            "cleanup": {"seed_days": 2, "stuck_download_days": 3},
            "free_ttl_hours": 48,
            "rss_missing_threshold": 2,
            "enable_rss_eviction": True,
            "gc": {"evicted_days": 5, "expired_days": 5},
            "download_timeout": 120,
            "mt_passkey": "",
            "site_domain": "m-team.cc",
        }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config or {})
        self.config = self._merge_config(self.default_config, self.config)
        _ensure_daily_defaults(self.config)
        self._lock = asyncio.Lock()

    @staticmethod
    def _merge_config(defaults: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        merged = json.loads(json.dumps(defaults))
        for key, value in config.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged[key] = value
        return merged

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "rss_urls": {"type": "array", "items": {"type": "string"}, "title": "RSS 订阅链接"},
                "download_dir": {"type": "string", "title": "下载目录"},
                "min_free_gb": {"type": "integer", "title": "最小剩余空间 (GB)"},
                "max_active_downloads": {"type": "integer", "title": "最大同时下载数"},
                "free_check": {"type": "boolean", "title": "仅下载 FREE"},
                "qbittorrent": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "username": {"type": "string"},
                        "password": {"type": "string"},
                    },
                },
                "cleanup": {
                    "type": "object",
                    "properties": {
                        "seed_days": {"type": "number"},
                        "stuck_download_days": {"type": "number"},
                    },
                },
            },
        }

    async def on_enable(self) -> None:
        logger.info("PT RSS plugin enabled")

    async def on_disable(self) -> None:
        logger.info("PT RSS plugin disabled")

    def _rss_sources(self) -> list[str]:
        sources: list[str] = []
        raw_list = self.config.get("rss_urls")
        if isinstance(raw_list, str):
            # Form textarea saves as a single string — split by newline
            for line in raw_list.strip().split("\n"):
                line = line.strip()
                if line and line.startswith("http"):
                    sources.append(line)
        elif isinstance(raw_list, list):
            for item in raw_list:
                if isinstance(item, str) and item.strip():
                    sources.append(item.strip())
        legacy = self.config.get("rss_url")
        if isinstance(legacy, str) and legacy.strip():
            sources.append(legacy.strip())
        deduped: list[str] = []
        seen: set[str] = set()
        for src in sources:
            if src in seen:
                continue
            seen.add(src)
            deduped.append(src)
        return deduped

    def _cleanup_target(self) -> float:
        value = self.config.get("min_free_gb", 50)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 50.0

    async def _collect_rss_items(self) -> tuple[list[RSSItem], set[str]]:
        items: list[RSSItem] = []
        seen_tid: set[str] = set()
        failed_sources: set[str] = set()
        sources = self._rss_sources()
        if not sources:
            return items, failed_sources

        for src in sources:
            try:
                entries = await asyncio.to_thread(parse_rss_with_retry, src, 10)
            except Exception as exc:
                logger.warning("RSS source failed: %s (%s)", src, exc)
                failed_sources.add(src)
                continue

            for entry in entries:
                tid = extract_tid(entry["url"])
                if not tid or tid in seen_tid:
                    continue
                seen_tid.add(tid)
                items.append(RSSItem(title=entry["title"], url=entry["url"], tid=tid, source=src))

        return items, failed_sources

    async def _cleanup_for_new_task(self, qb: AsyncQBClient) -> dict[str, Any]:
        target_free_gb = self._cleanup_target()
        try:
            start_space = await qb.free_space_gb()
        except Exception as exc:
            logger.warning("Space check failed: %s", exc)
            return {"ok": True, "deleted": [], "start_space": 0.0, "end_space": 0.0}

        if not qb.space_reliable():
            return {"ok": True, "deleted": [], "start_space": start_space, "end_space": start_space}

        if start_space >= target_free_gb:
            return {"ok": True, "deleted": [], "start_space": start_space, "end_space": start_space}

        deleted: list[dict[str, Any]] = []
        needed = target_free_gb - start_space
        freed = 0.0
        torrents = await qb.torrents()
        stuck_days = float(self.config.get("cleanup", {}).get("stuck_download_days", 3))
        seed_days = float(self.config.get("cleanup", {}).get("seed_days", 2))

        async def delete_candidate(torrent: dict[str, Any], reason_detail: str) -> None:
            nonlocal freed
            tid = extract_tid_from_tags(torrent.get("tags", ""))
            if tid and is_hard_final_status(_processed(self.config).get(tid, {}).get("status", "")):
                return
            await qb.delete(torrent["hash"], delete_files=True)
            size_gb = float(torrent.get("total_size", 0)) / (1024 ** 3)
            freed += size_gb
            deleted.append({"name": torrent.get("name", "unknown")[:60], "size": round(size_gb, 2), "reason": reason_detail})

        for torrent in torrents:
            if torrent.get("progress", 0) < 1 and torrent.get("added_on") and days_since(float(torrent.get("added_on", 0))) >= stuck_days:
                await delete_candidate(torrent, "卡死下载")
                if freed >= needed:
                    break

        if freed < needed:
            for torrent in torrents:
                if torrent.get("progress", 0) != 1:
                    continue
                if float(torrent.get("seeding_time", 0)) / 86400 >= seed_days:
                    await delete_candidate(torrent, "做种超时")
                    if freed >= needed:
                        break

        await asyncio.sleep(2)
        try:
            end_space = await qb.free_space_gb()
        except Exception:
            end_space = start_space + freed

        return {"ok": end_space >= target_free_gb or start_space + freed >= target_free_gb, "deleted": deleted, "start_space": start_space, "end_space": max(end_space, start_space + freed)}

    async def _run_cycle(self) -> dict[str, Any]:
        qb_cfg = self.config.get("qbittorrent", {})
        if not qb_cfg.get("url"):
            return {"status": "failed", "error": "qBittorrent is not configured"}

        processed = _processed(self.config)
        _ensure_daily_defaults(self.config)
        daily = _daily(self.config)
        for rec in processed.values():
            _migrate_old_status(rec)
            rec.setdefault("rss_missing_count", 0)

        notify_added: list[str] = []
        notify_evicted: list[str] = []
        notify_failed: list[str] = []
        notify_skipped: list[str] = []

        async with AsyncQBClient(
            qb_cfg,
            timeout=10,
            fallback_download_dir=self.config.get("download_dir") or None,
            fallback_local_dir=self.config.get("space_fallback_dir") or None,
        ) as qb:
            emergency_result = await self._cleanup_for_new_task(qb)
            rss_items, failed_sources = await self._collect_rss_items()
            rss_tid_set = {item.tid for item in rss_items}

            added = 0
            max_active = int(self.config.get("max_active_downloads", 0) or 0)
            for item in rss_items:
                if max_active and added >= max_active:
                    break

                rec = processed.setdefault(item.tid, {
                    "title": item.title,
                    "first_seen": utc_now_iso(),
                    "status": STATUS_PENDING_FREE,
                    "rss_missing_count": 0,
                    "rss_source": item.source,
                })

                if is_final_status(rec.get("status", "")):
                    continue

                free_ttl_hours = float(self.config.get("free_ttl_hours", 48))
                if rec.get("status") == STATUS_PENDING_FREE and hours_since_iso(rec.get("first_seen", "")) > free_ttl_hours:
                    rec["status"] = STATUS_EXPIRED_FREE
                    daily["stats"]["expired_free"] += 1
                    continue

                if rec.get("status") != STATUS_PENDING_FREE:
                    continue

                tag = f"rss_tid:{item.tid}"
                existing = await qb.torrents()
                matched = next((t for t in existing if has_tag(t, tag)), None)
                if matched is None:
                    normalized = normalize_title(item.title)
                    matched = next((t for t in existing if normalize_title(t.get("name", "")) == normalized), None)

                if matched is not None:
                    if not has_tag(matched, tag):
                        try:
                            await qb.add_tags(matched.get("hash", ""), tag)
                        except Exception:
                            pass
                    if matched.get("progress", 0) >= 1:
                        rec["status"] = STATUS_COMPLETED
                        rec.setdefault("completed_time", utc_now_iso())
                    else:
                        rec["status"] = STATUS_ADDED
                        rec.setdefault("added_time", utc_now_iso())
                    notify_added.append(f"♻️ 已存在：{item.title[:50]}")
                    continue

                if not emergency_result.get("ok", True):
                    continue

                added_ok = False
                last_error = ""
                urls_to_try = [item.url]
                passkey_url = build_passkey_download_url(self.config, item.tid)
                if passkey_url:
                    urls_to_try.append(passkey_url)

                for try_url in urls_to_try:
                    try:
                        await qb.add_file(try_url, savepath=self.config.get("download_dir", ""), tags=tag)
                        added_ok = True
                        break
                    except Exception as exc:
                        last_error = str(exc)
                    try:
                        await qb.add(try_url, savepath=self.config.get("download_dir", ""), tags=tag)
                        added_ok = True
                        break
                    except Exception as exc:
                        last_error = str(exc)

                if not added_ok:
                    rec["last_add_error"] = last_error
                    rec["last_add_error_time"] = utc_now_iso()
                    notify_failed.append(f"{item.tid} | {item.title[:40]} | {last_error[:180]}")
                    continue

                rec.update({"status": STATUS_ADDED, "added_time": utc_now_iso(), "tag": tag, "title": item.title})
                daily["stats"]["added"] += 1
                _append_limited(daily["details"]["added_items"], {"time": rec["added_time"], "tid": item.tid, "title": item.title, "tag": tag})
                notify_added.append(f"✅ 新增下载：{item.title[:50]}")
                added += 1

            for tid, rec in processed.items():
                if is_final_status(rec.get("status", "")) or rec.get("status") != STATUS_ADDED:
                    continue
                if tid in rss_tid_set:
                    rec["rss_missing_count"] = 0
                    continue
                source = rec.get("rss_source", "")
                if source and source in failed_sources:
                    continue
                rec["rss_missing_count"] = int(rec.get("rss_missing_count", 0)) + 1
                if rec["rss_missing_count"] < int(self.config.get("rss_missing_threshold", 2)):
                    notify_skipped.append(f"{tid} | missing {rec['rss_missing_count']}")
                    continue
                candidates = [t for t in await qb.torrents() if has_tag(t, rec.get("tag", f"rss_tid:{tid}"))]
                if len(candidates) != 1:
                    continue
                torrent = candidates[0]
                if torrent.get("progress", 0) >= 1:
                    rec["status"] = STATUS_COMPLETED
                    rec["completed_time"] = utc_now_iso()
                    rec["rss_missing_count"] = 0
                    continue
                await qb.delete(torrent["hash"], delete_files=True)
                rec["status"] = STATUS_EVICTED
                rec["evicted_time"] = utc_now_iso()
                rec["evicted_reason"] = "rss"
                rec["rss_missing_count"] = 0
                daily["stats"]["deleted_rss_missing"] += 1
                _append_limited(daily["details"]["deleted_items"], {"time": utc_now_iso(), "tid": tid, "name": torrent.get("name", "unknown"), "reason": "rss"})
                notify_evicted.append(f"{tid} | {torrent.get('name', '')[:50]} | RSS缺席")

        return {
            "status": "ok",
            "rss_sources": len(self._rss_sources()),
            "rss_items_found": len(rss_items),
            "rss_failed_sources": list(failed_sources),
            "added": daily["stats"].get("added", 0),
            "added_messages": notify_added,
            "deleted_messages": notify_evicted,
            "failed_messages": notify_failed,
            "skipped_messages": notify_skipped,
        }

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        async with self._lock:
            return await self._run_cycle()
