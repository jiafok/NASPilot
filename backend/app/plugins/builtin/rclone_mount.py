"""Rclone Mount Plugin — ports rclone_mount_simple.sh for mounting Alist remote via rclone."""

import asyncio
import logging
from typing import Any

from app.plugins.registry import PluginBase, PluginMeta

logger = logging.getLogger("naspilot.plugins.rclone_mount")


class RcloneMountPlugin(PluginBase):
    META = PluginMeta(
        slug="rclone_mount",
        name="Rclone Mount",
        description="Mount Alist remote via rclone FUSE with caching. Port of rclone_mount_simple.sh. Synology-optimized.",
        version="1.0.0",
        category="storage",
    )

    async def on_enable(self) -> None:
        pass

    async def on_disable(self) -> None:
        pass

    async def run(self, **kwargs: Any) -> Any:
        """Check rclone mount status or perform mount/unmount."""
        import traceback

        try:
            return await self._run_impl(**kwargs)
        except Exception as exc:
            logger.exception("Rclone Mount run failed")
            return {"status": "error", "error": str(exc)[:500], "action": kwargs.get("action", "unknown")}

    async def _run_impl(self, **kwargs: Any) -> Any:
        """Check rclone mount status or perform mount/unmount."""
        mount_point = self.config.get("mount_point", "/volume1/docker/Alist/media")
        remote = self.config.get("remote", "alist:/")
        action = kwargs.get("action", "status")
        logger.info("Rclone mount: action=%s, mount=%s, remote=%s", action, mount_point, remote)

        result = {"action": action, "mount_point": mount_point, "remote": remote}

        if action == "status":
            proc = await asyncio.create_subprocess_exec("mountpoint", mount_point,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await asyncio.wait_for(proc.communicate(), timeout=5)
            result["mounted"] = proc.returncode == 0
        elif action == "mount":
            cmd = [
                "rclone", "mount", remote, mount_point,
                "--allow-other", "--allow-non-empty",
                "--vfs-cache-mode", "full",
                "--vfs-cache-max-size", self.config.get("cache_size", "10G"),
                "--vfs-cache-max-age", f"{self.config.get('cache_age_m', 15)}m",
                "--buffer-size", "32M",
                "--daemon",
                "--config", self.config.get("config_file", f"{self.config.get('home', '/root')}/.config/rclone/rclone.conf"),
            ]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            result["exit_code"] = proc.returncode
            result["mounted"] = proc.returncode == 0
            if stderr:
                result["stderr"] = stderr.decode()[:1000]
        elif action == "unmount":
            proc = await asyncio.create_subprocess_exec("fusermount", "-uz", mount_point,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await asyncio.wait_for(proc.communicate(), timeout=10)
            result["unmounted"] = proc.returncode == 0

        return result
