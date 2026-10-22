import asyncio
from dataclasses import dataclass, field
import logging
import os
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)


@dataclass
class _PlatformStats:
    success: int = 0
    failure: int = 0
    multilang: int = 0
    total_duration_s: float = 0.0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0

    @property
    def total(self) -> int:
        return self.success + self.failure + self.multilang

    @property
    def avg_duration_s(self) -> float:
        return (self.total_duration_s / (self.success + self.failure)) if (self.success + self.failure) else 0.0


@dataclass
class _Metrics:
    started_at: float = field(default_factory=time.monotonic)
    platforms: dict[str, _PlatformStats] = field(default_factory=dict)
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    last_success_platform: str = ""
    last_failure_platform: str = ""


_lock = threading.Lock()
_metrics = _Metrics()
_METRICS_DB_PATH = os.getenv(
    "METRICS_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "metrics.sqlite3"),
)
_persistence_ready = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS download_metrics (
    platform TEXT PRIMARY KEY,
    success INTEGER NOT NULL DEFAULT 0,
    failure INTEGER NOT NULL DEFAULT 0,
    multilang INTEGER NOT NULL DEFAULT 0,
    total_duration_s REAL NOT NULL DEFAULT 0,
    last_success_at REAL NOT NULL DEFAULT 0,
    last_failure_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
)
"""


def _restrict_db_permissions() -> None:
    try:
        os.chmod(_METRICS_DB_PATH, 0o600)
    except OSError:
        pass


def _load_persisted() -> None:
    if not _persistence_ready:
        return
    try:
        with sqlite3.connect(_METRICS_DB_PATH, timeout=5) as conn:
            rows = conn.execute(
                "SELECT platform, success, failure, multilang, total_duration_s, "
                "last_success_at, last_failure_at FROM download_metrics"
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("Falha ao carregar métricas persistidas (%s)", type(exc).__name__)
        return

    for row in rows:
        platform, success, failure, multilang, duration, last_ok, last_fail = row
        stats = _PlatformStats(
            success=int(success),
            failure=int(failure),
            multilang=int(multilang),
            total_duration_s=float(duration),
            last_success_at=float(last_ok),
            last_failure_at=float(last_fail),
        )
        _metrics.platforms[str(platform)] = stats
        if stats.last_success_at > _metrics.last_success_at:
            _metrics.last_success_at = stats.last_success_at
            _metrics.last_success_platform = str(platform)
        if stats.last_failure_at > _metrics.last_failure_at:
            _metrics.last_failure_at = stats.last_failure_at
            _metrics.last_failure_platform = str(platform)


def _initialize_persistence() -> None:
    global _persistence_ready
    try:
        parent = os.path.dirname(_METRICS_DB_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with sqlite3.connect(_METRICS_DB_PATH, timeout=5) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_SCHEMA)
        _restrict_db_permissions()
        _persistence_ready = True
        _load_persisted()
    except (OSError, sqlite3.Error) as exc:
        _persistence_ready = False
        logger.warning("Métricas persistentes indisponíveis (%s)", type(exc).__name__)


def _persist_platform(platform: str, stats: _PlatformStats) -> None:
    if not _persistence_ready:
        return
    try:
        with sqlite3.connect(_METRICS_DB_PATH, timeout=5) as conn:
            conn.execute(
                """
                INSERT INTO download_metrics (
                    platform, success, failure, multilang, total_duration_s,
                    last_success_at, last_failure_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform) DO UPDATE SET
                    success=excluded.success,
                    failure=excluded.failure,
                    multilang=excluded.multilang,
                    total_duration_s=excluded.total_duration_s,
                    last_success_at=excluded.last_success_at,
                    last_failure_at=excluded.last_failure_at,
                    updated_at=excluded.updated_at
                """,
                (
                    platform,
                    stats.success,
                    stats.failure,
                    stats.multilang,
                    stats.total_duration_s,
                    stats.last_success_at,
                    stats.last_failure_at,
                    max(stats.last_success_at, stats.last_failure_at) or time.time(),
                ),
            )
        _restrict_db_permissions()
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Falha ao persistir métricas (%s)", type(exc).__name__)


def _schedule_persist(platform: str, stats: _PlatformStats) -> None:
    """Fire-and-forget SQLite write on a daemon thread in async loop, or inline when sync."""
    s = _PlatformStats(
        success=stats.success,
        failure=stats.failure,
        multilang=stats.multilang,
        total_duration_s=stats.total_duration_s,
        last_success_at=stats.last_success_at,
        last_failure_at=stats.last_failure_at,
    )
    try:
        asyncio.get_running_loop()
        t = threading.Thread(target=_persist_platform, args=(platform, s), daemon=True)
        t.start()
    except RuntimeError:
        _persist_platform(platform, s)


def _bucket(platform: str) -> _PlatformStats:
    stats = _metrics.platforms.get(platform)
    if stats is None:
        stats = _PlatformStats()
        _metrics.platforms[platform] = stats
    return stats


def record_success(platform: str, duration_s: float) -> None:
    with _lock:
        stats = _bucket(platform)
        stats.success += 1
        stats.total_duration_s += duration_s
        _metrics.last_success_at = time.time()
        _metrics.last_success_platform = platform
        stats.last_success_at = _metrics.last_success_at
        _schedule_persist(platform, stats)


def record_failure(platform: str, duration_s: float) -> None:
    with _lock:
        stats = _bucket(platform)
        stats.failure += 1
        stats.total_duration_s += duration_s
        _metrics.last_failure_at = time.time()
        _metrics.last_failure_platform = platform
        stats.last_failure_at = _metrics.last_failure_at
        _schedule_persist(platform, stats)


def record_multilang(platform: str) -> None:
    with _lock:
        stats = _bucket(platform)
        stats.multilang += 1
        _schedule_persist(platform, stats)


def snapshot() -> dict:
    with _lock:
        return {
            'uptime_s': time.monotonic() - _metrics.started_at,
            'last_success_at': _metrics.last_success_at,
            'last_failure_at': _metrics.last_failure_at,
            'last_success_platform': _metrics.last_success_platform,
            'last_failure_platform': _metrics.last_failure_platform,
            'persistent': _persistence_ready,
            'platforms': {
                name: {
                    'success': s.success,
                    'failure': s.failure,
                    'multilang': s.multilang,
                    'total': s.total,
                    'avg_duration_s': round(s.avg_duration_s, 2),
                    'last_success_at': s.last_success_at,
                    'last_failure_at': s.last_failure_at,
                }
                for name, s in _metrics.platforms.items()
            },
        }


def format_summary() -> str:
    snap = snapshot()
    uptime_h = snap['uptime_s'] / 3600
    if not snap['platforms']:
        return f"📊 Métricas: sem downloads nas últimas {uptime_h:.1f}h."
    parts = [f"📊 Métricas persistidas (processo ativo {uptime_h:.1f}h):"]
    for name, s in sorted(snap['platforms'].items()):
        parts.append(
            f"  {name}: ok={s['success']} fail={s['failure']} ml={s['multilang']} "
            f"total={s['total']} avg={s['avg_duration_s']}s"
        )
    return "\n".join(parts)


_initialize_persistence()
