"""Docker service helpers for container management UI."""

from __future__ import annotations

from datetime import datetime, timezone
import socket
from typing import Any

import docker
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


def _calc_blkio(stats: dict[str, Any]) -> tuple[int, int]:
    read_b = 0
    write_b = 0
    for item in ((stats.get("blkio_stats") or {}).get("io_service_bytes_recursive") or []):
        op = str(item.get("op") or "").lower()
        value = int(item.get("value") or 0)
        if op == "read":
            read_b += value
        elif op == "write":
            write_b += value
    return read_b, write_b


def _calc_net(stats: dict[str, Any]) -> tuple[int, int]:
    rx = 0
    tx = 0
    for value in ((stats.get("networks") or {}).values()):
        if not isinstance(value, dict):
            continue
        rx += int(value.get("rx_bytes") or 0)
        tx += int(value.get("tx_bytes") or 0)
    return rx, tx


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
            mem = stats.get("memory_stats") or {}
            mem_usage = int(mem.get("usage") or 0)
            mem_limit = int(mem.get("limit") or 0)
            mem_percent = round((mem_usage / mem_limit) * 100.0, 2) if mem_limit > 0 else 0.0
            net_rx, net_tx = _calc_net(stats)
            blk_read, blk_write = _calc_blkio(stats)
            result.append(
                {
                    "id": c.id,
                    "short_id": c.short_id,
                    "name": c.name,
                    "cpu_percent": _calc_cpu_percent(stats),
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
        try:
            self._raw_sock.settimeout(0.03)
        except Exception:
            pass

    def read(self, size: int = 4096) -> bytes:
        try:
            return self._raw_sock.recv(size)
        except socket.timeout:
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
