"""Rclone Mount Plugin — mount an AList / WebDAV remote *inside* the container.

Why inside the container
------------------------
Nothing is installed on the NAS: rclone ships in the image (see the
``rclone-static`` stage in the Dockerfile) and the FUSE mount is created in the
container's own mount namespace. Everything the plugin needs — the rclone
config, the VFS cache and the mount point itself — lives under ``DATA_DIR``,

i.e. the volume already mounted at ``/app/data``.

That is the whole point: the host sees the mount through the same bind mount
that already carries the database and the media library, so ``./data`` stays
self-contained. Copy that directory to another NAS, start the container, and
the mount is restored on boot with no NAS-side configuration at all.

What the compose file must provide
----------------------------------
* ``devices: /dev/fuse`` and ``cap_add: SYS_ADMIN`` — a container cannot
  create a device node, and FUSE mounting needs the capability.
* the data bind mount declared ``:rshared``. Docker bind mounts are
  ``rprivate`` by default, which means a mount created *inside* the container
  never propagates back out — the host would see an empty directory. Without
  ``rshared`` the plugin still works (the container can read the remote) but
  the "see it from the host" half of the feature does not.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.plugins.registry import PluginBase, PluginMeta

logger = logging.getLogger("naspilot.plugins.rclone_mount")

#: FUSE device. Present only when the compose file forwards it.
FUSE_DEVICE = "/dev/fuse"

#: Names rclone's CLI has used for the unmount helper across fuse2/fuse3.
UNMOUNT_HELPERS = ("fusermount3", "fusermount")

ACTIONS = ("status", "mount", "unmount", "restart", "ensure")


def _config_dir() -> Path:
    """Directory holding rclone.conf, the VFS cache and the mount log.

    Derived from ``DATA_DIR`` on purpose — that path is the mounted volume, so
    the remote definition survives ``docker compose down`` and travels with a
    backup to another machine.

    Resolved to an absolute path: ``DATA_DIR`` is relative (``./data``) by
    default, and a relative mount point never matches the absolute entries in
    ``/proc/mounts``, which made the reported mount source always empty.
    """
    return (Path(settings.DATA_DIR) / "rclone").resolve()


def _fmt_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


class RcloneMountPlugin(PluginBase):
    META = PluginMeta(
        slug="rclone_mount",
        name="Rclone 挂载",
        description=(
            "在容器内用 rclone 把 AList/WebDAV 远程挂载为本地目录，"
            "配置与缓存全部存放在数据卷，换机器重建容器即自动恢复。"
        ),
        version="2.0.0",
        author="NASPilot",
        icon="📁",
        category="storage",
        entrypoint="app.plugins.builtin.rclone_mount",
    )

    @property
    def default_config(self) -> dict[str, Any]:
        base = _config_dir()
        return {
            # ── remote definition (turned into rclone.conf) ──
            "alist_url": "",          # e.g. https://alist.example.com
            "username": "",
            "password": "",
            "remote_name": "alist",   # section name inside rclone.conf
            "remote_path": "/",       # subfolder of the remote to mount
            "manage_config": True,    # False = use an existing rclone.conf
            "config_file": str(base / "rclone.conf"),
            # ── mount behaviour ──
            "mount_point": str((Path(settings.DATA_DIR) / "alist").resolve()),
            "vfs_cache_mode": "full",
            "cache_size": "10G",
            "cache_age_m": 15,
            "dir_cache_time": "5m",
            "read_only": False,
            "allow_other": True,
            "uid": 0,
            "gid": 0,
            "umask": "002",
            "extra_args": "",
            "autostart": True,
        }

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "alist_url": {"type": "string", "title": "AList 地址（含协议，如 https://alist.example.com）"},
                "username": {"type": "string", "title": "用户名"},
                "password": {"type": "string", "title": "密码"},
                "remote_name": {"type": "string", "title": "Remote 名称"},
                "remote_path": {"type": "string", "title": "远程子路径"},
                "manage_config": {"type": "boolean", "title": "由插件生成 rclone.conf"},
                "config_file": {"type": "string", "title": "rclone.conf 路径"},
                "mount_point": {"type": "string", "title": "挂载点（容器内）"},
                "vfs_cache_mode": {"type": "string", "title": "VFS 缓存模式"},
                "cache_size": {"type": "string", "title": "缓存大小上限"},
                "cache_age_m": {"type": "integer", "title": "缓存保留（分钟）"},
                "dir_cache_time": {"type": "string", "title": "目录缓存时间"},
                "read_only": {"type": "boolean", "title": "只读挂载"},
                "allow_other": {"type": "boolean", "title": "允许其他用户访问"},
                "uid": {"type": "integer", "title": "文件属主 UID"},
                "gid": {"type": "integer", "title": "文件属主 GID"},
                "umask": {"type": "string", "title": "文件权限掩码"},
                "extra_args": {"type": "string", "title": "额外 rclone 参数（空格分隔）"},
                "autostart": {"type": "boolean", "title": "容器启动时自动挂载"},
            },
        }

    async def on_enable(self) -> None:
        logger.info("Rclone Mount plugin enabled")

    async def on_disable(self) -> None:
        logger.info("Rclone Mount plugin disabled")

    # ── lifecycle ─────────────────────────────────────────────────────

    async def on_startup(self) -> None:
        """Re-create the mount after a container restart.

        This is what makes the deployment portable: the DB row carries the
        config, ``<DATA_DIR>/rclone/rclone.conf`` carries the credentials, and
        this hook turns them back into a live mount without user action.
        """
        cfg = self._cfg()
        if not cfg["autostart"]:
            logger.info("Rclone 挂载：autostart 已关闭，跳过自动挂载")
            return

        if not self._remote_configured(cfg):
            logger.info("Rclone 挂载：尚未配置远程，跳过自动挂载")
            return

        # Don't fight a mount that already exists (e.g. plugin re-enabled).
        if await self._is_mounted(cfg["mount_point"]):
            logger.info("Rclone 挂载：%s 已在挂载状态", cfg["mount_point"])
            return

        result = await self._mount(cfg)
        if result.get("status") == "ok":
            logger.info("Rclone 挂载：容器启动自动挂载成功 → %s", cfg["mount_point"])
        else:
            logger.warning("Rclone 挂载：容器启动自动挂载失败 — %s", result.get("error"))

    # ── config helpers ────────────────────────────────────────────────

    def _cfg(self) -> dict[str, Any]:
        """Config with defaults applied.

        Plugins don't get defaults filled in by the registry, so merge here —
        otherwise a freshly created instance has no ``mount_point`` and the
        first "status" call reports a mount at the literal string ``None``.
        """
        merged = dict(self.default_config)
        merged.update({k: v for k, v in (self.config or {}).items() if v is not None})
        # Empty strings must not override a meaningful default.
        for key, value in self.default_config.items():
            if merged.get(key) in ("", None) and value not in ("", None):
                merged[key] = value
        return merged

    def _remote_configured(self, cfg: dict[str, Any]) -> bool:
        if not cfg["manage_config"]:
            return Path(cfg["config_file"]).is_file()
        return bool(str(cfg["alist_url"]).strip()) and bool(str(cfg["username"]).strip())

    @staticmethod
    def _dav_url(alist_url: str) -> str:
        """AList serves WebDAV under ``/dav``; accept a pasted URL with or without it."""
        url = alist_url.strip().rstrip("/")
        for suffix in ("/dav", "/webdav"):
            if url.endswith(suffix):
                return url
        return f"{url}/dav"

    async def _obscure(self, password: str) -> str:
        """Let rclone encrypt the password, reading it from stdin.

        Passing the plaintext as an argv element would expose it in ``ps``
        inside the container, so it goes through stdin instead.
        """
        proc = await asyncio.create_subprocess_exec(
            "rclone", "obscure", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(password.encode()), timeout=15
        )
        if proc.returncode != 0:
            raise RuntimeError(f"rclone obscure 失败: {stderr.decode()[:300]}")
        return stdout.decode().strip()

    async def _write_config(self, cfg: dict[str, Any]) -> Path:
        """(Re)generate rclone.conf from the plugin config."""
        path = Path(cfg["config_file"])
        path.parent.mkdir(parents=True, exist_ok=True)

        obscured = await self._obscure(str(cfg["password"]))
        section = (
            f"[{cfg['remote_name']}]\n"
            "type = webdav\n"
            f"url = {self._dav_url(str(cfg['alist_url']))}\n"
            "vendor = other\n"
            f"user = {cfg['username']}\n"
            f"pass = {obscured}\n"
        )
        # Plain config, no secrets beyond rclone's own obfuscation. Written
        # atomically so a crash can't leave a half file that rclone refuses.
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(section.encode("utf-8"))
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        logger.info("Rclone 配置已写入 %s (remote=%s)", path, cfg["remote_name"])
        return path

    def _remote_spec(self, cfg: dict[str, Any]) -> str:
        sub = str(cfg["remote_path"]).strip().strip("/")
        return f"{cfg['remote_name']}:/{sub}" if sub else f"{cfg['remote_name']}:"

    # ── probes ────────────────────────────────────────────────────────

    async def _is_mounted(self, mount_point: str) -> bool:
        """True when ``mount_point`` is a live mount, not just an empty dir."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "mountpoint", "-q", mount_point,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
            return proc.returncode == 0
        except (FileNotFoundError, asyncio.TimeoutError, ProcessLookupError):
            return False

    @staticmethod
    def _mount_source(mount_point: str) -> str:
        """Source field from /proc/mounts for the exact mount point.

        Compares resolved paths: a configured relative mount point otherwise
        never matches the absolute entry the kernel reports.
        """
        try:
            target = os.path.realpath(mount_point)
        except OSError:
            target = mount_point
        try:
            with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) >= 3 and os.path.realpath(parts[1]) == target:
                        return parts[0]
        except OSError:
            pass
        return ""

    def _preflight(self, cfg: dict[str, Any]) -> list[str]:
        """Environment problems that would make `rclone mount` fail."""
        problems: list[str] = []
        if not shutil.which("rclone"):
            problems.append("镜像内找不到 rclone 二进制（Dockerfile 未包含）")
        if not os.path.exists(FUSE_DEVICE):
            problems.append(
                f"{FUSE_DEVICE} 不存在 —— compose 需要 devices: - {FUSE_DEVICE}:/dev/fuse"
            )
        if not any(shutil.which(h) for h in UNMOUNT_HELPERS):
            problems.append("找不到 fusermount3/fusermount（镜像缺少 fuse3 包）")
        # A mount created outside DATA_DIR lives in the container's writable
        # layer: the host cannot see it and it disappears on recreate, which
        # defeats the whole point of mounting inside the container.
        if not self._under_data_dir(cfg["mount_point"]):
            problems.append(
                f"挂载点 {cfg['mount_point']} 不在 {settings.DATA_DIR} 之下 —— "
                "宿主机将看不到挂载内容，且容器重建后丢失"
            )
        if cfg["manage_config"] and not self._remote_configured(cfg):
            problems.append("AList 地址或用户名为空，无法自动生成 rclone.conf")
        if not cfg["manage_config"] and not Path(cfg["config_file"]).is_file():
            problems.append(f"rclone.conf 不存在: {cfg['config_file']}")
        return problems

    @staticmethod
    def _under_data_dir(path_text: str) -> bool:
        try:
            mount = Path(path_text).resolve()
            data = Path(settings.DATA_DIR).resolve()
            return mount == data or data in mount.parents
        except OSError:
            return False

    @staticmethod
    def _dir_writable(path: Path) -> tuple[bool, str]:
        try:
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=path):
                pass
            return True, ""
        except OSError as exc:
            return False, str(exc)

    # ── actions ───────────────────────────────────────────────────────

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        try:
            return await self._run_impl(**kwargs)
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI
            logger.exception("Rclone Mount run failed")
            return {
                "status": "error",
                "error": str(exc)[:500],
                "action": kwargs.get("action", "unknown"),
            }

    async def _run_impl(self, **kwargs: Any) -> dict[str, Any]:
        cfg = self._cfg()
        action = str(kwargs.get("action") or "ensure")
        if action not in ACTIONS:
            return {"status": "error", "error": f"未知动作: {action}", "action": action}

        logger.info(
            "Rclone 挂载：action=%s, remote=%s, mount=%s",
            action, self._remote_spec(cfg), cfg["mount_point"],
        )

        if action == "status":
            return await self._status(cfg)

        if action == "unmount":
            return await self._unmount(cfg)

        if action == "restart":
            await self._unmount(cfg)
            return await self._mount(cfg)

        # "mount" and "ensure" are the same call: _mount is idempotent and
        # returns already_mounted when a live mount exists.
        return await self._mount(cfg)

    async def _status(self, cfg: dict[str, Any]) -> dict[str, Any]:
        mount_point = cfg["mount_point"]
        mounted = await self._is_mounted(mount_point)
        source = self._mount_source(mount_point) if mounted else ""

        entry_count = 0
        cache_bytes = 0
        if mounted:
            try:
                entry_count = len(os.listdir(mount_point))
            except OSError:
                entry_count = -1
            cache_dir = _config_dir() / "cache" / "vfs"
            if cache_dir.is_dir():
                for root, _dirs, files in os.walk(cache_dir):
                    for name in files:
                        try:
                            cache_bytes += os.path.getsize(os.path.join(root, name))
                        except OSError:
                            pass

        problems = self._preflight(cfg)
        return {
            "status": "ok",
            "action": "status",
            "mounted": mounted,
            "mount_point": mount_point,
            "remote": self._remote_spec(cfg),
            "mount_source": source,
            "entry_count": entry_count,
            "cache_size": _fmt_bytes(cache_bytes),
            "config_file": cfg["config_file"],
            "config_exists": Path(cfg["config_file"]).is_file(),
            "rclone_version": await self._version(),
            "fuse_device": os.path.exists(FUSE_DEVICE),
            "problems": problems,
            "ready": not problems,
        }

    async def _version(self) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                "rclone", "version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            first = stdout.decode(errors="replace").splitlines()
            return first[0].strip() if first else ""
        except (FileNotFoundError, asyncio.TimeoutError, ProcessLookupError):
            return ""

    async def _mount(self, cfg: dict[str, Any]) -> dict[str, Any]:
        mount_point = cfg["mount_point"]

        problems = self._preflight(cfg)
        if problems:
            logger.error("Rclone 挂载：环境检查未通过 — %s", "; ".join(problems))
            return {
                "status": "error",
                "action": "mount",
                "error": "; ".join(problems),
                "problems": problems,
                "mount_point": mount_point,
            }

        if await self._is_mounted(mount_point):
            logger.info("Rclone 挂载：%s 已挂载，跳过", mount_point)
            return {
                "status": "ok",
                "action": "mount",
                "mounted": True,
                "already_mounted": True,
                "mount_point": mount_point,
                "remote": self._remote_spec(cfg),
            }

        ok, err = self._dir_writable(Path(mount_point))
        if not ok:
            return {
                "status": "error",
                "action": "mount",
                "error": f"挂载点不可写: {err}",
                "mount_point": mount_point,
            }

        if cfg["manage_config"]:
            await self._write_config(cfg)

        base = _config_dir()
        cache_dir = base / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        log_file = base / "rclone-mount.log"

        cmd = [
            "rclone", "mount", self._remote_spec(cfg), mount_point,
            "--config", str(cfg["config_file"]),
            "--cache-dir", str(cache_dir),
            "--log-file", str(log_file),
            "--log-level", "INFO",
            "--vfs-cache-mode", str(cfg["vfs_cache_mode"]),
            "--vfs-cache-max-size", str(cfg["cache_size"]),
            "--vfs-cache-max-age", f"{int(cfg['cache_age_m'])}m",
            "--dir-cache-time", str(cfg["dir_cache_time"]),
            "--buffer-size", "32M",
            "--uid", str(int(cfg["uid"])),
            "--gid", str(int(cfg["gid"])),
            "--umask", str(cfg["umask"]),
            "--allow-non-empty",
            "--daemon",
        ]
        if cfg["read_only"]:
            cmd.append("--read-only")
        if cfg["allow_other"]:
            cmd.append("--allow-other")
        for extra in str(cfg.get("extra_args") or "").split():
            cmd.append(extra)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        stderr_text = stderr.decode(errors="replace").strip()

        # `--daemon` detaches after the mount is in place, so a zero exit code
        # is the only reliable success signal — verify with mountpoint too.
        mounted = await self._is_mounted(mount_point)
        if proc.returncode == 0 and mounted:
            logger.info(
                "Rclone 挂载成功：%s → %s (cache=%s)",
                self._remote_spec(cfg), mount_point, cfg["cache_size"],
            )
            return {
                "status": "ok",
                "action": "mount",
                "mounted": True,
                "mount_point": mount_point,
                "remote": self._remote_spec(cfg),
                "cache_size": cfg["cache_size"],
                "host_path_hint": (
                    "宿主机如需访问，data 卷必须以 :rshared 挂载（默认 rprivate 不会传播）"
                ),
            }

        tail = self._tail_log(log_file)
        error = stderr_text or (tail or "rclone mount 未能在挂载点建立挂载")
        logger.error("Rclone 挂载失败: %s", error[:500])
        return {
            "status": "error",
            "action": "mount",
            "mounted": False,
            "mount_point": mount_point,
            "exit_code": proc.returncode,
            "error": error[:1000],
            "log_tail": tail[-1000:],
        }

    async def _unmount(self, cfg: dict[str, Any]) -> dict[str, Any]:
        mount_point = cfg["mount_point"]
        if not await self._is_mounted(mount_point):
            return {
                "status": "ok",
                "action": "unmount",
                "unmounted": True,
                "already_unmounted": True,
                "mount_point": mount_point,
            }

        helper = next((h for h in UNMOUNT_HELPERS if shutil.which(h)), None)
        if helper is None:
            return {
                "status": "error",
                "action": "unmount",
                "error": "找不到 fusermount3/fusermount，无法卸载",
                "mount_point": mount_point,
            }

        # -z: lazy unmount. A busy VFS mount otherwise fails with EBUSY, and
        # the user has no shell inside the container to go find the culprit.
        proc = await asyncio.create_subprocess_exec(
            helper, "-uz", mount_point,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        await asyncio.sleep(0.5)  # the kernel needs a moment to drop the mount
        unmounted = not await self._is_mounted(mount_point)

        if unmounted:
            logger.info("Rclone 已卸载: %s", mount_point)
        else:
            logger.warning(
                "Rclone 卸载失败: %s", stderr.decode(errors="replace").strip()[:300]
            )

        return {
            "status": "ok" if unmounted else "error",
            "action": "unmount",
            "unmounted": unmounted,
            "mount_point": mount_point,
            "stderr": stderr.decode(errors="replace").strip()[:500] if not unmounted else "",
        }

    @staticmethod
    def _tail_log(log_file: Path, lines: int = 40) -> str:
        try:
            if not log_file.is_file():
                return ""
            with open(log_file, "r", encoding="utf-8", errors="replace") as fh:
                return "".join(fh.readlines()[-lines:]).strip()
        except OSError:
            return ""
