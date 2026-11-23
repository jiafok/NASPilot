import asyncio
import errno
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.plugins.builtin import cf_pages
from app.plugins.builtin.rclone_mount import RcloneMountPlugin


class RcloneMountTests(unittest.IsolatedAsyncioTestCase):
    async def test_mount_creates_directory_and_checks_writability(self):
        with tempfile.TemporaryDirectory() as directory:
            mount = Path(directory) / "mount"
            plugin = RcloneMountPlugin({"mount_point": str(mount)})
            plugin._preflight = MagicMock(return_value=[])
            plugin._is_mounted = AsyncMock(return_value=False)
            plugin._write_config = AsyncMock(side_effect=RuntimeError("config reached"))

            result = await plugin.run(action="mount")

            self.assertEqual(result["error"], "config reached")
            self.assertTrue(mount.is_dir())
            plugin._write_config.assert_awaited_once()

    def test_mount_point_cannot_be_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file"
            path.write_text("keep", encoding="utf-8")
            writable, error, err_no = RcloneMountPlugin._dir_writable(path)
            self.assertFalse(writable)
            self.assertTrue(error)
            self.assertNotEqual(err_no, errno.ENOTCONN)
            self.assertEqual(path.read_text(encoding="utf-8"), "keep")

    async def test_dead_fuse_mount_is_stale_not_unmounted(self):
        """A kernel mount whose rclone process died must not read as "absent".

        `mountpoint -q` stat()s the path, so it fails with ENOTCONN and the
        plugin used to treat the corpse as an ordinary empty directory.
        """
        plugin = RcloneMountPlugin({"mount_point": "/app/data/alist"})
        with patch.object(plugin, "_mount_entries", return_value=["alist:"]), patch.object(
            plugin, "_is_connected", return_value=False
        ):
            self.assertEqual(await plugin._mount_state("/app/data/alist"), ("stale", "alist:"))
            self.assertFalse(await plugin._is_mounted("/app/data/alist"))

        with patch.object(plugin, "_mount_entries", return_value=["alist:"]), patch.object(
            plugin, "_is_connected", return_value=True
        ):
            self.assertEqual(await plugin._mount_state("/app/data/alist"), ("mounted", "alist:"))
            self.assertTrue(await plugin._is_mounted("/app/data/alist"))

        with patch.object(plugin, "_mount_entries", return_value=[]):
            self.assertEqual(await plugin._mount_state("/app/data/alist"), ("unmounted", ""))
            self.assertFalse(await plugin._is_mounted("/app/data/alist"))

    async def test_stale_mount_is_unmounted_then_remounted(self):
        with tempfile.TemporaryDirectory() as directory:
            mount = Path(directory) / "alist"
            mount.mkdir()
            plugin = RcloneMountPlugin({"mount_point": str(mount)})
            plugin._preflight = MagicMock(return_value=[])
            plugin._force_unmount = AsyncMock(return_value=(True, ""))
            plugin._write_config = AsyncMock(side_effect=RuntimeError("config reached"))
            with patch.object(plugin, "_mount_state", AsyncMock(return_value=("stale", "alist:"))):
                result = await plugin.run(action="mount")

            plugin._force_unmount.assert_awaited_once_with(str(mount))
            plugin._write_config.assert_awaited_once()
            self.assertEqual(result["error"], "config reached")

    async def test_stale_mount_reports_actionable_error_when_cleanup_fails(self):
        plugin = RcloneMountPlugin({"mount_point": "/app/data/alist"})
        plugin._preflight = MagicMock(return_value=[])
        plugin._force_unmount = AsyncMock(return_value=(False, "umount -l: Device or resource busy"))
        with patch.object(plugin, "_mount_state", AsyncMock(return_value=("stale", "alist:"))):
            result = await plugin.run(action="mount")

        self.assertEqual(result["status"], "error")
        self.assertTrue(result["stale_mount"])
        self.assertIn("umount -l", result["error"])
        self.assertIn("umount -l", result["hint"])

    async def test_enotconn_writability_triggers_forced_cleanup(self):
        """ENOTCONN from the probe is proof of a corpse even if /proc/mounts missed."""
        plugin = RcloneMountPlugin({"mount_point": "/app/data/alist"})
        plugin._preflight = MagicMock(return_value=[])
        plugin._force_unmount = AsyncMock(return_value=(True, ""))
        probes = iter(
            [
                (False, "[Errno 107] Transport endpoint is not connected", errno.ENOTCONN),
                (True, "", 0),
            ]
        )
        plugin._dir_writable = MagicMock(side_effect=lambda _path: next(probes))
        plugin._write_config = AsyncMock(side_effect=RuntimeError("config reached"))
        with patch.object(plugin, "_mount_state", AsyncMock(return_value=("unmounted", ""))):
            await plugin.run(action="mount")

        plugin._force_unmount.assert_awaited_once()
        self.assertEqual(plugin._dir_writable.call_count, 2)

    async def test_status_flags_stale_mount(self):
        plugin = RcloneMountPlugin({"mount_point": "/app/data/alist"})
        plugin._preflight = MagicMock(return_value=[])
        with patch.object(plugin, "_mount_state", AsyncMock(return_value=("stale", "alist:"))):
            result = await plugin.run(action="status")

        self.assertFalse(result["mounted"])
        self.assertTrue(result["stale_mount"])
        self.assertEqual(result["mount_state"], "stale")
        self.assertFalse(result["ready"])
        self.assertTrue(any("失效的 FUSE 挂载" in p for p in result["problems"]))

    async def test_unmount_clears_a_stale_mount(self):
        plugin = RcloneMountPlugin({"mount_point": "/app/data/alist"})
        plugin._force_unmount = AsyncMock(return_value=(True, ""))
        with patch.object(plugin, "_mount_state", AsyncMock(return_value=("stale", "alist:"))):
            result = await plugin.run(action="unmount")

        plugin._force_unmount.assert_awaited_once_with("/app/data/alist")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["unmounted"])
        self.assertEqual(result["recovered_from"], "stale")


class RcloneSelfHealTests(unittest.IsolatedAsyncioTestCase):
    """The watchdog is what makes a container restart recover on its own."""

    async def test_healthy_mount_does_not_remount(self):
        from app.plugins.builtin import rclone_mount as module

        remount = AsyncMock(return_value={"status": "ok"})
        with patch.object(module, "_load_instance_config", AsyncMock(return_value={})), patch.object(
            RcloneMountPlugin, "_mount_state", AsyncMock(return_value=("mounted", "alist:"))
        ), patch.object(RcloneMountPlugin, "run", remount), patch.object(
            module.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)
        ):
            with self.assertRaises(asyncio.CancelledError):
                await module._self_heal_loop()

        remount.assert_not_awaited()

    async def test_stale_mount_is_healed_by_the_loop(self):
        from app.plugins.builtin import rclone_mount as module

        remount = AsyncMock(return_value={"status": "ok"})
        with patch.object(
            module, "_load_instance_config",
            AsyncMock(return_value={"alist_url": "https://a", "username": "u"}),
        ), patch.object(
            RcloneMountPlugin, "_mount_state", AsyncMock(return_value=("stale", "alist:"))
        ), patch.object(RcloneMountPlugin, "run", remount), patch.object(
            module.asyncio, "sleep", AsyncMock(side_effect=[None, asyncio.CancelledError])
        ):
            with self.assertRaises(asyncio.CancelledError):
                await module._self_heal_loop()

        remount.assert_awaited_once_with(action="ensure")

    async def test_backoff_grows_and_is_capped(self):
        from app.plugins.builtin import rclone_mount as module

        self.assertEqual(module._heal_delay(60, 0), 60.0)
        self.assertEqual(module._heal_delay(60, 1), 120.0)
        self.assertEqual(module._heal_delay(60, 2), 240.0)
        self.assertEqual(module._heal_delay(60, 99), module._HEAL_MAX_BACKOFF)

    async def test_watchdog_is_started_by_the_startup_hook(self):
        from app.plugins.builtin import rclone_mount as module

        plugin = RcloneMountPlugin({"autostart": True, "alist_url": "https://a", "username": "u"})
        with patch.object(module, "_start_watchdog") as start, patch.object(
            RcloneMountPlugin, "_mount_state", AsyncMock(return_value=("mounted", "alist:"))
        ):
            await plugin.on_startup()

        start.assert_called_once()

    async def test_start_watchdog_is_idempotent(self):
        from app.plugins.builtin import rclone_mount as module

        with patch.object(module, "_watchdog_task", None), patch.object(
            module, "_self_heal_loop", AsyncMock(return_value=None)
        ):
            module._start_watchdog()
            first = module._watchdog_task
            self.assertIsNotNone(first)

            # A second call must not spawn a duplicate loop.
            module._start_watchdog()
            self.assertIs(module._watchdog_task, first)

            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first

    def test_watchdog_config_defaults_to_enabled(self):
        cfg = RcloneMountPlugin()._cfg()
        self.assertEqual(cfg["autoheal_seconds"], 60)