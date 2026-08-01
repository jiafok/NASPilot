"""Docker service helpers for container management UI."""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from typing import Any

import docker
import psutil
from docker.errors import DockerException, NotFound

from app.core.config import settings


def _client() -> docker.DockerClient:
    return docker.DockerClient(base_url=f"unix://{settings.DOCKER_SOCK}")


def _fmt_ports(port_map: dict[str, list[dict[str, str]] | None] | None) -> list[str]:
    if not port_map:
        return []
    out: list[str] = []
    for internal, bindings in port_map.items():
        if not bindings:
            continue
        for b in bindings:
            host_ip = b.get("HostIp") or "0.0.0.0"
            host_port = b.get("HostPort") or ""
            if host_port:
                out.append(f"{host_ip}:{host_port}->{internal}")
    return out


def _created_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        # Docker uses RFC3339 style timestamps, usually ending with "Z"
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return value


def list_containers(include_all: bool = True) -> list[dict[str, Any]]:
    """Return normalized container list for frontend table."""
    client = _client()
    try:
        items: list[dict[str, Any]] = []
        for c in client.containers.list(all=include_all):
            attrs = c.attrs or {}
            state = attrs.get("State") or {}
            labels = (attrs.get("Config") or {}).get("Labels") or {}
            networks = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
            ip_addrs = [
                net.get("IPAddress")
                for net in networks.values()
                if isinstance(net, dict) and net.get("IPAddress")
            ]
            # Host-networked containers share the host's network stack
            # and don't have their own IP via Docker SDK
            network_mode = (attrs.get("HostConfig") or {}).get("NetworkMode", "")
            if not ip_addrs and network_mode == "host":
                ip_addrs = ["共享宿主机网络"]
            items.append(
                {
                    "id": c.id,
                    "short_id": c.short_id,
                    "name": c.name,
                    "image": (attrs.get("Config") or {}).get("Image") or "",
                    "status": c.status,
                    "state": state.get("Status") or c.status,
                    "running": bool(state.get("Running", False)),
                    "created_at": _created_to_iso(attrs.get("Created")),
                    "stack": labels.get("com.docker.compose.project") or labels.get("stack") or "",
                    "ownership": labels.get("io.portainer.owner") or labels.get("owner") or "",
                    "ip_addresses": ip_addrs,
                    "ports": _fmt_ports((attrs.get("NetworkSettings") or {}).get("Ports")),
                }
            )
        items.sort(key=lambda x: (0 if x.get("running") else 1, x.get("name", "")))
        return items
    finally:
        client.close()


def _calc_cpu_percent(stats: dict[str, Any]) -> float:
    cpu_stats = stats.get("cpu_stats") or {}
    precpu = stats.get("precpu_stats") or {}
    cpu_total = (cpu_stats.get("cpu_usage") or {}).get("total_usage", 0)
    pre_total = (precpu.get("cpu_usage") or {}).get("total_usage", 0)
    sys_total = cpu_stats.get("system_cpu_usage", 0)
    pre_sys_total = precpu.get("system_cpu_usage", 0)
    cpu_delta = cpu_total - pre_total
    sys_delta = sys_total - pre_sys_total
    cpu_count = cpu_stats.get("online_cpus") or len((cpu_stats.get("cpu_usage") or {}).get("percpu_usage") or [1])
    if cpu_delta <= 0 or sys_delta <= 0 or cpu_count <= 0:
        return 0.0
    return round((cpu_delta / sys_delta) * cpu_count * 100.0, 2)


def _calc_memory_percent(mem_usage: int, mem_limit: int) -> float:
    if mem_usage <= 0:
        return 0.0
    if mem_limit > 0:
        return round((mem_usage / mem_limit) * 100.0, 2)
    host_total = int(psutil.virtual_memory().total or 0)
    if host_total > 0:
        return round((mem_usage / host_total) * 100.0, 2)
    return 0.0


def _sample_container_stats(container: Any, delay: float = 1.5) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Capture two successive stats snapshots for environments where a single snapshot is zeroed.

    Synology Docker daemon may not update counters fast enough at 0.35s;
    using 1.5s to give it time to populate precpu_stats."""
    try:
        first = container.stats(stream=False)
    except Exception:
        return None, None
    time.sleep(delay)
    try:
        second = container.stats(stream=False)
    except Exception:
        return first, None
    return first, second


def _fallback_stats_via_cli(container_name: str) -> dict[str, Any] | None:
    """Last-resort fallback: use `docker stats` CLI when SDK stats are all zero.

    Some Synology Docker engines do not update cpu_stats.system_cpu_usage
    between consecutive SDK calls, leaving us with cpu_delta == 0.
    The CLI parses cgroup files directly and is more reliable."""
    import json as _json

    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", container_name],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return None
        data = _json.loads(result.stdout.strip())
        cp = float(str(data.get("CPUPerc", "0")).rstrip("%"))
        mp = float(str(data.get("MemPerc", "0")).rstrip("%"))
        mu_raw = str(data.get("MemUsage", "0 / 0")).split("/")[0].strip()
        # Convert "12.5MiB" to bytes
        mem_usage = _parse_mem(mu_raw)
        return {
            "cpu_percent": round(cp, 2),
            "memory_percent": round(mp, 2),
            "memory_usage": mem_usage,
        }
    except Exception:
        return None


def _parse_mem(raw: str) -> int:
    """Parse docker stats memory like '12.5MiB' or '1.2GiB' to bytes."""
    import re

    raw = raw.strip().upper()
    m = re.match(r"([\d.]+)\s*(\w+)", raw)
    if not m:
        return 0
    val = float(m.group(1))
    unit = m.group(2)
    if unit in ("KIB", "KB"):
        return int(val * 1024)
    if unit in ("MIB", "MB"):
        return int(val * 1024 * 1024)
    if unit in ("GIB", "GB"):
        return int(val * 1024 * 1024 * 1024)
    if unit == "B":
        return int(val)
    return 0


def get_containers_stats(container_ids: list[str] | None = None, running_only: bool = True) -> list[dict[str, Any]]:
    client = _client()
    try:
        result: list[dict[str, Any]] = []
        containers = client.containers.list(all=not running_only)
        wanted = set(container_ids or [])
        for c in containers:
            if wanted and c.id not in wanted and c.short_id not in wanted and c.name not in wanted:
                continue
            if running_only and c.status != "running":
                continue
            stats = c.stats(stream=False)
            cpu_percent = _calc_cpu_percent(stats)
            mem = stats.get("memory_stats") or {}
            mem_usage = int(mem.get("usage") or 0)
            mem_limit = int(mem.get("limit") or 0)
            mem_percent = _calc_memory_percent(mem_usage, mem_limit)

            # Synology and some cgroup setups can return a zeroed first snapshot.
            # When both CPU and memory are 0 for a running container, take a second sample and retry.
            if cpu_percent <= 0.0 or (mem_percent <= 0.0 and (mem_usage > 0 or mem_limit == 0)):
                first_stats, second_stats = _sample_container_stats(c)
                if first_stats and second_stats:
                    cpu_percent = _calc_cpu_percent({
                        "cpu_stats": second_stats.get("cpu_stats") or {},
                        "precpu_stats": first_stats.get("cpu_stats") or first_stats.get("precpu_stats") or {},
                    })
                    mem = second_stats.get("memory_stats") or mem
                    mem_usage = int(mem.get("usage") or mem_usage or 0)
                    mem_limit = int(mem.get("limit") or mem_limit or 0)
                    mem_percent = _calc_memory_percent(mem_usage, mem_limit)

                # Synology fallback: SDK still returned 0s → try docker stats CLI
                if cpu_percent <= 0.0 and mem_percent <= 0.0:
                    cli = _fallback_stats_via_cli(c.name)
                    if cli:
                        cpu_percent = cli["cpu_percent"]
                        mem_percent = cli["memory_percent"]
                        mem_usage = cli["memory_usage"]

            net_rx, net_tx = _calc_net(stats)
            blk_read, blk_write = _calc_blkio(stats)
            result.append(
                {
                    "id": c.id,
                    "short_id": c.short_id,
                    "name": c.name,
                    "cpu_percent": cpu_percent,
                    "memory_usage": mem_usage,
                    "memory_limit": mem_limit,
                    "memory_percent": mem_percent,
                    "net_rx": net_rx,
                    "net_tx": net_tx,
                    "blk_read": blk_read,
                    "blk_write": blk_write,
                    "pids": int((stats.get("pids_stats") or {}).get("current") or 0),
                }
            )
        return result
    finally:
        client.close()


def get_container_logs(container_id: str, tail: int = 500, since: int | None = None) -> str:
    client = _client()
    try:
        c = client.containers.get(container_id)
        data = c.logs(stdout=True, stderr=True, tail=tail, since=since, timestamps=True)
        if isinstance(data, (bytes, bytearray)):
            return data.decode("utf-8", errors="replace")
        return str(data)
    finally:
        client.close()


def exec_in_container(
    container_id: str,
    command: str,
    user: str | None = None,
    workdir: str | None = None,
) -> dict[str, Any]:
    """Execute a shell command inside a container and return output + exit code."""
    client = _client()
    try:
        c = client.containers.get(container_id)
        exec_id = client.api.exec_create(
            container=c.id,
            cmd=["/bin/sh", "-lc", command],
            stdout=True,
            stderr=True,
            stdin=False,
            tty=False,
            user=user or "",
            workdir=workdir or "",
        ).get("Id")
        if not exec_id:
            raise RuntimeError("Failed to create exec session")
        output = client.api.exec_start(exec_id, tty=False)
        inspect = client.api.exec_inspect(exec_id)
        text = output.decode("utf-8", errors="replace") if isinstance(output, (bytes, bytearray)) else str(output)
        return {
            "exit_code": inspect.get("ExitCode"),
            "running": inspect.get("Running", False),
            "output": text,
        }
    finally:
        client.close()


def apply_container_action(container_id: str, action: str) -> dict[str, Any]:
    client = _client()
    try:
        c = client.containers.get(container_id)
        if action == "start":
            c.start()
        elif action == "stop":
            c.stop(timeout=10)
        elif action == "restart":
            c.restart(timeout=10)
        elif action == "pause":
            c.pause()
        elif action == "unpause":
            c.unpause()
        elif action == "kill":
            c.kill()
        elif action == "remove":
            c.remove(force=True)
        else:
            raise ValueError(f"Unsupported action: {action}")
        return {"ok": True, "action": action}
    finally:
        client.close()


def bulk_container_action(container_ids: list[str], action: str) -> dict[str, Any]:
    if not container_ids:
        return {"ok": True, "action": action, "success": [], "failed": []}
    success: list[str] = []
    failed: list[dict[str, str]] = []
    for cid in container_ids:
        try:
            apply_container_action(cid, action)
            success.append(cid)
        except Exception as exc:
            failed.append({"id": cid, "error": str(exc)})
    return {"ok": len(failed) == 0, "action": action, "success": success, "failed": failed}


class DockerExecSession:
    """Interactive docker exec session backed by a raw socket."""

    def __init__(
        self,
        container_id: str,
        user: str | None = None,
        workdir: str | None = None,
        shell: str = "/bin/sh",
    ) -> None:
        self.api = docker.APIClient(base_url=f"unix://{settings.DOCKER_SOCK}")
        info = self.api.exec_create(
            container=container_id,
            cmd=[shell],
            stdin=True,
            stdout=True,
            stderr=True,
            tty=True,
            user=user or "",
            workdir=workdir or "",
        )
        self.exec_id = info.get("Id")
        if not self.exec_id:
            raise RuntimeError("Failed to create interactive exec session")
        self._sock_wrapper = self.api.exec_start(self.exec_id, tty=True, socket=True)
        self._raw_sock = getattr(self._sock_wrapper, "_sock", self._sock_wrapper)
        # Keep socket blocking; websocket layer reads it in a worker thread
        # for lower latency and less CPU overhead than timeout polling.

    def read(self, size: int = 4096) -> bytes:
        try:
            return self._raw_sock.recv(size)
        except OSError:
            return b""

    def write(self, data: str) -> None:
        self._raw_sock.send(data.encode("utf-8", errors="replace"))

    def inspect(self) -> dict[str, Any]:
        return self.api.exec_inspect(self.exec_id)

    def close(self) -> None:
        try:
            self._raw_sock.close()
        except Exception:
            pass
        try:
            self._sock_wrapper.close()
        except Exception:
            pass
        try:
            self.api.close()
        except Exception:
            pass


__all__ = [
    "DockerExecSession",
    "DockerException",
    "NotFound",
    "apply_container_action",
    "bulk_container_action",
    "exec_in_container",
    "get_containers_stats",
    "get_container_logs",
    "list_containers",
]
