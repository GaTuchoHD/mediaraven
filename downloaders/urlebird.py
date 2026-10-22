"""Fast path for Urlebird video pages.

Urlebird exposes the original TikTok CDN asset in ``og:video``.  Reading that
metadata directly avoids an unnecessary yt-dlp TikTok pre-extraction, while
the dispatcher still keeps the regular yt-dlp and scraper paths as fallbacks.
"""
import logging
import os
import re
from html.parser import HTMLParser

from config import cfg
from utils import safe_url

from ._caption import _build_caption
from ._scrape_helpers import extract_meta_media, merge_media_lists
from .fallback import (
    _build_status,
    _download_all,
    _fetch_html,
    _gather_media_via_playwright,
)

logger = logging.getLogger(__name__)
URLEBIRD_PLAYWRIGHT_READY_TIMEOUT_MS = 10_000


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "meta":
            return
        data = {str(k).lower(): str(v) for k, v in attrs if k and v is not None}
        key = (data.get("property") or data.get("name") or "").lower()
        content = data.get("content", "").strip()
        if key and content and key not in self.values:
            self.values[key] = content


def _extract_urlebird_payload(html: str, page_url: str) -> tuple[list[tuple[str, str]], dict]:
    parser = _MetaParser()
    parser.feed(html)

    media = [
        item for item in extract_meta_media(html, page_url)
        if item[0] in ("video", "hls", "dash")
    ]
    media = merge_media_lists(media, cap=1)

    raw_description = parser.values.get("og:description", "").strip()
    parts = [part.strip() for part in raw_description.split("|") if part.strip()]

    uploader_id = ""
    uploader_match = re.search(r"@[\w.]+", parts[-1] if parts else "")
    if uploader_match:
        uploader_id = uploader_match.group(0)

    description_parts = list(parts)
    if description_parts and re.fullmatch(r"[\d.,]+\s*[KMB]?\s+views", description_parts[0], re.I):
        description_parts.pop(0)
    if description_parts and uploader_id and uploader_id in description_parts[-1]:
        description_parts.pop()

    description = " | ".join(description_parts).strip()
    info = {
        "uploader_id": uploader_id,
        "description": description,
        "webpage_url": page_url,
    }
    if not description:
        info["title"] = parser.values.get("og:title", "").strip()
    return media, info


async def download_urlebird(
    url: str,
    unique_folder: str,
) -> tuple[list[str], str, str, str]:
    """Download Urlebird's direct TikTok CDN asset, returning an empty result on miss."""
    html = await _fetch_html(url, timeout=cfg("SCRAPE_FAST_PATH_TIMEOUT_S"))
    media: list[tuple[str, str]] = []
    info: dict = {}
    if html:
        media, info = _extract_urlebird_payload(html, url)

    if not media:
        # Urlebird is frequently protected by Cloudflare for curl_cffi.  The
        # application already maintains a cookie-backed Playwright context;
        # reuse its captured media before falling through to generic extractors.
        pw_media, _iframes, pw_html = await _gather_media_via_playwright(
            url,
            wait_for_video_ms=URLEBIRD_PLAYWRIGHT_READY_TIMEOUT_MS,
        )
        pw_video = [item for item in pw_media if item[0] in ("video", "hls", "dash")]
        if pw_html:
            html_media, html_info = _extract_urlebird_payload(pw_html, url)
            media = merge_media_lists(html_media, pw_video, cap=1)
            info = html_info
        else:
            media = merge_media_lists(pw_video, cap=1)
        if media:
            logger.info("Urlebird resolvido via Playwright após bloqueio do fast path (%s)", safe_url(url))

    if not media:
        logger.info("Urlebird sem mídia direta; seguindo para yt-dlp (%s)", safe_url(url))
        return [], "", "", ""

    os.makedirs(unique_folder, exist_ok=True)
    files, _failed = await _download_all(media, url, unique_folder)
    if not files:
        logger.info("Download direto do Urlebird falhou; seguindo para yt-dlp (%s)", safe_url(url))
        return [], "", "", ""

    caption_short, caption_full = _build_caption(info, url)
    logger.info("Download direto do Urlebird concluído (%s)", safe_url(url))
    return (
        files,
        _build_status(files, "downloader_status.scraper"),
        caption_short,
        caption_full,
    )
