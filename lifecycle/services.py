from metrics_server import start_metrics_server
from media_cache import init_cache_db
from curl_cffi import requests as cffi_requests
import asyncio
import logging

import aiohttp
from cachetools import TTLCache
from playwright.async_api import async_playwright

import state
from config import (
    AIOHTTP_CONN_LIMIT,
    AIOHTTP_CONNECT_TIMEOUT,
    AIOHTTP_READ_TIMEOUT,
    AIOHTTP_TOTAL_TIMEOUT,
    PLAYWRIGHT_UA,
    PW_VIEWPORT_HEIGHT,
    PW_VIEWPORT_WIDTH,
    SHUTDOWN_TASKS_TIMEOUT,
    TTL_FUTURES_SECONDS,
    TTL_RETRIES_SECONDS,
)
from messages import lmsg
from cookies import extract_firefox_cookies

from .instagram_login import init_instagrapi_async
from .metrics_log import periodic_metrics_log
from .periodic_cleanup import periodic_temp_cleanup
from .health_watchdog import periodic_health_watchdog
from .playwright_refresh import periodic_playwright_refresh
from .startup import startup_cleanup_async

logger = logging.getLogger(__name__)


def _start_service_task(coro, name: str) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    state.service_tasks.add(task)
    task.add_done_callback(state.service_tasks.discard)
    return task


async def init_globals(app) -> None:
    await startup_cleanup_async()
    await asyncio.to_thread(init_cache_db)

    try:
        connector = aiohttp.TCPConnector(limit=AIOHTTP_CONN_LIMIT)
        default_timeout = aiohttp.ClientTimeout(
            total=AIOHTTP_TOTAL_TIMEOUT,
            connect=AIOHTTP_CONNECT_TIMEOUT,
            sock_read=AIOHTTP_READ_TIMEOUT,
        )
        state.CFFI_SESSION = cffi_requests.Session(impersonate='chrome124')
        state.AIOHTTP_SESSION = aiohttp.ClientSession(
            connector=connector, timeout=default_timeout,
        )
        logger.info(lmsg("services.sess_o_aiohttp_global", AIOHTTP_TOTAL_TIMEOUT=AIOHTTP_TOTAL_TIMEOUT))

        logger.info(lmsg("services.iniciando_playwright_navegador"))
        state.PW_MANAGER = await async_playwright().start()
        state.PW_BROWSER = await state.PW_MANAGER.chromium.launch(headless=True)
        state.PW_CONTEXT = await state.PW_BROWSER.new_context(
            user_agent=PLAYWRIGHT_UA,
            viewport={'width': PW_VIEWPORT_WIDTH, 'height': PW_VIEWPORT_HEIGHT},
        )

        state.FIREFOX_COOKIES_CACHE = extract_firefox_cookies()
        if state.FIREFOX_COOKIES_CACHE:
            try:
                await state.PW_CONTEXT.add_cookies(state.FIREFOX_COOKIES_CACHE)
                logger.info(lmsg("services.x_cookies_do", arg0=len(state.FIREFOX_COOKIES_CACHE)))
            except Exception as e:
                logger.warning(lmsg("services.erro_ao_injetar", e=e))
    except BaseException:
        await _close_runtime_resources()
        raise

    logger.info(lmsg("services.contexto_playwright_pronto"))

    app.bot_data['lang_requests'] = TTLCache(maxsize=1000, ttl=TTL_RETRIES_SECONDS)
    app.bot_data['retries'] = TTLCache(maxsize=1000, ttl=TTL_RETRIES_SECONDS)
    app.bot_data['dl_futures'] = TTLCache(maxsize=1000, ttl=TTL_FUTURES_SECONDS)
    app.bot_data['lang_futures'] = TTLCache(maxsize=1000, ttl=TTL_FUTURES_SECONDS)
    app.bot_data['caption_futures'] = TTLCache(maxsize=1000, ttl=TTL_FUTURES_SECONDS)
    app.bot_data['screenshot_futures'] = TTLCache(maxsize=1000, ttl=TTL_FUTURES_SECONDS)
    logger.info(lmsg("services.todos_os_servi_os"))

    # Background tasks
    _start_service_task(periodic_playwright_refresh(), "playwright-refresh")
    _start_service_task(init_instagrapi_async(), "instagram-init")
    _start_service_task(periodic_metrics_log(), "metrics-log")
    _start_service_task(periodic_temp_cleanup(), "temp-cleanup")
    _start_service_task(periodic_health_watchdog(), "health-watchdog")
    _start_service_task(start_metrics_server(8282), "metrics-server")
    logger.info("🛡️ Tarefas de limpeza periódica e Watchdog de Saúde ativadas.")


async def _close_runtime_resources() -> None:
    if state.METRICS_RUNNER:
        await state.METRICS_RUNNER.cleanup()
        state.METRICS_RUNNER = None
        state.METRICS_SITE = None
    if state.CFFI_SESSION:
        state.CFFI_SESSION.close()
        state.CFFI_SESSION = None
    if state.AIOHTTP_SESSION:
        await state.AIOHTTP_SESSION.close()
        state.AIOHTTP_SESSION = None
    if state.PW_CONTEXT:
        await state.PW_CONTEXT.close()
        state.PW_CONTEXT = None
    if state.PW_BROWSER:
        await state.PW_BROWSER.close()
        state.PW_BROWSER = None
    if state.PW_MANAGER:
        await state.PW_MANAGER.stop()
        state.PW_MANAGER = None


async def stop_globals(app) -> None:
    logger.info(lmsg("services.desligando_servi_os_globais"))

    pending = [t for t in state.background_tasks if not t.done()]
    if pending:
        logger.info(lmsg("services.aguardando_x_download", arg0=len(pending), SHUTDOWN_TASKS_TIMEOUT=SHUTDOWN_TASKS_TIMEOUT))
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=SHUTDOWN_TASKS_TIMEOUT,
            )
            logger.info(lmsg("services.downloads_pendentes_finalizados"))
        except asyncio.TimeoutError:
            still_pending = [t for t in pending if not t.done()]
            logger.warning(lmsg("services.x_task_s", arg0=len(still_pending)))
            for t in still_pending:
                t.cancel()
            await asyncio.gather(*still_pending, return_exceptions=True)

    services = [task for task in state.service_tasks if not task.done()]
    for task in services:
        task.cancel()
    if services:
        await asyncio.gather(*services, return_exceptions=True)
    state.service_tasks.clear()

    await _close_runtime_resources()

    for pool_name in ('YTDLP_POOL', 'IG_POOL', 'IO_POOL'):
        pool = getattr(state, pool_name, None)
        if pool is not None:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except Exception as e:
                logger.debug(lmsg("services.falha_ao_desligar", pool_name=pool_name, e=e))

    logger.info(lmsg("services.shutdown_completo"))
