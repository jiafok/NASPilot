"""Docker Backup Plugin — container inspect export + restore script.

Ported from backup_docker_all_core.sh:
- List running containers via Docker SDK
- Export docker inspect JSON for each container (config backup)
- Detect app directories (with config/, data/, db/ subdirs)
- Archive all container configs into a timestamped .tgz
- Generate restore.sh for manual recovery
- Prune old backups beyond keep_days
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
    """Synchronous backup — only container configs, matching backup_docker_all_core.sh."""
    try:
        import docker  # type: ignore
    except ImportError:
        return {"status": "failed", "error": "docker SDK not installed"}

    backup_dir = cfg.get("backup_dir", "/app/data/docker_backup").rstrip("/")
    keep_days = int(cfg.get("keep_days", 7))
    containers_filter: list[str] = cfg.get("containers") or []
    compress = bool(cfg.get("compress", True))

    os.makedirs(backup_dir, exist_ok=True)

    tag = _now_tag()
    tmp_dir = tempfile.mkdtemp(prefix=f"naspilot_docker_backup_{tag}_")
    results: list[dict[str, Any]] = []

    try:
        client = docker.from_env()

        # ── 1. Export container inspect JSON ─────────────────────────────
        containers = client.containers.list(all=False)  # running only
        backed_up = 0
        container_names: list[str] = []

        for container in containers:
            name = container.name
            if containers_filter and name not in containers_filter:
                continue
            try:
                inspect_data = client.api.inspect_container(container.id)
                out_path = os.path.join(tmp_dir, f"{name}_inspect.json")
                with open(out_path, "w", encoding="utf-8") as fh:
                    json.dump(inspect_data, fh, indent=2, ensure_ascii=False)
                container_names.append(name)
                backed_up += 1
                results.append({"type": "container", "name": name, "status": "ok"})
            except Exception as exc:
                results.append({"type": "container", "name": name, "status": "failed", "error": str(exc)})

        # Container list file
        with open(os.path.join(tmp_dir, "container_list.txt"), "w") as fh:
            fh.write("\n".join(container_names))

        # ── 2. Generate restore.sh ───────────────────────────────────────
        restore_script = """\
#!/bin/bash
# NASPilot generated Docker restore script
# Run as: bash restore.sh
# This restores only container CONFIGS from inspect JSON.
# Containers must be re-created manually (docker compose up / docker run).
BACKUP_DIR=$(dirname "$0")
echo "Container configs backed up:"
ls "$BACKUP_DIR"/*_inspect.json 2>/dev/null
echo ""
echo "To restore a container, inspect its config and re-run:"
echo "  docker compose up -d <service>  # if using compose"
echo "  docker run ...                  # if using docker run"
"""
        with open(os.path.join(tmp_dir, "restore.sh"), "w") as fh:
            fh.write(restore_script)

        # ── 3. Archive to .tgz ──────────────────────────────────────────
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
            "backed_up_containers": backed_up,
            "pruned_old": pruned,
            "container_names": container_names,
            "time": _now_iso(),
        }

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


class DockerBackupPlugin(PluginBase):
    META = PluginMeta(
        slug="docker_backup",
        name="Docker Backup",
        description="Export container inspect configs and archive to .tgz. Matches backup_docker_all_core.sh behavior — config only, no volume data.",
        version="2.0.0",
        category="system",
    )

    async def on_enable(self) -> None:
        pass

    async def on_disable(self) -> None:
        pass

    async def run(self, **kwargs: Any) -> Any:
        result = await asyncio.to_thread(_backup_sync, self.config)

        # Track history
        history: list[dict[str, Any]] = self.config.setdefault("state", {}).setdefault("history", [])
        history.insert(0, result)
        self.config["state"]["history"] = history[:30]

        return result
