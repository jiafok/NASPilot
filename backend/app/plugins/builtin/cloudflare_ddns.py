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

    async def list_zones(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{CF_API}/zones", headers=self._headers)
            data = resp.json()
            if not data.get("success"):
                raise RuntimeError(f"list_zones failed: {data.get('errors')}")
            return data.get("result", [])

    async def list_records(self, zone_id: str, name: str, rtype: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{CF_API}/zones/{zone_id}/dns_records",
                headers=self._headers,
                params={"type": rtype, "name": name},
            )
            data = resp.json()
            return data.get("result", []) if data.get("success") else []

    async def update_record(self, zone_id: str, record_id: str,
                             rtype: str, name: str, content: str, proxied: bool = False) -> bool:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.put(
                f"{CF_API}/zones/{zone_id}/dns_records/{record_id}",
                headers=self._headers,
                json={"type": rtype, "name": name, "content": content, "proxied": proxied, "ttl": 1},
            )
            return resp.json().get("success", False)

    async def create_record(self, zone_id: str, rtype: str, name: str,
                             content: str, proxied: bool = False) -> bool:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{CF_API}/zones/{zone_id}/dns_records",
                headers=self._headers,
                json={"type": rtype, "name": name, "content": content, "proxied": proxied, "ttl": 1},
            )
            return resp.json().get("success", False)

    async def upsert_record(self, zone_id: str, rtype: str, name: str,
                             content: str, proxied: bool = False) -> str:
        """Create or update record. Returns 'created'|'updated'|'unchanged'."""
        records = await self.list_records(zone_id, name, rtype)
        if records:
            if records[0].get("content") == content:
                return "unchanged"
            ok = await self.update_record(zone_id, records[0]["id"], rtype, name, content, proxied)
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
        import traceback
        try:
            return await self._run_impl(**kwargs)
        except Exception as exc:
            logger.exception("Cloudflare DDNS run failed")
            return {"status": "error", "error": str(exc)[:500], "ipv4": None, "ipv6": None, "updated": 0, "unchanged": 0, "results": []}

    async def _run_impl(self, **kwargs: Any) -> dict[str, Any]:
        api_token = self.config.get("api_token", "").strip()
        if not api_token:
            logger.warning("API Token is not configured")
            return {"status": "failed", "error": "API Token is not configured"}

        zones: list[dict[str, Any]] = self.config.get("zones") or []
        if not zones:
            logger.warning("No zones configured")
            return {"status": "failed", "error": "No zones configured"}

        iface = self.config.get("iface", "").strip()
        logger.info("Starting DDNS update, zones=%d", len(zones))
        cf = CloudflareClient(api_token)
        state = self.config.setdefault("state", {})
        results: list[dict[str, Any]] = []

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
