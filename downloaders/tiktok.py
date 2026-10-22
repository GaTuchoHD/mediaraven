import asyncio
import logging
import os
import re
from typing import Optional
from urllib.parse import quote, urlparse

import aiofiles

import state
from config import cfg, PW_GOTO_TIMEOUT_MS
from messages import msg
from utils import async_ffprobe_codecs, safe_url
from url_safety import install_public_navigation_guard, safe_aiohttp_get

from ._caption import _build_caption

logger = logging.getLogger(__name__)

_VIDEO_ID_RE = re.compile(r"/video/(\d{8,})")
_MEDIA_HOST_SUFFIXES = ("tiktok.com", "tiktokcdn.com", "tiktokcdn-us.com")
_MIN_VIDEO_BYTES = 100_000
_MAX_CAPTURE_MB = 200


def _extract_video_id(url: str) -> Optional[str]:
    match = _VIDEO_ID_RE.search(urlparse(url).path or "")
    return match.group(1) if match else None


def _is_official_media_response(url: str, content_type: str, status: int) -> bool:
    if status not in (200, 206) or not content_type.casefold().startswith("video/"):
        return False
    host = (urlparse(url).hostname or "").casefold()
    if "website-login" in host:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in _MEDIA_HOST_SUFFIXES)


async def _fetch_oembed_caption(url: str) -> tuple[str, str]:
    session = state.AIOHTTP_SESSION
    if session is None or getattr(session, "closed", False):
        return "", ""
    endpoint = f"https://www.tiktok.com/oembed?url={quote(url, safe='')}"
    try:
        async with safe_aiohttp_get(session, endpoint, timeout=15) as response:
            if response.status != 200:
                return "", ""
            payload = await response.json(content_type=None)
        username = str(payload.get("author_unique_id") or payload.get("author_name") or "").strip()
        description = str(payload.get("title") or "").strip()
        uploader = f"@{username.lstrip('@')}" if username else ""
        return _build_caption(
            {"uploader": uploader, "description": description}, url,
        )
    except Exception as exc:
        logger.debug("TikTok oEmbed indisponível para %s: %s", safe_url(url), exc)
        return "", ""


async def _capture_page_video(url: str) -> list[bytes]:
    """Capture video responses consumed by TikTok's own player.

    The public HTML no longer embeds playAddr and current yt-dlp releases can
    miss the rehydration payload.  Chromium still receives the real MP4 while
    rendering the official page, so we keep only large video responses from
    official TikTok media hosts and explicitly reject the login-page loop.
    """
    page = await state.PW_CONTEXT.new_page()
    tasks: list[asyncio.Task] = []
    candidates: list[bytes] = []
    seen_urls: set[str] = set()
    max_bytes = min(_MAX_CAPTURE_MB, max(1, int(cfg("TELEGRAM_MAX_UPLOAD_MB")))) * 1024 * 1024

    async def capture(response) -> None:
        content_type = (response.headers.get("content-type") or "").casefold()
        response_url = response.url
        if (
            response_url in seen_urls
            or not _is_official_media_response(response_url, content_type, response.status)
        ):
            return
        seen_urls.add(response_url)
        try:
            declared = int(response.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            declared = 0
        if declared > max_bytes:
            logger.warning("TikTok: resposta de vídeo acima do limite (%s bytes).", declared)
            return
        try:
            body = await asyncio.wait_for(response.body(), timeout=45)
        except Exception as exc:
            logger.debug("TikTok: não foi possível ler resposta de vídeo: %s", exc)
            return
        if _MIN_VIDEO_BYTES <= len(body) <= max_bytes:
            candidates.append(body)

    def on_response(response) -> None:
        tasks.append(asyncio.create_task(capture(response)))

    page.on("response", on_response)
    try:
        await install_public_navigation_guard(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=PW_GOTO_TIMEOUT_MS)
        await page.wait_for_timeout(7000)
        try:
            await page.locator("video").first.click(timeout=2500)
        except Exception:
            pass
        await page.wait_for_timeout(5000)
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=50)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                try:
                    task.result()
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("TikTok Playwright falhou em %s: %s", safe_url(url), exc)
    finally:
        await page.close()
    return candidates


async def download_tiktok(url: str, unique_folder: str) -> tuple[list[str], str, str, str]:
    if not _extract_video_id(url):
        return [], msg("downloader_status.tiktok_playwright_fail"), "", ""
    if not state.PW_BROWSER or not state.PW_CONTEXT:
        return [], msg("downloader_status.playwright_not_running"), "", ""

    os.makedirs(unique_folder, exist_ok=True)
    candidates: list[bytes] = []
    async with state.PW_SEMAPHORE:
        # A second navigation handles TikTok's occasional login/interstitial
        # response without falling back to unrelated page images.
        for _attempt in range(2):
            candidates.extend(await _capture_page_video(url))
            if candidates:
                break

    filepath = os.path.join(unique_folder, "tiktok_playwright.mp4")
    for body in sorted(candidates, key=len, reverse=True):
        async with aiofiles.open(filepath, "wb") as handle:
            await handle.write(body)
        vcodec, _acodec = await async_ffprobe_codecs(filepath)
        if vcodec:
            short, full = await _fetch_oembed_caption(url)
            return [filepath], msg("downloader_status.tiktok_playwright"), short, full
        try:
            os.remove(filepath)
        except OSError:
            pass

    return [], msg("downloader_status.tiktok_playwright_fail"), "", ""
