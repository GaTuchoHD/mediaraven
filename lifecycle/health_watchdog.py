import asyncio
import logging
import os
import psutil
import shutil
import time

import aiohttp
from curl_cffi import requests as cffi_requests

import state
from metrics import snapshot as metrics_snapshot

logger = logging.getLogger(__name__)


def _check_system_resources() -> dict:
    """Check disk and memory usage."""
    metrics = {}
    try:
        total, used, free = shutil.disk_usage("/")
        pct_used = (used / total) * 100
        metrics['disk_pct'] = round(pct_used, 1)
        metrics['disk_free_gb'] = round(free / (1024**3), 2)
    except Exception:
        pass

    try:
        with open('/sys/fs/cgroup/memory.current', 'r') as f:
            mem_used = int(f.read().strip())
        with open('/sys/fs/cgroup/memory.max', 'r') as f:
            raw_limit = f.read().strip()
        if raw_limit != 'max' and int(raw_limit) > 0:
            metrics['ram_pct'] = round(mem_used * 100 / int(raw_limit), 1)
        else:
            metrics['ram_pct'] = round(psutil.virtual_memory().percent, 1)
    except Exception:
        try:
            metrics['ram_pct'] = round(psutil.virtual_memory().percent, 1)
        except Exception:
            pass

    return metrics


async def _check_reddit_health() -> bool:
    """Verify old.reddit.com HTTP connectivity."""
    try:
        resp = await asyncio.to_thread(
            cffi_requests.get,
            "https://old.reddit.com/r/all/",
            cookies={"over18": "1"},
            headers={"User-Agent": "Mozilla/5.0"},
            impersonate="chrome124",
            timeout=8,
        )
        return resp.status_code == 200
    except Exception:
        return False


async def periodic_health_watchdog():
    """Background watchdog running every 2 hours to monitor system health."""
    while True:
        try:
            await asyncio.sleep(7200)  # Every 2 hours
            res = _check_system_resources()
            reddit_probe_ok = await _check_reddit_health()
            reddit_stats = metrics_snapshot().get("platforms", {}).get("reddit", {})
            last_reddit_success = reddit_stats.get("last_success_at", 0)
            recent_reddit_success = (
                last_reddit_success > 0
                and 0 <= time.time() - last_reddit_success <= 24 * 60 * 60
            )

            disk_str = f"Disco: {res.get('disk_pct', '?')}% usado ({res.get('disk_free_gb', '?')} GB livre)"
            ram_str = f"RAM: {res.get('ram_pct', '?')}% usada"
            if reddit_probe_ok:
                reddit_str = "Reddit: OK"
            elif recent_reddit_success:
                reddit_str = "Reddit: OK (download recente; probe old.reddit indisponível)"
            else:
                reddit_str = "Reddit: WARN (probe e downloads sem confirmação)"

            logger.info(f"🛡️ Watchdog de Saúde -> {disk_str} | {ram_str} | {reddit_str}")

            if res.get('disk_pct', 0) > 90:
                logger.warning(f"⚠️ ATENÇÃO: Espaço em disco baixo! ({res.get('disk_pct')}% ocupado)")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Erro no Watchdog de Saúde: {e}")
