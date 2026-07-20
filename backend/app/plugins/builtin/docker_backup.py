"""Docker Backup Plugin — container config + volume backup.

Ported from backup_docker_all_core.sh logic:
- List running/all containers via Docker SDK
- Export container inspect JSON (config backup)
- Backup volumes using a temporary alpine container (tar)
- Compress entire backup to .tgz
- Prune old backups beyond keep_days
- Restore metadata stored in config["state"]["history"]
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

from app.plugins.registry import PluginBase, PluginMeta

logger = logging.getLogger("naspilot.plugin.docker_backup")

LOCAL_TZ = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(LOCAL_TZ).isoformat()


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


def _backup_sync(cfg: dict[str, Any]) -> dict[str, Any]:
    """Synchronous backup logic — called via asyncio.to_thread."""
    try:
        import docker  # type: ignore
    except ImportError:
        return {"status": "failed", "error": "docker SDK not installed (pip install docker)"}

    backup_dir = cfg.get("backup_dir", "/tmp/docker_backup").rstrip("/")
    keep_days = int(cfg.get("keep_days", 7))
    containers_filter: list[str] = cfg.get("containers") or []
    volumes_filter: list[str] = cfg.get("volumes") or []
    compress = bool(cfg.get("compress", True))

    os.makedirs(backup_dir, exist_ok=True)

    tag = _now_tag()
    tmp_dir = tempfile.mkdtemp(prefix=f"naspilot_docker_backup_{tag}_")
    results: list[dict[str, Any]] = []

    try:
        client = docker.from_env()

        # ── 1. Container configs ────────────────────────────────────────
        containers = client.containers.list(all=True)
        backed_up_containers = 0
        for container in containers:
            name = container.name
            if containers_filter and name not in containers_filter:
                continue
            try:
                inspect_data = client.api.inspect_container(container.id)
                out_path = os.path.join(tmp_dir, f"{name}_inspect.json")
                with open(out_path, "w", encoding="utf-8") as fh:
                    json.dump(inspect_data, fh, indent=2, ensure_ascii=False)
                backed_up_containers += 1
                results.append({"type": "container", "name": name, "status": "ok"})
            except Exception as exc:
                results.append({"type": "container", "name": name, "status": "failed", "error": str(exc)})

        # Container list file
        with open(os.path.join(tmp_dir, "container_list.txt"), "w") as fh:
            fh.write("\n".join(c.name for c in containers))

        # ── 2. Volume data ──────────────────────────────────────────────
        backed_up_volumes = 0
        all_volumes = client.volumes.list()
        for vol in all_volumes:
            vol_name = vol.name
            if volumes_filter and vol_name not in volumes_filter:
                continue
            out_tar = os.path.join(tmp_dir, f"{vol_name}_backup.tar.gz")
            try:
                result = client.containers.run(
                    "alpine",
                    command=f"tar czf /backup/{vol_name}_backup.tar.gz -C /volume .",
                    volumes={
                        vol_name: {"bind": "/volume", "mode": "ro"},
                        tmp_dir: {"bind": "/backup", "mode": "rw"},
                    },
                    remove=True,
                    stdout=True,
                    stderr=True,
                )
                if os.path.exists(out_tar):
                    backed_up_volumes += 1
                    size = os.path.getsize(out_tar)
                    results.append({"type": "volume", "name": vol_name, "status": "ok", "size": _fmt_size(size)})
                else:
                    results.append({"type": "volume", "name": vol_name, "status": "failed", "error": "tar not created"})
            except Exception as exc:
                results.append({"type": "volume", "name": vol_name, "status": "failed", "error": str(exc)})

        # Network config
        networks = client.api.networks()
        with open(os.path.join(tmp_dir, "network_config.json"), "w", encoding="utf-8") as fh:
            json.dump(networks, fh, indent=2, ensure_ascii=False)

        # Generate restore script
        restore_script = """\
#!/bin/bash
# NASPilot generated Docker restore script
# Run as: bash restore.sh
BACKUP_DIR=$(dirname "$0")
cd "$BACKUP_DIR" || exit 1

# Restore volumes
for vol_file in *_backup.tar.gz; do
  vol_name=${vol_file%_backup.tar.gz}
  echo "Restoring volume: $vol_name"
  docker volume create "$vol_name"
  docker run --rm -v "$vol_name:/volume" -v "$(pwd):/backup" \\
    alpine tar xzf "/backup/$vol_file" -C /volume
done

# Containers must be re-created manually from *_inspect.json
echo "Done. Re-create containers from *_inspect.json as needed."
"""
        with open(os.path.join(tmp_dir, "restore.sh"), "w") as fh:
            fh.write(restore_script)

        # ── 3. Archive ──────────────────────────────────────────────────
        archive_name = f"docker_backup_{tag}.tgz"
        archive_path = os.path.join(backup_dir, archive_name)

        if compress:
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(tmp_dir, arcname=f"docker_backup_{tag}")
            archive_size = os.path.getsize(archive_path)
        else:
            dest = os.path.join(backup_dir, f"docker_backup_{tag}")
            shutil.copytree(tmp_dir, dest)
            archive_path = dest
            archive_size = sum(
                os.path.getsize(os.path.join(r, f))
                for r, _, files in os.walk(dest)
                for f in files
            )

        # ── 4. Prune old backups ─────────────────────────────────────────
        pruned = 0
        if keep_days > 0:
            cutoff = datetime.now().timestamp() - keep_days * 86400
            for fname in os.listdir(backup_dir):
                fpath = os.path.join(backup_dir, fname)
                if fname.startswith("docker_backup_") and os.path.getmtime(fpath) < cutoff:
                    try:
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
            "backed_up_containers": backed_up_containers,
            "backed_up_volumes": backed_up_volumes,
            "pruned_old": pruned,
            "results": results,
            "time": _now_iso(),
        }

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


class DockerBackupPlugin(PluginBase):
    META = PluginMeta(
        slug="docker_backup",
        name="Docker 备份",
        description="容器配置备份、数据卷备份、自动恢复",
        version="1.0.0",
        author="NASPilot",
        icon="🐳",
        category="system",
        entrypoint="app.plugins.builtin.docker_backup",
    )

    @property
    def default_config(self) -> dict[str, Any]:
        return {
            "backup_dir": "/app/data/docker_backup",
            "containers": [],    # empty = all containers
            "volumes": [],       # empty = all volumes
            "compress": True,
            "keep_days": 7,
        }

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "backup_dir": {"type": "string", "title": "备份目录"},
                "containers": {"type": "array", "items": {"type": "string"}, "title": "备份容器（空=全部）"},
                "volumes": {"type": "array", "items": {"type": "string"}, "title": "备份卷（空=全部）"},
                "compress": {"type": "boolean", "title": "压缩为 .tgz"},
                "keep_days": {"type": "integer", "title": "保留天数"},
            },
        }

    async def on_enable(self) -> None:
        logger.info("Docker Backup plugin enabled")

    async def on_disable(self) -> None:
        logger.info("Docker Backup plugin disabled")

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        result = await asyncio.to_thread(_backup_sync, self.config)
        # Persist history
        state = self.config.setdefault("state", {})
        history: list[dict[str, Any]] = state.setdefault("history", [])
        history.append({
            "time": _now_iso(),
            "status": result.get("status"),
            "archive": result.get("archive", ""),
            "archive_size": result.get("archive_size", ""),
            "containers": result.get("backed_up_containers", 0),
            "volumes": result.get("backed_up_volumes", 0),
        })
        if len(history) > 30:
            state["history"] = history[-30:]
        return result
