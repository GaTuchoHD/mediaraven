import asyncio
from collections import deque
import json
import logging
import os
import shutil
import sqlite3
import time
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
import yt_dlp

import state
from config import (
    BASE_DOWNLOAD_DIR,
    LOCAL_API_HOST,
    LOCAL_API_URL,
    MEDIA_CACHE_ENABLED,
    TOKEN,
)
from cookies import get_firefox_cookie_status, get_reddit_cookie_status
from media_cache import DB_PATH
from metrics import snapshot as download_metrics_snapshot
from version import __build__, __version__
from system_resources import get_disk_stats, get_memory_stats, get_process_tree_rss_mb

logger = logging.getLogger(__name__)

BOT_START_TIME = time.time()
_TELEGRAM_AUTH_SUCCESS_TTL_SECONDS = 300.0
_TELEGRAM_AUTH_FAILURE_TTL_SECONDS = 30.0
_telegram_auth_cache = {"checked_at": 0.0, "ok": False}


def _get_system_stats() -> dict:
    stats = {
        "uptime_seconds": int(time.time() - BOT_START_TIME),
        "ytdlp_version": getattr(yt_dlp.version, '__version__', 'N/A'),
        "ffmpeg": bool(state.FFMPEG_PATH or shutil.which("ffmpeg")),
        "deno": bool(state.DENO_PATH or shutil.which("node")),
        "playwright": bool(state.PW_BROWSER),
        "name": "MediaRaven Bot",
        "id": "mediaraven-host-33",
    }

    try:
        stats["disk"] = get_disk_stats(BASE_DOWNLOAD_DIR or "/")
    except Exception:
        stats["disk"] = {}

    try:
        stats["ram"] = get_memory_stats()
    except Exception:
        stats["ram"] = {}

    try:
        stats["process_ram_mb"] = get_process_tree_rss_mb()
    except (OSError, RuntimeError):
        stats["process_ram_mb"] = 0

    try:
        if MEDIA_CACHE_ENABLED and os.path.exists(DB_PATH):
            conn = sqlite3.connect(DB_PATH, timeout=5)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM media_cache")
            stats["cache_entries"] = cur.fetchone()[0]
            conn.close()
        else:
            stats["cache_entries"] = 0
    except Exception:
        stats["cache_entries"] = 0

    request_stats = state.request_stats()
    stats["requests"] = {
        "active": request_stats["active"],
        "pending": request_stats["pending"],
        "last_request_age_seconds": (
            max(0, int(time.time() - request_stats["last_request_at"]))
            if request_stats["last_request_at"]
            else None
        ),
    }

    download_metrics = download_metrics_snapshot()
    platform_stats = download_metrics.get("platforms", {}).values()
    last_success_at = float(download_metrics.get("last_success_at") or 0)
    last_failure_at = float(download_metrics.get("last_failure_at") or 0)
    stats["downloads"] = {
        "success": sum(item.get("success", 0) for item in platform_stats),
        "failure": sum(item.get("failure", 0) for item in platform_stats),
        "last_success_at": last_success_at or None,
        "last_failure_at": last_failure_at or None,
        "last_success_age_seconds": (
            max(0, int(time.time() - last_success_at)) if last_success_at else None
        ),
        "last_failure_age_seconds": (
            max(0, int(time.time() - last_failure_at)) if last_failure_at else None
        ),
        "last_success_platform": download_metrics.get("last_success_platform") or None,
        "last_failure_platform": download_metrics.get("last_failure_platform") or None,
        "persistent": bool(download_metrics.get("persistent")),
    }

    try:
        firefox = get_firefox_cookie_status()
        reddit = get_reddit_cookie_status()
        stats["cookies"] = {
            "firefox_active": firefox["active"] if firefox["readable"] else 0,
            "firefox_expired": firefox["expired"] if firefox["readable"] else 0,
            "reddit_active": reddit["active"] if reddit["readable"] else 0,
            "reddit_expired": reddit["expired"] if reddit["readable"] else 0,
            "expiring_soon": firefox.get("expiring_soon", 0),
            "readiness": firefox.get("readiness", {}),
        }
    except Exception:
        stats["cookies"] = {}

    return stats


async def _telegram_api_online() -> bool:
    parsed = urlsplit(f"//{LOCAL_API_HOST}")
    host = parsed.hostname
    port = parsed.port or 80
    if not host:
        return False
    writer = None
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=2)
        return True
    except (OSError, asyncio.TimeoutError):
        return False
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def _telegram_api_authenticated(force: bool = False) -> bool:
    """Valida o token com getMe, com cache para não pressionar a API local."""
    now = time.monotonic()
    cache_ttl = (
        _TELEGRAM_AUTH_SUCCESS_TTL_SECONDS
        if _telegram_auth_cache["ok"]
        else _TELEGRAM_AUTH_FAILURE_TTL_SECONDS
    )
    if (
        not force
        and _telegram_auth_cache["checked_at"]
        and now - _telegram_auth_cache["checked_at"] < cache_ttl
    ):
        return bool(_telegram_auth_cache["ok"])

    session = state.AIOHTTP_SESSION
    if not TOKEN or not LOCAL_API_HOST or not session or session.closed:
        ok = False
    else:
        try:
            async with session.get(
                f"{LOCAL_API_URL}{TOKEN}/getMe",
                timeout=5,
            ) as response:
                payload = await response.json(content_type=None)
                ok = response.status == 200 and payload.get("ok") is True
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError) as exc:
            logger.warning(
                "Falha na autenticação do healthcheck do Telegram (%s)",
                type(exc).__name__,
            )
            ok = False

    _telegram_auth_cache["checked_at"] = now
    _telegram_auth_cache["ok"] = ok
    return ok


def _core_services_online() -> bool:
    return bool(
        state.AIOHTTP_SESSION
        and not state.AIOHTTP_SESSION.closed
        and state.PW_BROWSER
    )


async def handle_live(request: web.Request) -> web.Response:
    """Liveness interna usada pelo Docker; não depende de serviços externos."""
    live = _core_services_online()
    return web.json_response(
        {
            "status": "ok" if live else "degraded",
            "uptime_seconds": int(time.time() - BOT_START_TIME),
            "aiohttp": bool(state.AIOHTTP_SESSION and not state.AIOHTTP_SESSION.closed),
            "playwright": bool(state.PW_BROWSER),
        },
        status=200 if live else 503,
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def handle_health(request: web.Request) -> web.Response:
    """Readiness completa (/health e /ready), incluindo Telegram."""
    stats = _get_system_stats()
    telegram_api = await _telegram_api_online()
    telegram_auth = await _telegram_api_authenticated()
    stats["telegram_api"] = telegram_api
    stats["telegram_auth"] = telegram_auth
    healthy = bool(
        _core_services_online()
        and telegram_api
        and telegram_auth
    )
    stats["status"] = "ok" if healthy else "degraded"
    return web.json_response(
        stats,
        status=200 if healthy else 503,
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def handle_version(request: web.Request) -> web.Response:
    """Dozzle compatibility version endpoint (/version or /api/version)."""
    return web.json_response(
        {"version": __version__, "build": __build__, "agent": "mediaraven-logs"},
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def handle_prometheus_metrics(request: web.Request) -> web.Response:
    """Prometheus / Grafana metrics endpoint (/metrics)."""
    stats = _get_system_stats()
    disk_pct = stats.get("disk", {}).get("pct_used", 0)
    ram_pct = stats.get("ram", {}).get("pct_used", 0)
    cache = stats.get("cache_entries", 0)
    uptime = stats.get("uptime_seconds", 0)
    process_ram = stats.get("process_ram_mb", 0)
    requests = stats.get("requests", {})
    cookies = stats.get("cookies", {})
    telegram_api = int(await _telegram_api_online())
    telegram_auth = int(await _telegram_api_authenticated())
    downloads = stats.get("downloads", {})
    download_success = downloads.get("success", 0)
    download_failure = downloads.get("failure", 0)
    last_success_at = downloads.get("last_success_at") or 0
    last_failure_at = downloads.get("last_failure_at") or 0
    metrics_persistent = int(bool(downloads.get("persistent")))
    readiness = cookies.get("readiness", {})

    lines = [
        "# HELP mediaraven_uptime_seconds Bot uptime in seconds",
        "# TYPE mediaraven_uptime_seconds counter",
        f"mediaraven_uptime_seconds {uptime}",
        "# HELP mediaraven_disk_used_percent Percentage of disk used",
        "# TYPE mediaraven_disk_used_percent gauge",
        f"mediaraven_disk_used_percent {disk_pct}",
        "# HELP mediaraven_ram_used_percent Percentage of RAM memory used",
        "# TYPE mediaraven_ram_used_percent gauge",
        f"mediaraven_ram_used_percent {ram_pct}",
        "# HELP mediaraven_cache_entries Total active entries in SQLite media cache",
        "# TYPE mediaraven_cache_entries gauge",
        f"mediaraven_cache_entries {cache}",
        "# HELP mediaraven_process_ram_megabytes RAM used by MediaRaven and child processes",
        "# TYPE mediaraven_process_ram_megabytes gauge",
        f"mediaraven_process_ram_megabytes {process_ram}",
        "# HELP mediaraven_requests_active Requests currently being processed",
        "# TYPE mediaraven_requests_active gauge",
        f"mediaraven_requests_active {requests.get('active', 0)}",
        "# HELP mediaraven_requests_pending Requests waiting for the global semaphore",
        "# TYPE mediaraven_requests_pending gauge",
        f"mediaraven_requests_pending {requests.get('pending', 0)}",
        "# HELP mediaraven_telegram_api_up Local Telegram Bot API TCP health",
        "# TYPE mediaraven_telegram_api_up gauge",
        f"mediaraven_telegram_api_up {telegram_api}",
        "# HELP mediaraven_telegram_auth_ok Telegram bot token authenticated by getMe",
        "# TYPE mediaraven_telegram_auth_ok gauge",
        f"mediaraven_telegram_auth_ok {telegram_auth}",
        "# HELP mediaraven_cookie_active Active cookies by source",
        "# TYPE mediaraven_cookie_active gauge",
        f"mediaraven_cookie_active{{source=\"firefox\"}} {cookies.get('firefox_active', 0)}",
        f"mediaraven_cookie_active{{source=\"reddit\"}} {cookies.get('reddit_active', 0)}",
        "# HELP mediaraven_cookie_expiring_soon Active Firefox cookies expiring in seven days",
        "# TYPE mediaraven_cookie_expiring_soon gauge",
        f"mediaraven_cookie_expiring_soon {cookies.get('expiring_soon', 0)}",
        "# HELP mediaraven_cookie_platform_ready Required authentication cookies present",
        "# TYPE mediaraven_cookie_platform_ready gauge",
        f"mediaraven_cookie_platform_ready{{platform=\"youtube\"}} {int(readiness.get('YouTube/Google', False))}",
        f"mediaraven_cookie_platform_ready{{platform=\"instagram\"}} {int(readiness.get('Instagram', False))}",
        f"mediaraven_cookie_platform_ready{{platform=\"x\"}} {int(readiness.get('X/Twitter', False))}",
        "# HELP mediaraven_download_success_total Persisted successful downloads",
        "# TYPE mediaraven_download_success_total counter",
        f"mediaraven_download_success_total {download_success}",
        "# HELP mediaraven_download_failure_total Persisted failed downloads",
        "# TYPE mediaraven_download_failure_total counter",
        f"mediaraven_download_failure_total {download_failure}",
        "# HELP mediaraven_last_download_success_timestamp_seconds Unix timestamp of last successful download",
        "# TYPE mediaraven_last_download_success_timestamp_seconds gauge",
        f"mediaraven_last_download_success_timestamp_seconds {last_success_at}",
        "# HELP mediaraven_last_download_failure_timestamp_seconds Unix timestamp of last failed download",
        "# TYPE mediaraven_last_download_failure_timestamp_seconds gauge",
        f"mediaraven_last_download_failure_timestamp_seconds {last_failure_at}",
        "# HELP mediaraven_metrics_persistence_ok Download metrics SQLite persistence health",
        "# TYPE mediaraven_metrics_persistence_ok gauge",
        f"mediaraven_metrics_persistence_ok {metrics_persistent}",
    ]
    return web.Response(text="\n".join(lines) + "\n", content_type="text/plain", headers={"Access-Control-Allow-Origin": "*"})


async def handle_logs(request: web.Request) -> web.StreamResponse:
    """Real-time log streaming endpoint for Dozzle & Web Log Viewers (/logs & /api/logs/stream)."""
    log_path = os.path.join(os.path.dirname(__file__), "bot.log")

    # Check if client requests SSE (Server-Sent Events) or raw text
    accept = request.headers.get("Accept", "")
    is_sse = "text/event-stream" in accept or "stream" in request.path

    if is_sse:
        response = web.StreamResponse(
            status=200,
            reason='OK',
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'Access-Control-Allow-Origin': '*',
            }
        )
        await response.prepare(request)

        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    # Send last 50 lines initial
                    lines = f.readlines()[-50:]
                    for line in lines:
                        await response.write(f"data: {line.strip()}\n\n".encode('utf-8'))

                    # Stream new lines live
                    while True:
                        line = f.readline()
                        if line:
                            await response.write(f"data: {line.strip()}\n\n".encode('utf-8'))
                        else:
                            await asyncio.sleep(1)
            except Exception:
                pass
        return response
    else:
        try:
            lines_count = int(request.query.get("tail", "100"))
        except ValueError:
            return web.Response(text="tail deve ser inteiro", status=400)
        lines_count = max(1, min(lines_count, 2000))
        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    tail_lines = deque(f, maxlen=lines_count)
                    return web.Response(text="".join(tail_lines), content_type="text/plain", headers={"Access-Control-Allow-Origin": "*"})
            except Exception as e:
                return web.Response(text=f"Erro ao ler bot.log: {e}", status=500, headers={"Access-Control-Allow-Origin": "*"})
        return web.Response(text="bot.log nao encontrado", status=404, headers={"Access-Control-Allow-Origin": "*"})


async def start_metrics_server(port: int = 8282):
    """Start background HTTP metrics/logs server on specified port."""
    app = web.Application()

    # Route aliases for Dozzle & standard monitoring tools
    app.router.add_get('/live', handle_live)

    for path in ('/health', '/ready', '/status', '/api/status', '/api/v1/status'):
        app.router.add_get(path, handle_health)

    for path in ('/version', '/api/version', '/api/v1/version'):
        app.router.add_get(path, handle_version)

    for path in ('/logs', '/api/logs/stream', '/api/v1/logs/stream'):
        app.router.add_get(path, handle_logs)

    app.router.add_get('/metrics', handle_prometheus_metrics)

    # Health probes run every 30 seconds; access_log=None keeps them out of
    # bot.log while application errors continue through the normal logger.
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', port)
    try:
        await site.start()
        state.METRICS_RUNNER = runner
        state.METRICS_SITE = site
        logger.info(f"📊 Servidor interno de Métricas/Logs ativo na porta {port}")
    except Exception as e:
        await runner.cleanup()
        logger.warning(f"Não foi possível iniciar o Servidor de Métricas na porta {port}: {e}")
