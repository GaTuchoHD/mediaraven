import asyncio
from http.cookiejar import LoadError, MozillaCookieJar
import logging
import os
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientResponseError

import state
from config import cfg
from cookies import get_aiohttp_cookies_for_url
from messages import lmsg, msg
from utils import async_download_file, normalize_image, safe_url
from curl_cffi import requests as cffi_requests
from url_safety import safe_aiohttp_get, safe_curl_get

from .reddit_common import build_reddit_caption, clean_reddit_media_url, looks_like_image

logger = logging.getLogger(__name__)

# Internal dispatcher signal. It must never be shown to the Telegram user.
REDDIT_JSON_VIDEO = "__reddit_json_video__"
REDDIT_UNAVAILABLE = "__reddit_unavailable__"

_REDDIT_COOKIE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "reddit_cookies.txt",
)


def _load_reddit_cookie_file() -> dict[str, str]:
    """Load the dedicated Reddit Netscape cookie jar without exposing values."""
    if not os.path.isfile(_REDDIT_COOKIE_FILE):
        return {}

    jar = MozillaCookieJar(_REDDIT_COOKIE_FILE)
    try:
        jar.load(ignore_discard=True, ignore_expires=False)
    except (LoadError, OSError) as exc:
        logger.warning("⚠️ Não foi possível carregar os cookies do Reddit: %s", exc)
        return {}

    return {
        cookie.name: cookie.value
        for cookie in jar
        if not cookie.is_expired()
    }


def _is_reddit_video_post(post_data: dict) -> bool:
    """Detect Reddit video metadata, including crosspost wrappers."""
    pending = [post_data]
    visited = 0
    while pending and visited < 8:
        candidate = pending.pop(0)
        if not isinstance(candidate, dict):
            continue
        visited += 1

        secure_media = candidate.get("secure_media") or {}
        media = candidate.get("media") or {}
        media_url = candidate.get("url") or ""
        media_host = (urlsplit(media_url).hostname or "").lower()
        if (
            candidate.get("is_video")
            or candidate.get("post_hint") in ("hosted:video", "rich:video")
            or bool(secure_media.get("reddit_video"))
            or bool(media.get("reddit_video"))
            or media_host == "v.redd.it"
        ):
            return True

        crossposts = candidate.get("crosspost_parent_list") or []
        if isinstance(crossposts, list):
            pending.extend(item for item in crossposts if isinstance(item, dict))
    return False


async def _fetch_reddit_json_with_curl(json_url: str) -> list:
    """Use curl's TLS/HTTP stack when Reddit blocks aiohttp's fingerprint."""
    def _request() -> list:
        response = safe_curl_get(
            (state.CFFI_SESSION or cffi_requests).get,
            json_url,
            impersonate="chrome124",
            timeout=20,
            headers={
                "User-Agent": cfg("REDDIT_JSON_UA"),
                "Accept": "application/json",
            },
            cookies=_load_reddit_cookie_file(),
        )
        response.raise_for_status()
        return response.json()

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(state.IO_POOL, _request)


async def download_reddit_json(url: str, unique_folder: str) -> tuple[list[str], str, str, str]:
    logger.info(lmsg("reddit_json.iniciando_extra_o_via", arg0=safe_url(url)))
    if not os.path.exists(unique_folder):
        os.makedirs(unique_folder)

    media_urls = []
    downloaded_files = []
    title = ""
    selftext = ""

    headers = {
        'User-Agent': cfg("REDDIT_JSON_UA"),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.8,en-US;q=0.5,en;q=0.3',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
    }

    cookies_dict = get_aiohttp_cookies_for_url(url)
    reddit_file_cookies = _load_reddit_cookie_file()
    if reddit_file_cookies:
        cookies_dict.update(reddit_file_cookies)
        logger.info(
            "🍪 Usando %d cookies do arquivo dedicado do Reddit.",
            len(reddit_file_cookies),
        )
    if cookies_dict:
         logger.info(lmsg("reddit_json.usando_x_cookies", arg0=len(cookies_dict)))

    try:
        parsed_url = urlsplit(url)
        clean_url = urlunsplit(
            (parsed_url.scheme, parsed_url.netloc, parsed_url.path.rstrip('/'), '', '')
        )
        json_url = f"{clean_url}.json?raw_json=1"

        try:
            async with safe_aiohttp_get(
                state.AIOHTTP_SESSION, json_url,
                headers=headers, cookies=cookies_dict, timeout=15,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except ClientResponseError as exc:
            if exc.status != 403:
                raise
            logger.info(
                "↪️ Reddit bloqueou o aiohttp; repetindo a consulta pelo transporte compatível."
            )
            data = await _fetch_reddit_json_with_curl(json_url)

        post_data = data[0]['data']['children'][0]['data']
        if post_data.get('removed_by_category'):
            logger.info("Reddit confirmou conteúdo removido ou indisponível: %s", safe_url(url))
            return [], REDDIT_UNAVAILABLE, "", ""
        title = post_data.get('title', '') or ''
        selftext = post_data.get('selftext', '') or ''

        if _is_reddit_video_post(post_data):
            logger.info(lmsg("reddit_json.post_v_deo"))
            return [], REDDIT_JSON_VIDEO, "", ""

        if 'media_metadata' in post_data:
            gallery_items = post_data.get('gallery_data', {}).get('items', [])
            if gallery_items:
                for item in gallery_items:
                    media_id = item['media_id']
                    media_info = post_data['media_metadata'].get(media_id, {})
                    if media_info.get('status') == 'valid':
                        img_url = media_info.get('s', {}).get('u') or media_info.get('s', {}).get('gif')
                        clean_u = clean_reddit_media_url(img_url)
                        if clean_u and clean_u not in media_urls: media_urls.append(clean_u)
            else:
                for media_id, media_info in post_data['media_metadata'].items():
                    if media_info.get('status') == 'valid':
                        img_url = media_info.get('s', {}).get('u') or media_info.get('s', {}).get('gif')
                        clean_u = clean_reddit_media_url(img_url)
                        if clean_u and clean_u not in media_urls: media_urls.append(clean_u)

        elif 'url' in post_data and looks_like_image(post_data['url']):
            clean_u = clean_reddit_media_url(post_data['url'])
            if clean_u and clean_u not in media_urls: media_urls.append(clean_u)

        elif 'preview' in post_data and 'images' in post_data['preview']:
            img_url = post_data['preview']['images'][0]['source']['url']
            clean_u = clean_reddit_media_url(img_url)
            if clean_u and clean_u not in media_urls: media_urls.append(clean_u)

    except ClientResponseError as e:
        if e.status == 403:
            logger.warning(
                "⚠️ Reddit bloqueou a API JSON com HTTP 403; o próximo extrator será tentado: %s",
                safe_url(url),
            )
        else:
            logger.error(lmsg("reddit_json.erro_ao_ler", e=e), exc_info=True)
    except Exception as e:
        logger.error(lmsg("reddit_json.erro_ao_ler", e=e), exc_info=True)

    count = 0
    for m_url in media_urls:
        try:
            ext = ".jpg" if not m_url[-4:].startswith('.') else m_url[-4:]
            filepath = os.path.join(unique_folder, f"reddit_media_{count}{ext}")

            success = await async_download_file(m_url, filepath)

            if success:
                if not filepath.lower().endswith(('.mp4', '.gif', '.webm')):
                    normalized = normalize_image(filepath, min_size=1)
                    if normalized is None:
                        continue
                    filepath = normalized

                downloaded_files.append(filepath)
                count += 1
        except Exception as e:
            logger.error(lmsg("reddit_json.erro_ao_processar", e=e))

    if downloaded_files:
        caption_short, caption_full = build_reddit_caption(title, selftext, url)
        return downloaded_files, msg("downloader_status.reddit_json"), caption_short, caption_full
    return [], msg("downloader_status.reddit_json_fail"), "", ""
