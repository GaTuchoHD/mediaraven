import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import state
from config import PW_GOTO_TIMEOUT_MS

logger = logging.getLogger(__name__)


@asynccontextmanager
async def playwright_page(
    url: Optional[str] = None,
    timeout_ms: int = PW_GOTO_TIMEOUT_MS,
    wait_until: str = "domcontentloaded",
) -> AsyncGenerator[Optional[object], None]:
    """Context manager para adquirir semáforo, criar página, navegar e garantir fechamento."""
    if not state.PW_CONTEXT:
        yield None
        return

    async with state.PW_SEMAPHORE:
        page = None
        try:
            page = await state.PW_CONTEXT.new_page()
            if url:
                await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            yield page
        except Exception as e:
            if url:
                logger.warning(f"Erro Playwright navegando para {url[:60]}: {e}")
            yield None
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
