"""Host resource snapshot for the Resource Dock."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

from infra.host_resource.nvml import NvmlQueryError, query_gpu_telemetry

_logger = logging.getLogger(__name__)
_VIRTUAL_FSTYPES = frozenset((
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs",
    "securityfs", "cgroup", "cgroup2", "pstore",
    "debugfs", "tracefs", "hugetlbfs", "mqueue",
    "fusectl", "configfs", "binfmt_misc", "autofs",
    "efivarfs", "bpf", "overlay", "squashfs",
    "nsfs", "fuse.lxcfs", "fuse.snapfuse",
    "nfs", "nfs4", "cifs", "smbfs", "fuse.sshfs",
    "fuse.rclone", "glusterfs", "lustre", "9p",
))


def _gpu_list() -> list[dict]:
    try:
        return [gpu._asdict() for gpu in query_gpu_telemetry(5.0)]
    except (NvmlQueryError, TimeoutError) as exc:
        _logger.warning("NVML Web telemetry failed: %s", exc)
        return []


def _disk_list() -> list[dict]:
    disks = []
    seen_devs: set[str] = set()
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                dev, mount, fstype = parts[0], parts[1], parts[2]
                if fstype in _VIRTUAL_FSTYPES:
                    continue
                if dev in seen_devs:
                    continue
                seen_devs.add(dev)
                try:
                    usage = shutil.disk_usage(mount)
                except OSError:
                    continue
                if usage.total < 2 * (1 << 30):
                    continue
                disks.append({
                    "mount": mount,
                    "total_gb": round(usage.total / (1 << 30)),
                    "used_gb": round(usage.used / (1 << 30)),
                    "free_gb": round(usage.free / (1 << 30)),
                })
    except OSError:
        pass
    return disks


def _mem_info() -> dict[str, int]:
    mem: dict[str, int] = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                key, val = line.split(":", 1)
                if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
                    mem[key] = int(val.strip().split()[0]) // 1024
    except OSError:
        pass
    return mem


def _load_avg() -> list[float] | None:
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            return [float(parts[0]), float(parts[1]), float(parts[2])]
    except OSError:
        return None


def _system_info() -> dict:
    mem = _mem_info()
    load = _load_avg()
    import socket
    return {
        "hostname": socket.gethostname(),
        "cpu_count": os.cpu_count() or 0,
        "load_avg": load,
        "memory_total_mb": mem.get("MemTotal", 0),
        "memory_available_mb": mem.get("MemAvailable", 0),
        "swap_total_mb": mem.get("SwapTotal", 0),
        "swap_free_mb": mem.get("SwapFree", 0),
        "disks": _disk_list(),
    }


def collect_resources() -> dict:
    return {
        "gpus": _gpu_list(),
        "processes": [],
        "system": _system_info(),
    }


_MAX_HISTORY_BYTES = 4 * 1024 * 1024
_TRIM_TARGET = 2 * 1024 * 1024


def collect_history_sample(now_relative: float) -> dict:
    mem = _mem_info()
    load = _load_avg()
    gpus = _gpu_list()
    return {
        "t": round(now_relative, 1),
        "cpu_load_1m": load[0] if load else None,
        "cpu_count": os.cpu_count() or 0,
        "memory_used_mb": mem.get("MemTotal", 0) - mem.get("MemAvailable", 0),
        "memory_total_mb": mem.get("MemTotal", 0),
        "gpus": [
            {"index": g["index"], "utilization_pct": g["utilization_pct"],
             "memory_bw_pct": g["memory_bw_pct"], "memory_used_mb": g["memory_used_mb"],
             "memory_total_mb": g["memory_total_mb"]}
            for g in gpus
        ],
    }


def append_history_sample(path: "str | os.PathLike[str]", sample: dict) -> None:
    import json
    p = Path(path) if not isinstance(path, Path) else path
    line = json.dumps(sample, separators=(",", ":")) + "\n"
    with open(p, "a") as f:
        f.write(line)
    try:
        size = p.stat().st_size
    except OSError:
        return
    if size > _MAX_HISTORY_BYTES:
        raw = p.read_bytes()
        tail = raw[-_TRIM_TARGET:]
        first_nl = tail.find(b"\n")
        if first_nl >= 0:
            tail = tail[first_nl + 1:]
        fd, tmp_path = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(tail)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, p)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def read_history_samples(path: "str | os.PathLike[str]") -> list[dict]:
    import json
    p = Path(path) if not isinstance(path, Path) else path
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
    return rows
