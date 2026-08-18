"""Cloudflare DDNS Plugin — IPv4/IPv6 DDNS update via Cloudflare DNS API.

Ported from update_cloudflare.sh logic:
- Detect current public IPv4 (via api.ipify.org / ipv4.icanhazip.com)
- Detect current public IPv6 (via network interface or ipv6.icanhazip.com)
- Update Cloudflare DNS A/AAAA records only when IP changes
- Supports multiple zones and multiple record names per zone
- State (last known IPs) stored in config["state"]
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.plugins.registry import PluginBase, PluginMeta

logger = logging.getLogger("naspilot.plugin.cloudflare_ddns")

LOCAL_TZ = timezone(timedelta(hours=8))
CF_API = "https://api.cloudflare.com/client/v4"


def _normalize_zones(value: Any) -> list[dict[str, Any]]:
    """Parse zones config from form (may be JSON string, comma-separated, or already a list)."""
    if isinstance(value, list):
        return [z for z in value if isinstance(z, dict)]
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        # Try JSON parse — auto-wrap single object in array
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [z for z in parsed if isinstance(z, dict)]
            if isinstance(parsed, dict):
                return [parsed]
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _now_iso() -> str:
    return datetime.now(LOCAL_TZ).isoformat()


async def _get_public_ipv4() -> str | None:
    sources = [
        "https://api.ipify.org",
        "https://ipv4.icanhazip.com",
        "https://checkip.amazonaws.com",
    ]
    async with httpx.AsyncClient(timeout=10) as client:
        for url in sources:
            try:
                resp = await client.get(url)
                ip = resp.text.strip()
                if ip:
                    # Validate it looks like an IPv4
                    socket.inet_pton(socket.AF_INET, ip)
                    return ip
            except Exception:
                continue
    return None


async def _get_public_ipv6(iface: str = "") -> str | None:
    # Try to get via network interface first (same as ip -6 addr show)
    if iface:
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["ip", "-6", "addr", "show", "dev", iface, "scope", "global"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("inet6 ") and "scope global" not in line:
                    continue
                if line.startswith("inet6 "):
                    addr = line.split()[1].split("/")[0]
                    if not addr.startswith(("fe80:", "fd", "fc")):
                        return addr
        except Exception:
            pass
    # Fallback: public resolver
    async with httpx.AsyncClient(timeout=10) as client:
        for url in ["https://ipv6.icanhazip.com", "https://api6.ipify.org"]:
            try:
                resp = await client.get(url)
                ip = resp.text.strip()
                if ip:
                    socket.inet_pton(socket.AF_INET6, ip)
                    return ip
            except Exception:
                continue
    return None


class CloudflareClient:
    def __init__(self, api_token: str):
        self.api_token = api_token
        self._headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        # Reuse one client across all records — avoids re-establishing TLS for
        # each of N records, which was the cause of "1 domain ok, 17 domains fail".
        #
        # Bind to IPv4 explicitly: many Synology/NAS containers can reach
        # api.cloudflare.com over IPv4 but their IPv6 egress times out. Binding
        # the local socket to 0.0.0.0 forces IPv4 for the Cloudflare API only,
        # while public IPv6 detection (icanhazip/api6.ipify) still runs over IPv6.
        self._client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
            timeout=httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        retries: int = 3,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                return await self._client.request(
                    method, url, headers=self._headers, params=params, json=json_body
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < retries - 1:
                    delay = 2.0 * (attempt + 1)
                    logger.warning(
                        "Cloudflare API %s %s failed (attempt %d/%d): %s — retrying in %.0fs",
                        method, url, attempt + 1, retries, type(exc).__name__, delay,
                    )
                    await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    async def list_zones(self) -> list[dict[str, Any]]:
        resp = await self._request("GET", f"{CF_API}/zones")
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"list_zones failed: {data.get('errors')}")
        return data.get("result", [])

    async def list_records(self, zone_id: str, name: str, rtype: str) -> list[dict[str, Any]]:
        resp = await self._request(
            "GET",
            f"{CF_API}/zones/{zone_id}/dns_records",
            params={"type": rtype, "name": name},
        )
        data = resp.json()
        return data.get("result", []) if data.get("success") else []

    async def update_record(self, zone_id: str, record_id: str,
                             rtype: str, name: str, content: str, proxied: bool = False) -> bool:
        resp = await self._request(
            "PUT",
            f"{CF_API}/zones/{zone_id}/dns_records/{record_id}",
            json_body={"type": rtype, "name": name, "content": content, "proxied": proxied, "ttl": 1},
        )
        return resp.json().get("success", False)

    async def create_record(self, zone_id: str, rtype: str, name: str,
                             content: str, proxied: bool = False) -> bool:
        resp = await self._request(
            "POST",
            f"{CF_API}/zones/{zone_id}/dns_records",
            json_body={"type": rtype, "name": name, "content": content, "proxied": proxied, "ttl": 1},
        )
        return resp.json().get("success", False)

    async def upsert_record(self, zone_id: str, rtype: str, name: str,
                             content: str, proxied: bool = False) -> str:
        """Create or update record. Returns 'created'|'updated'|'unchanged'.

        Compares both IP content AND proxied flag so proxy toggles are synced
        even when the IP has not changed."""
        records = await self.list_records(zone_id, name, rtype)
        if records:
            rec = records[0]
            same_content = rec.get("content") == content
            same_proxied = bool(rec.get("proxied")) == bool(proxied)
            if same_content and same_proxied:
                return "unchanged"
            ok = await self.update_record(zone_id, rec["id"], rtype, name, content, proxied)
            return "updated" if ok else "error"
        ok = await self.create_record(zone_id, rtype, name, content, proxied)
        return "created" if ok else "error"


class CloudflareDDNSPlugin(PluginBase):
    META = PluginMeta(
        slug="cloudflare_ddns",
        name="Cloudflare DDNS",
        description="IPv4/IPv6 自动更新、多域名管理、Zone 管理",
        version="1.0.0",
        author="NASPilot",
        icon="🌐",
        category="network",
        entrypoint="app.plugins.builtin.cloudflare_ddns",
    )

    @property
    def default_config(self) -> dict[str, Any]:
        return {
            "api_token": "",
            "iface": "",   # network interface for IPv6 detection, optional
            "zones": [
                # Example:
                # {
                #   "zone_id": "abc123",
                #   "records": ["home.example.com"],
                #   "ip_type": "both",    # ipv4 | ipv6 | both
                #   "proxied": false
                # }
            ],
        }

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "api_token": {"type": "string", "title": "Cloudflare API Token"},
                "iface": {"type": "string", "title": "IPv6 网卡名（可选，留空自动检测）"},
                "zones": {
                    "type": "array",
                    "title": "Zones",
                    "items": {
                        "type": "object",
                        "properties": {
                            "zone_id": {"type": "string", "title": "Zone ID"},
                            "records": {"type": "array", "items": {"type": "string"}, "title": "记录名列表"},
                            "ip_type": {"type": "string", "enum": ["ipv4", "ipv6", "both"], "title": "IP 类型"},
                            "proxied": {"type": "boolean", "title": "启用 Cloudflare 代理"},
                        },
                    },
                },
            },
        }

    async def on_enable(self) -> None:
        logger.info("Cloudflare DDNS plugin enabled")

    async def on_disable(self) -> None:
        logger.info("Cloudflare DDNS plugin disabled")

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        try:
            return await self._run_impl(**kwargs)
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}".strip().rstrip(":").strip()
            if not error_text:
                error_text = type(exc).__name__
            logger.exception("Cloudflare DDNS run failed: %s", error_text)
            return {"status": "error", "error": error_text, "ipv4": None, "ipv6": None, "updated": 0, "unchanged": 0, "results": []}

    async def _run_impl(self, **kwargs: Any) -> dict[str, Any]:
        api_token = self.config.get("api_token", "").strip()
        if not api_token:
            logger.warning("API Token is not configured")
            return {"status": "failed", "error": "API Token is not configured"}

        zones: list[dict[str, Any]] = _normalize_zones(self.config.get("zones"))
        if not zones:
            logger.warning("No zones configured")
            return {"status": "failed", "error": "No zones configured"}

        iface = self.config.get("iface", "").strip()
        logger.info("Starting DDNS update, zones=%d", len(zones))
        cf = CloudflareClient(api_token)
        state = self.config.setdefault("state", {})
        results: list[dict[str, Any]] = []

        try:
            # Detect IPs
            ipv4: str | None = None
            ipv6: str | None = None
            needs_v4 = any(z.get("ip_type", "both") in ("ipv4", "both") for z in zones)
            needs_v6 = any(z.get("ip_type", "both") in ("ipv6", "both") for z in zones)

            if needs_v4:
                ipv4 = await _get_public_ipv4()
                logger.info("Public IPv4: %s", ipv4)
            if needs_v6:
                ipv6 = await _get_public_ipv6(iface)
                logger.info("Public IPv6: %s", ipv6)

            updated = 0
            unchanged = 0

            for zone in zones:
                zone_id = zone.get("zone_id", "").strip()
                records: list[str] = zone.get("records") or []
                ip_type = zone.get("ip_type", "both")
                proxied = bool(zone.get("proxied", False))

                if not zone_id or not records:
                    continue

                for record_name in records:
                    if ip_type in ("ipv4", "both") and ipv4:
                        res = await cf.upsert_record(zone_id, "A", record_name, ipv4, proxied)
                        results.append({"record": record_name, "type": "A", "ip": ipv4, "result": res, "time": _now_iso()})
                        if res in ("created", "updated"):
                            updated += 1
                        else:
                            unchanged += 1

                    if ip_type in ("ipv6", "both") and ipv6:
                        res = await cf.upsert_record(zone_id, "AAAA", record_name, ipv6, proxied)
                        results.append({"record": record_name, "type": "AAAA", "ip": ipv6, "result": res, "time": _now_iso()})
                        if res in ("created", "updated"):
                            updated += 1
                        else:
                            unchanged += 1
        finally:
            await cf.close()

        state["last_run"] = _now_iso()
        state["last_ipv4"] = ipv4
        state["last_ipv6"] = ipv6
        # Keep last 50 results in history
        history: list[dict[str, Any]] = state.setdefault("history", [])
        history.extend(results)
        if len(history) > 50:
            state["history"] = history[-50:]

        return {
            "status": "ok",
            "ipv4": ipv4,
            "ipv6": ipv6,
            "updated": updated,
            "unchanged": unchanged,
            "results": results,
        }
