"""System service — hardware stats, Docker/QB/AList health checks."""

import logging
import socket
from typing import Any

import httpx
import psutil

from app.core.config import settings

logger = logging.getLogger("naspilot.system")


def get_system_stats() -> dict[str, Any]:
    """Gather CPU, memory, and disk stats for the dashboard."""
    cpu_percent = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    try:
        disk = psutil.disk_usage("/")
        disk_total, disk_used, disk_percent = disk.total, disk.used, disk.percent
    except Exception:
        disk_total = disk_used = 0
        disk_percent = 0.0
    return {
        "cpu_percent": cpu_percent,
        "cpu_count": psutil.cpu_count() or 0,
        "memory_total": mem.total,
        "memory_used": mem.used,
        "memory_percent": mem.percent,
        "disk_total": disk_total,
        "disk_used": disk_used,
        "disk_percent": disk_percent,
        "docker_status": {},
        "qbittorrent_status": {},
        "alist_status": {},
    }


async def check_docker() -> dict[str, Any]:
    """Check Docker daemon connectivity via sock."""
    try:
        import docker  # type: ignore

        client = docker.from_env(socket_path=settings.DOCKER_SOCK)
        info = client.info()
        return {"status": "online", "containers": info.get("Containers", 0), "running": info.get("ContainersRunning", 0)}
    except Exception as e:
        return {"status": "offline", "error": str(e)}


async def check_qbittorrent(url: str, username: str, password: str) -> dict[str, Any]:
    """Check qBittorrent WebAPI connectivity."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{url}/api/v2/auth/login",
                data={"username": username, "password": password},
            )
            if resp.text.strip() != "Ok.":
                return {"status": "error", "error": "Auth failed"}
            info = await client.get(f"{url}/api/v2/transfer/info")
            return {"status": "online", **info.json()}
    except Exception as e:
        return {"status": "offline", "error": str(e)}


async def check_alist(url: str) -> dict[str, Any]:
    """Check AList API connectivity via a lightweight ping."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{url}/api/public/settings")
            data = resp.json()
            return {"status": "online" if data.get("code") == 200 else "error", "data": data}
    except Exception as e:
        return {"status": "offline", "error": str(e)}
