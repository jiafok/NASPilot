"""In-memory metrics collector — polls /proc every 1s, keeps a ring buffer.

Provides time-series data for the real-time charts (CPU, memory, net, disk IO, partitions).
"""

import asyncio
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("naspilot.metrics")

# ── Ring buffer config ──
MAX_SAMPLES = 300  # 5 minutes at 1 sample/sec


@dataclass
class MetricSnapshot:
    ts: float  # unix timestamp
    cpu_percent: float
    mem_percent: float
    mem_used_mb: float
    net_recv_kbps: float
    net_sent_kbps: float
    disk_read_kbps: float
    disk_write_kbps: float
    partitions: list[dict[str, Any]] = field(default_factory=list)


# Global ring buffer
_buffer: deque[MetricSnapshot] = deque(maxlen=MAX_SAMPLES)
_collect_task: asyncio.Task[None] | None = None

# Previous values for rate calculation
_prev_net: dict[str, int] = {}
_prev_disk: dict[str, int] = {}
_prev_ts: float = 0.0


def _read_cpu() -> float:
    """Read CPU % from /proc/stat (non-blocking, no interval needed)."""
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        parts = [int(x) for x in line.split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        total = sum(parts)
        return max(0.0, 100.0 * (1 - idle / total)) if total else 0.0
    except Exception:
        return 0.0


# Per-call CPU delta tracking
_cpu_prev_idle = 0
_cpu_prev_total = 0


def _read_cpu_delta() -> float:
    """Read CPU % using delta between calls."""
    global _cpu_prev_idle, _cpu_prev_total
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        parts = [int(x) for x in line.split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        total = sum(parts)
        d_idle = idle - _cpu_prev_idle
        d_total = total - _cpu_prev_total
        _cpu_prev_idle = idle
        _cpu_prev_total = total
        if d_total > 0:
            return max(0.0, 100.0 * (1 - d_idle / d_total))
        return 0.0
    except Exception:
        return 0.0


def _read_mem() -> tuple[float, float]:
    """Read memory % and used GB."""
    try:
        with open("/proc/meminfo") as f:
            info = {}
            for line in f:
                key, *rest = line.split(":")
                val = rest[0].strip().split()[0] if rest else "0"
                info[key.strip()] = int(val)
        total = info.get("MemTotal", 1)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        used = total - avail
        pct = (used / total) * 100 if total else 0
        return pct, used / 1024  # MB
    except Exception:
        return 0.0, 0.0


def _read_net_kbps() -> tuple[float, float]:
    """Read network RX/TX rates in KB/s."""
    global _prev_net, _prev_ts
    try:
        ifaces = set()
        for p in os.listdir("/sys/class/net"):
            if p != "lo":
                ifaces.add(p)
        recv = 0
        sent = 0
        for iface in ifaces:
            try:
                with open(f"/sys/class/net/{iface}/statistics/rx_bytes") as f:
                    recv += int(f.read().strip())
                with open(f"/sys/class/net/{iface}/statistics/tx_bytes") as f:
                    sent += int(f.read().strip())
            except Exception:
                pass
        now = time.time()
        if _prev_net and _prev_ts > 0:
            dt = now - _prev_ts
            if dt > 0:
                rx_rate = (recv - _prev_net.get("rx", recv)) / 1024 / dt
                tx_rate = (sent - _prev_net.get("tx", sent)) / 1024 / dt
                _prev_net = {"rx": recv, "tx": sent}
                _prev_ts = now
                return max(0, rx_rate), max(0, tx_rate)
        _prev_net = {"rx": recv, "tx": sent}
        _prev_ts = now
        return 0.0, 0.0
    except Exception:
        return 0.0, 0.0


def _read_disk_io_kbps() -> tuple[float, float]:
    """Read disk read/write IO rates in KB/s."""
    global _prev_disk
    try:
        read_sectors = 0
        write_sectors = 0
        for entry in os.listdir("/sys/block"):
            stat_path = f"/sys/block/{entry}/stat"
            if not os.path.isfile(stat_path):
                continue
            with open(stat_path) as f:
                fields = f.read().split()
                if len(fields) >= 7:
                    read_sectors += int(fields[2])  # sectors read
                    write_sectors += int(fields[6])  # sectors written
        now = time.time()
        if _prev_disk and _prev_ts > 0:
            dt = now - _prev_ts
            if dt > 0:
                r_rate = (read_sectors - _prev_disk.get("r", read_sectors)) * 512 / 1024 / dt
                w_rate = (write_sectors - _prev_disk.get("w", write_sectors)) * 512 / 1024 / dt
                _prev_disk = {"r": read_sectors, "w": write_sectors}
                return max(0, r_rate), max(0, w_rate)
        _prev_disk = {"r": read_sectors, "w": write_sectors}
        return 0.0, 0.0
    except Exception:
        return 0.0, 0.0


def _read_partitions() -> list[dict[str, Any]]:
    """Read disk partitions via df command or /proc/mounts."""
    parts: list[dict[str, Any]] = []
    try:
        # Try df first (more human-readable)
        import subprocess
        result = subprocess.run(
            ["df", "-B1", "--output=source,target,size,used,avail,pcent"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().split("\n")[1:]:
            fields = line.split()
            if len(fields) < 6:
                continue
            src, mnt, size, used, avail, pct = fields
            # Filter: only real devices and interesting mounts
            if not src.startswith("/dev/"):
                continue
            try:
                size_b = int(size)
                used_b = int(used)
                avail_b = int(avail)
                pct_v = float(pct.replace("%", ""))
            except ValueError:
                continue
            parts.append({
                "device": src,
                "mount": mnt,
                "size_gb": round(size_b / 1024**3, 1),
                "used_gb": round(used_b / 1024**3, 1),
                "avail_gb": round(avail_b / 1024**3, 1),
                "percent": pct_v,
            })
    except Exception:
        pass
    return parts


async def _collect_loop() -> None:
    """Background task: sample every 1 second."""
    logger.info("Metrics collector started (interval=1s, buffer=%d)", MAX_SAMPLES)
    while True:
        try:
            cpu = _read_cpu_delta()
            mem_pct, mem_mb = _read_mem()
            net_rx, net_tx = _read_net_kbps()
            disk_r, disk_w = _read_disk_io_kbps()
            parts = _read_partitions()

            snap = MetricSnapshot(
                ts=time.time(),
                cpu_percent=round(cpu, 1),
                mem_percent=round(mem_pct, 1),
                mem_used_mb=round(mem_mb, 1),
                net_recv_kbps=round(net_rx, 1),
                net_sent_kbps=round(net_tx, 1),
                disk_read_kbps=round(disk_r, 1),
                disk_write_kbps=round(disk_w, 1),
                partitions=parts,
            )
            _buffer.append(snap)
        except Exception:
            logger.exception("Metrics collection error")
        await asyncio.sleep(1)


def start_collector() -> None:
    """Start the background metrics collector (called during lifespan startup)."""
    global _collect_task
    if _collect_task is not None and not _collect_task.done():
        return
    _collect_task = asyncio.ensure_future(_collect_loop())


def get_history(count: int = 300) -> list[dict[str, Any]]:
    """Return the last N samples as dicts for the API."""
    items = list(_buffer)[-count:]
    return [
        {
            "ts": s.ts,
            "cpu_percent": s.cpu_percent,
            "mem_percent": s.mem_percent,
            "mem_used_mb": s.mem_used_mb,
            "net_recv_kbps": s.net_recv_kbps,
            "net_sent_kbps": s.net_sent_kbps,
            "disk_read_kbps": s.disk_read_kbps,
            "disk_write_kbps": s.disk_write_kbps,
        }
        for s in items
    ]


def get_current() -> dict[str, Any]:
    """Return latest snapshot including partitions."""
    if not _buffer:
        return {
            "cpu_percent": 0, "mem_percent": 0, "mem_used_mb": 0,
            "net_recv_kbps": 0, "net_sent_kbps": 0,
            "disk_read_kbps": 0, "disk_write_kbps": 0,
            "partitions": [],
        }
    s = _buffer[-1]
    return {
        "ts": s.ts,
        "cpu_percent": s.cpu_percent,
        "mem_percent": s.mem_percent,
        "mem_used_mb": s.mem_used_mb,
        "net_recv_kbps": s.net_recv_kbps,
        "net_sent_kbps": s.net_sent_kbps,
        "disk_read_kbps": s.disk_read_kbps,
        "disk_write_kbps": s.disk_write_kbps,
        "partitions": s.partitions,
    }
