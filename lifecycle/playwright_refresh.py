import asyncio
import logging
from typing import Optional

import psutil

import state
from config import (
    PLAYWRIGHT_UA,
    PW_CONCURRENCY,
    PW_REFRESH_CHECK_INTERVAL_MIN,
    PW_REFRESH_MAX_INTERVAL_HOURS,
    PW_REFRESH_MIN_INTERVAL_MIN,
    PW_REFRESH_RSS_MB_THRESHOLD,
    PW_VIEWPORT_HEIGHT,
    PW_VIEWPORT_WIDTH,
)
from messages import lmsg
from cookies import extract_firefox_cookies

logger = logging.getLogger(__name__)


PW_REFRESH_CHECK_INTERVAL = PW_REFRESH_CHECK_INTERVAL_MIN * 60
PW_REFRESH_MAX_INTERVAL = PW_REFRESH_MAX_INTERVAL_HOURS * 3600
PW_REFRESH_MIN_INTERVAL = PW_REFRESH_MIN_INTERVAL_MIN * 60

_proc = psutil.Process()


def _get_process_rss_mb() -> Optional[float]:
    try:
        return _proc.memory_info().rss / (1024 * 1024)
    except (psutil.Error, OSError) as e:
        logger.debug(lmsg("playwright_refresh.falha_ao_ler", e=e))
        return None


async def periodic_playwright_refresh() -> None:
    loop = asyncio.get_running_loop()
    last_refresh = loop.time()

    while True:
        await asyncio.sleep(PW_REFRESH_CHECK_INTERVAL)

        if not state.PW_BROWSER:
            continue

        now = loop.time()
        elapsed = now - last_refresh
        if elapsed < PW_REFRESH_MIN_INTERVAL:
            continue

        rss_mb = _get_process_rss_mb()
        should_refresh = False
        reason = ""
        if rss_mb is not None and rss_mb >= PW_REFRESH_RSS_MB_THRESHOLD:
            should_refresh = True
            reason = f"RSS {rss_mb:.0f}MB >= {PW_REFRESH_RSS_MB_THRESHOLD}MB"
        elif elapsed >= PW_REFRESH_MAX_INTERVAL:
            should_refresh = True
            reason = f"intervalo máximo atingido ({elapsed/3600:.1f}h)"

        if not should_refresh:
            continue

        logger.info(lmsg("playwright_refresh.iniciando_limpeza_de", reason=reason))
        last_refresh = now

        try:
            new_context = await state.PW_BROWSER.new_context(
                user_agent=PLAYWRIGHT_UA,
                viewport={'width': PW_VIEWPORT_WIDTH, 'height': PW_VIEWPORT_HEIGHT},
            )

            state.FIREFOX_COOKIES_CACHE = extract_firefox_cookies()

            if state.FIREFOX_COOKIES_CACHE:
                try:
                    await new_context.add_cookies(state.FIREFOX_COOKIES_CACHE)
                    logger.info(lmsg("playwright_refresh.x_cookies_do", arg0=len(state.FIREFOX_COOKIES_CACHE)))
                except Exception as e:
                    logger.warning(lmsg("playwright_refresh.erro_ao_injetar", e=e))

            tickets = 0
            try:
                for _ in range(PW_CONCURRENCY):
                    await state.PW_SEMAPHORE.acquire()
                    tickets += 1
                old_context = state.PW_CONTEXT
                state.PW_CONTEXT = new_context

                if old_context:
                    await old_context.close()

                logger.info(lmsg("playwright_refresh.contexto_playwright_recriado"))
            finally:
                for _ in range(tickets):
                    state.PW_SEMAPHORE.release()

        except Exception as e:
            logger.error(lmsg("playwright_refresh.erro_ao_recriar", e=e), exc_info=True)
