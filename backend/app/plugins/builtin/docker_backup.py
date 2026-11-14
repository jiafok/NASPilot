"""Docker App Backup Plugin — exact port of backup_docker_all_core.sh.

Scans /volume1/docker/ for app directories (those containing config/data/conf/db).
Excludes media/downloads/cache/logs/transcode/imagecache/trickplay/metadata.
Archives everything to a single .tgz.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tarfile
from datetime import datetime, timedelta, timezone
from typing import Any

from app.plugins.registry import PluginBase, PluginMeta

logger = logging.getLogger("naspilot.plugin.docker_backup")

LOCAL_TZ = timezone(timedelta(hours=8))

# Directories that mark an "app" in /volume1/docker/
DATA_DIR_NAMES = ("config", "data", "conf", "db", "appdata", "AppData")

# Directories excluded at top level (media/downloads etc.)
# NOTE: "data" is NOT here — it's a valid app data directory, only excluded
# as a top-level media category directory like movies/tv, not inside apps.
EXCLUDED_TOP_DIRS = {"media", "downloads", "download", "movies", "tv", "music", "Music", "Movies", "rclone-cache"}

# Directories excluded at any depth (cache/tmp/logs etc.)
EXCLUDED_SUBDIR_NAMES = {"cache", "tmp", "temp", "log", "logs", "transcode", "transcoding-temp", "imagecache", "trickplay", "metadata"}

# Compose-related files to always include
COMPOSE_FILES = {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", ".env"}


def _now_tag() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y%m%d_%H%M%S")


def _fmt_size(n: int) -> str:
    if n <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _is_excluded_dir(name: str) -> bool:
    return name.lower() in EXCLUDED_SUBDIR_NAMES


def _should_skip_root(name: str) -> bool:
    """Check if the root-level subdirectory should be excluded entirely."""
    return name.lower() in EXCLUDED_TOP_DIRS


def _normalize_list(value: Any) -> list[str]:
    """Normalize comma-separated string to list. Form saves type:'array' as string."""
    if isinstance(value, list):
        return [v.strip() for v in value if isinstance(v, str) and v.strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _norm_rel(path: str, root: str) -> str:
    """Normalise an exclusion entry to a `/`-separated path relative to root."""
    raw = str(path).strip().replace("\\", "/")
    if not raw:
        return ""
    root_norm = str(root).replace("\\", "/").rstrip("/")
    # Accept absolute host paths so the UI can show what users already know.
    if raw.startswith("/") and root_norm and raw.startswith(root_norm + "/"):
        raw = raw[len(root_norm) + 1:]
    return "/".join(p for p in raw.split("/") if p and p != ".")


def _is_explicitly_excluded(rel_path: str, excluded: list[str]) -> bool:
    """True when rel_path is, or sits under, an explicitly excluded path."""
    if not excluded or not rel_path:
        return False
    for item in excluded:
        if rel_path == item or rel_path.startswith(item + "/"):
            return True
    return False


def _is_mount_point(path: str) -> bool:
    """True when ``path`` is itself a mount (FUSE, NFS, SMB, ...).

    An rclone mount looks like an ordinary directory, so walking into it
    copies the whole remote — or blocks for hours while it streams. This is
    the only reliable check: the mount table knows, directory listings do not.
    """
    try:
        return os.path.ismount(path)
    except OSError:
        return False


def _is_pruned_dir(name: str, parent: str = "") -> bool:
    """Should this directory (and everything under it) be skipped entirely?

    Deliberately narrow: only regenerable caches and mount points. A directory
    merely *named* ``media``/``music``/``movies`` is NOT excluded — inside
    another container (AList, for instance) that is real data. NASPilot's own
    heavy paths are excluded by full relative path via ``exclude_paths``.
    """
    if _is_excluded_dir(name):
        return True
    return bool(parent) and _is_mount_point(parent)


def _dir_size(path: str, excluded: list[str] | None = None) -> int:
    """Total size of a directory tree, skipping pruned dirs, mounts and exclusions."""
    excluded = excluded or []
    total = 0
    for root, dirs, files in os.walk(path, topdown=True):
        pruned: list[str] = []
        for d in dirs:
            full = os.path.join(root, d)
            rel = os.path.relpath(full, path).replace("\\", "/")
            if _is_pruned_dir(d, full) or _is_explicitly_excluded(rel, excluded):
                continue
            pruned.append(d)
        dirs[:] = pruned
        for fname in files:
            rel_file = os.path.relpath(os.path.join(root, fname), path).replace("\\", "/")
            if _is_explicitly_excluded(rel_file, excluded):
                continue
            try:
                total += os.path.getsize(os.path.join(root, fname))
            except OSError:
                pass
    return total


def _copy_app(
    src: str, dst: str, app_name: str, excluded: list[str] | None = None
) -> dict[str, Any]:
    """Copy an app directory tree, pruning excluded paths. Mirrors rsync logic.

    Mount points are pruned: an rclone/network mount is a live view of data that
    lives elsewhere, so copying it either explodes the archive or streams the
    whole remote over the network.
    """
    excluded = excluded or []
    copied_files = 0
    skipped_mounts: list[str] = []
    skipped_excluded: list[str] = []
    os.makedirs(dst, exist_ok=True)

    for root, dirs, files in os.walk(src, topdown=True):
        # Prune excluded subdirectories and mount points in-place.
        pruned: list[str] = []
        for d in dirs:
            full = os.path.join(root, d)
            rel_dir = os.path.relpath(full, src).replace("\\", "/")
            if _is_mount_point(full):
                skipped_mounts.append(rel_dir)
                continue
            if _is_pruned_dir(d):
                continue
            if _is_explicitly_excluded(rel_dir, excluded):
                skipped_excluded.append(rel_dir)
                continue
            pruned.append(d)
        dirs[:] = pruned

        for fname in files:
            src_file = os.path.join(root, fname)
            # Check if this file's parent path contains an excluded component
            rel = os.path.relpath(src_file, src).replace("\\", "/")
            parts = rel.split("/")

            # Any-level cache/tmp/log exclusion. No name-based media check:
            # another container's `media` folder is its real data.
            if any(_is_excluded_dir(p) for p in parts):
                continue
            # User-configured exclusions, e.g. an rclone mount point
            if _is_explicitly_excluded(rel, excluded):
                continue

            target = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                shutil.copy2(src_file, target)
                copied_files += 1
            except OSError as e:
                logger.warning(f"Copy failed: {src_file} — {e}")

    return {
        "app": app_name,
        "files": copied_files,
        "skipped_mounts": skipped_mounts,
        "skipped_excluded": sorted(set(skipped_excluded)),
    }


def _backup_sync(cfg: dict[str, Any]) -> dict[str, Any]:
    """Synchronous backup — complete port of backup_docker_all_core.sh."""

    docker_root = cfg.get("docker_root", "/volume1/docker")
    backup_root = cfg.get("backup_dir", "/volumeUSB1/usbshare/docker_backup")
    keep_days = int(cfg.get("keep_days", 7))
    containers_filter: list[str] = _normalize_list(cfg.get("containers"))
    max_app_size_gb = float(cfg.get("max_app_size_gb", 0) or 0)
    # Explicit exclusions: rclone mounts, big media folders, etc.
    excluded_paths = [
        p for p in (_norm_rel(v, docker_root) for v in _normalize_list(cfg.get("exclude_paths")))
        if p
    ]
    if excluded_paths:
        logger.info("Excluding paths: %s", ", ".join(excluded_paths))

    if not os.path.isdir(docker_root):
        logger.warning("docker_root not found: %s", docker_root)
        return {"status": "failed", "error": f"docker_root not found: {docker_root}"}

    os.makedirs(backup_root, exist_ok=True)
    logger.info("Starting backup: docker=%s, dest=%s, keep=%d days", docker_root, backup_root, keep_days)

    tag = _now_tag()
    tmp_dir = os.path.join(backup_root, f"docker_all_core_{tag}")
    os.makedirs(tmp_dir, exist_ok=True)

    apps: list[dict[str, Any]] = []
    total_files = 0
    skipped_mounts: list[str] = []
    skipped_size: list[dict[str, Any]] = []

    try:
        for entry in sorted(os.listdir(docker_root)):
            app_path = os.path.join(docker_root, entry)
            if not os.path.isdir(app_path):
                logger.debug(f"Skipping non-directory: {entry}")
                continue

            app_name = entry
            if containers_filter and app_name not in containers_filter:
                continue

            # Skip top-level media/excluded dirs entirely
            if _should_skip_root(app_name):
                logger.info(f"Skipping (excluded): {app_name}")
                continue

            # Skip the directory itself when it is a mount: backing it up would
            # copy whatever is mounted over it (rclone remote, NFS share, ...).
            if _is_mount_point(app_path):
                logger.info(f"Skipping (mount point): {app_name}")
                skipped_mounts.append(app_name)
                continue

            # Exclusion entries are declared relative to docker_root; strip the
            # app prefix so they match inside this app's own tree.
            if app_name in excluded_paths:
                logger.info(f"Skipping (excluded path): {app_name}")
                continue
            relative_exclusions = [
                p[len(app_name) + 1:]
                for p in excluded_paths
                if p.startswith(app_name + "/")
            ]

            # Must have at least one config/data/conf/db subdirectory OR
            # contain files directly (for simple backup scenarios)
            has_data = any(os.path.isdir(os.path.join(app_path, d)) for d in DATA_DIR_NAMES)
            if not has_data:
                # Check if directory has any files at all
                has_files = any(os.path.isfile(os.path.join(app_path, f))
                                for f in os.listdir(app_path))
                if not has_files:
                    logger.info(f"Skipping (empty): {app_name}")
                    continue
                logger.info(f"Backing up (flat files): {app_name}")

            else:
                logger.info(f"Backing up: {app_name}")

            # Enforce the size cap that the config schema has always advertised.
            # Measured after pruning, so media/cache/mounts do not inflate it.
            if max_app_size_gb > 0:
                size_bytes = _dir_size(app_path, relative_exclusions)
                size_gb = size_bytes / (1024 ** 3)
                if size_gb > max_app_size_gb:
                    logger.info(
                        "Skipping (size %.1f GB > %.1f GB limit): %s",
                        size_gb, max_app_size_gb, app_name,
                    )
                    skipped_size.append(
                        {"app": app_name, "size_gb": round(size_gb, 1)}
                    )
                    continue

            app_dest = os.path.join(tmp_dir, app_name)
            logger.info(f"Collecting: {app_name}")

            result = _copy_app(app_path, app_dest, app_name, relative_exclusions)

            # Copy compose / .env files
            for fname in COMPOSE_FILES:
                src_file = os.path.join(app_path, fname)
                if os.path.isfile(src_file):
                    shutil.copy2(src_file, os.path.join(app_dest, fname))

            apps.append(result)
            total_files += result["files"]

        # Archive to .tgz
        archive_path = os.path.join(backup_root, f"docker_all_core_{tag}.tgz")
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(tmp_dir, arcname=f"docker_all_core_{tag}")
        archive_size = os.path.getsize(archive_path)

        # Prune old backups
        pruned = 0
        if keep_days > 0:
            cutoff = datetime.now().timestamp() - keep_days * 86400
            for fname in os.listdir(backup_root):
                fpath = os.path.join(backup_root, fname)
                if not fname.startswith("docker_all_core_"):
                    continue
                try:
                    if os.path.getmtime(fpath) < cutoff:
                        if os.path.isdir(fpath):
                            shutil.rmtree(fpath)
                        else:
                            os.remove(fpath)
                        pruned += 1
                except OSError:
                    pass

        return {
            "status": "ok",
            "archive": archive_path,
            "archive_size": _fmt_size(archive_size),
            "apps": [a["app"] for a in apps],
            "apps_count": len(apps),
            "total_files": total_files,
            "pruned_old": pruned,
            "skipped_mounts": skipped_mounts,
            "skipped_size": skipped_size,
            "tag": tag,
        }

    finally:
        if os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


class DockerBackupPlugin(PluginBase):
    META = PluginMeta(
        slug="docker_backup",
        name="Docker App Backup",
        description="Backup /volume1/docker/ app configs (no media/cache/logs). Exact port of backup_docker_all_core.sh.",
        version="3.0.0",
        category="system",
    )

    @property
    def default_config(self) -> dict[str, Any]:
        return {
            "docker_root": "/volume1/docker",
            "backup_dir": "/volumeUSB1/usbshare/docker_backup",
            "keep_days": 7,
            "containers": [],
            "max_app_size_gb": 0,
            # Paths (relative to docker_root, or absolute) never backed up.
            # The rclone mount lives inside the naspilot data dir, so its
            # contents are a remote copy — archiving it is pure waste.
            # Full paths relative to docker_root. Both are NASPilot-owned and
            # huge: `alist` is the rclone remote view, `media` is the
            # re-downloadable library. Other containers are untouched — a
            # folder named `media` inside them is their own data.
            "exclude_paths": ["naspilot/data/alist", "naspilot/data/media"],
        }

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "docker_root": {"type": "string", "title": "Docker 应用根目录"},
                "backup_dir": {"type": "string", "title": "备份输出目录"},
                "keep_days": {"type": "integer", "title": "保留天数"},
                "containers": {"type": "array", "items": {"type": "string"}, "title": "只备份指定容器（空=全部）"},
                "max_app_size_gb": {"type": "number", "title": "单个 App 数据上限(GB)，超限跳过，0=不限"},
                "exclude_paths": {"type": "array", "items": {"type": "string"}, "title": "排除路径（相对 docker 根目录或绝对路径）；默认仅排除 naspilot 自己的 alist 挂载与媒体库"},
            },
        }

    async def on_enable(self) -> None:
        pass

    async def on_disable(self) -> None:
        pass

    async def run(self, **kwargs: Any) -> Any:
        import traceback
        try:
            result = await asyncio.to_thread(_backup_sync, self.config)
            return result
        except Exception as exc:
            logger.exception("Docker Backup run failed")
            return {"status": "error", "error": str(exc)[:500], "apps": [], "archive": None}
