"""Container-aware resource metrics used by health endpoints."""
from __future__ import annotations

import os
import shutil

import psutil

CGROUP_MEMORY_CURRENT = "/sys/fs/cgroup/memory.current"
CGROUP_MEMORY_MAX = "/sys/fs/cgroup/memory.max"


def _read_int(path: str) -> int | None:
    try:
        value = open(path, "r", encoding="ascii").read().strip()
        if value == "max":
            return None
        return int(value)
    except (OSError, ValueError):
        return None


def get_memory_stats() -> dict[str, float]:
    used = _read_int(CGROUP_MEMORY_CURRENT)
    total = _read_int(CGROUP_MEMORY_MAX)
    if used is None or total is None or total <= 0:
        memory = psutil.virtual_memory()
        used, total = int(memory.used), int(memory.total)
    return {
        "total_mb": round(total / 1024**2, 2),
        "used_mb": round(used / 1024**2, 2),
        "pct_used": round(used * 100 / total, 1),
    }


def get_disk_stats(path: str = "/") -> dict[str, float]:
    total, used, free = shutil.disk_usage(path)
    return {
        "total_gb": round(total / 1024**3, 2),
        "used_gb": round(used / 1024**3, 2),
        "free_gb": round(free / 1024**3, 2),
        "pct_used": round(used * 100 / total, 1) if total else 0.0,
    }


def get_process_tree_rss_mb(pid: int | None = None) -> float:
    try:
        process = psutil.Process(pid or os.getpid())
        rss = process.memory_info().rss
        for child in process.children(recursive=True):
            try:
                rss += child.memory_info().rss
            except (psutil.Error, OSError):
                continue
        return round(rss / 1024**2, 2)
    except (psutil.Error, OSError):
        return 0.0
