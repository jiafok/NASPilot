"""Btrfs Cleanup Plugin — ports clean_btrfs.sh for orphaned Docker subvolumes."""

import asyncio
import logging
from typing import Any

from app.plugins.registry import PluginBase, PluginMeta

logger = logging.getLogger("naspilot.plugins.btrfs_cleanup")


class BtrfsCleanupPlugin(PluginBase):
    META = PluginMeta(
        slug="btrfs_cleanup",
        name="Btrfs Subvolume Cleanup",
        description="Identify and clean orphaned Docker btrfs subvolumes to reclaim disk space. Port of clean_btrfs.sh.",
        version="1.0.0",
        category="system",
    )

    async def on_enable(self) -> None:
        pass

    async def on_disable(self) -> None:
        pass

    async def run(self, **kwargs: Any) -> Any:
        """Scan btrfs subvolumes and report orphaned ones."""
        import traceback

        try:
            return await self._run_impl(**kwargs)
        except Exception as exc:
            logger.exception("Btrfs Cleanup run failed")
            return {"status": "error", "error": str(exc)[:500], "orphaned": [], "errors": [str(exc)]}

    async def _run_impl(self, **kwargs: Any) -> Any:
        subvol_path = self.config.get("subvol_path", "/volume1/@docker/btrfs/subvolumes")
        try:
            min_age_days = max(0, int(self.config.get("min_age_days", 7)))
        except (TypeError, ValueError):
            min_age_days = 7
        dry_run = self.config.get("dry_run", True)

        result = {"subvol_path": subvol_path, "orphaned": [], "total_size_bytes": 0, "errors": []}

        try:
            cmd = ["find", subvol_path, "-mindepth", "1", "-maxdepth", "1", "-type", "d", "-mtime", f"+{min_age_days}"]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if stderr:
                result["errors"].append(stderr.decode())

            subdirs = [d.strip() for d in stdout.decode().split("\n") if d.strip()]
            result["orphaned"] = subdirs
            result["count"] = len(subdirs)

            if not dry_run and subdirs:
                for d in subdirs:
                    try:
                        rm_proc = await asyncio.create_subprocess_exec("btrfs", "subvolume", "delete", d)
                        await asyncio.wait_for(rm_proc.communicate(), timeout=30)
                        result["deleted"] = result.get("deleted", 0) + 1
                    except Exception as e:
                        result["errors"].append(f"Failed to delete {d}: {e}")
                result["dry_run"] = False
            else:
                result["dry_run"] = True
                result["message"] = f"Dry run — {len(subdirs)} subvolumes would be deleted"

        except Exception as e:
            result["errors"].append(str(e))
            logger.exception("Btrfs cleanup error")

        return result
