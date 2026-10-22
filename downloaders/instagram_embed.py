import asyncio
import json
import logging
import os
import re
import html
from typing import Optional

from curl_cffi import requests as curl_requests

import state

from messages import lmsg, msg
from utils import normalize_image, safe_url

from ._caption import _build_caption
from ._caption import _normalize_caption_text
from config import cfg
from url_safety import safe_curl_get

logger = logging.getLogger(__name__)

_SHORTCODE_RE = re.compile(r"/(?:p|reel|tv|reels)/([^/?#]+)")
_VIDEO_POST_RE = re.compile(r"/(?:reel|reels|tv)/", re.IGNORECASE)


def _extract_shortcode(url: str) -> Optional[str]:
    m = _SHORTCODE_RE.search(url)
    return m.group(1) if m else None


def _url_requires_video(url: str) -> bool:
    """Return True when an Instagram URL can only represent video media."""
    return bool(_VIDEO_POST_RE.search(url))


def _parse_context_json(html_text: str) -> Optional[dict]:
    idx = html_text.find('"contextJSON":"')
    if idx < 0:
        return None
    start = idx + len('"contextJSON":"')
    end = start
    while end < len(html_text):
        if html_text[end] == "\\":
            end += 2
            continue
        if html_text[end] == '"':
            break
        end += 1
    raw = html_text[start:end]
    try:
        decoded = json.loads('"' + raw + '"')
        return json.loads(decoded)
    except (json.JSONDecodeError, ValueError) as e:
        logger.debug(lmsg("instagram_embed.contextjson_parse_falhou", e=e))
        return None


def _best_image_url(node: dict) -> Optional[str]:
    resources = node.get("display_resources") or []
    if resources:
        best = max(resources, key=lambda item: item.get("config_width") or 0)
        if best.get("src"):
            return best["src"]
    return node.get("display_url") or None


def _media_from_node(node: dict) -> list[tuple[str, str]]:
    if not isinstance(node, dict):
        return []
    sidecar = node.get("edge_sidecar_to_children")
    if isinstance(sidecar, dict):
        out: list[tuple[str, str]] = []
        for edge in sidecar.get("edges") or []:
            out.extend(_media_from_node(edge.get("node") or {}))
        if out:
            return out
    if node.get("is_video"):
        video_url = node.get("video_url")
        return [("video", video_url)] if video_url else []
    image_url = _best_image_url(node)
    return [("image", image_url)] if image_url else []


def _extract_caption(media: dict) -> str:
    edges = ((media.get("edge_media_to_caption") or {}).get("edges")) or []
    if not edges:
        return ""
    return _normalize_caption_text(edges[0].get("node", {}).get("text", "") or "")


def _find_shortcode_media(data: dict) -> Optional[dict]:
    media = (data.get("gql_data") or {}).get("shortcode_media")
    if isinstance(media, dict):
        return media
    media = (data.get("context") or {}).get("media")
    return media if isinstance(media, dict) else None


def _has_unembedded_music(media: dict) -> bool:
    if not isinstance(media, dict) or media.get("is_video"):
        return False
    attribution = media.get("clips_music_attribution_info")
    return isinstance(attribution, dict) and bool(attribution)


def _extract_fallback_media(html_text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    # 1. Video URLs
    v_urls = re.findall(r'"video_url"\s*:\s*"([^"]+)"', html_text)
    if not v_urls:
        v_urls = re.findall(r'class="EmbeddedMedia[^"]*"[^>]*src="([^"]+\.mp4[^"]*)"', html_text)
    for v in v_urls:
        v_clean = html.unescape(v.replace('\\/', '/'))
        if ("video", v_clean) not in out:
            out.append(("video", v_clean))

    # 2. Image URLs
    if not out:
        img_urls = re.findall(r'class="EmbeddedMedia[^"]*"[^>]*src="([^"]+)"', html_text)
        if not img_urls:
            img_urls = re.findall(r'"display_url"\s*:\s*"([^"]+)"', html_text)
        if not img_urls:
            img_urls = re.findall(r'"display_src"\s*:\s*"([^"]+)"', html_text)
        for img in img_urls:
            img_clean = html.unescape(img.replace('\\/', '/'))
            if not any(m[1] == img_clean for m in out):
                out.append(("image", img_clean))

    return out


async def _download_media_url_curl(url: str, filepath: str) -> bool:
    loop = asyncio.get_running_loop()
    try:
        def _get():
            r = safe_curl_get(
                curl_requests.get, url, impersonate="chrome124", timeout=30,
            )
            if r.status_code == 200 and len(r.content) > 100:
                with open(filepath, "wb") as f:
                    f.write(r.content)
                return True
            return False

        return await loop.run_in_executor(state.IO_POOL, _get)
    except Exception as e:
        logger.error(f"Erro ao baixar mídia com curl_cffi: {e}")
        return False


async def _fetch_embed_html(shortcode: str) -> Optional[str]:
    url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
    loop = asyncio.get_running_loop()
    try:
        def _fetch():
            r = safe_curl_get(
                curl_requests.get, url, impersonate="chrome124", timeout=15,
            )
            if r.status_code == 200:
                return r.content.decode("utf-8", errors="ignore")
            return None

        return await loop.run_in_executor(state.IO_POOL, _fetch)
    except Exception as e:
        logger.warning(lmsg("instagram_embed.curl_cffi_falhou", e=e))
        return None


async def download_instagram_embed(url: str, unique_folder: str) -> tuple[list[str], str, str, str]:
    logger.info(lmsg("instagram_embed.tentando_ig_embed", arg0=safe_url(url)))

    if not os.path.exists(unique_folder):
        os.makedirs(unique_folder)

    shortcode = _extract_shortcode(url)
    if not shortcode:
        logger.debug(lmsg("instagram_embed.n_o_consegui_extrair", arg0=safe_url(url)))
        return [], msg("downloader_status.instagram_embed_fail"), "", ""

    html_text = await _fetch_embed_html(shortcode)
    if not html_text:
        return [], msg("downloader_status.instagram_embed_fail"), "", ""

    items: list[tuple[str, str]] = []
    caption_text = ""
    username = ""
    requires_video = _url_requires_video(url)

    data = _parse_context_json(html_text)
    if data:
        media = _find_shortcode_media(data)
        if media and isinstance(media, dict):
            requires_video = requires_video or bool(media.get("is_video"))
            if _has_unembedded_music(media):
                logger.info("Post do Instagram possui música não incorporada; usando fallbacks completos.")
                return [], msg("downloader_status.instagram_embed_fail"), "", ""
            caption_text = _extract_caption(media)
            username = ((media.get("owner") or {}).get("username")) or ""
            items = _media_from_node(media)

    if not items:
        logger.info("contextJSON ausente ou sem mídia, extraindo via HTML tags...")
        items = _extract_fallback_media(html_text)

        cap_match = re.search(r'class="Caption"[^>]*>(.*?)</div>', html_text, re.DOTALL)
        if cap_match:
            raw_cap = cap_match.group(1)
            raw_cap = re.sub(r'<div class="CaptionComments".*$', '', raw_cap, flags=re.DOTALL)
            raw_cap = re.sub(r'<a class="CaptionUsername"[^>]*>.*?</a>', '', raw_cap, flags=re.DOTALL)
            formatted = re.sub(r'<br\s*/?>', '\n', raw_cap, flags=re.IGNORECASE)
            formatted = re.sub(r'</p>', '\n\n', formatted, flags=re.IGNORECASE)
            formatted = re.sub(r'<[^>]+>', '', formatted)
            caption_text = _normalize_caption_text(
                html.unescape(formatted).replace('\u200b', '')
            )

    if requires_video and items and not any(kind == "video" for kind, _ in items):
        logger.info(
            "IG Embed retornou apenas a capa de uma publicação de vídeo; "
            "continuando para os fallbacks de vídeo."
        )
        return [], msg("downloader_status.instagram_embed_fail"), "", ""

    if not items:
        logger.warning(f"Nenhuma mídia extraída do IG Embed para shortcode {shortcode}")
        return [], msg("downloader_status.instagram_embed_fail"), "", ""

    downloaded: list[str] = []
    for idx, (kind, m_url) in enumerate(items):
        ext = ".mp4" if kind == "video" else ".jpg"
        filepath = os.path.join(unique_folder, f"ig_embed_{idx}{ext}")
        try:
            ok = await _download_media_url_curl(m_url, filepath)
        except Exception as e:
            logger.error(lmsg("instagram_embed.erro_ao_baixar", e=e))
            continue
        if not ok:
            continue
        if kind == "image":
            normalized = normalize_image(filepath, min_size=1)
            if normalized:
                downloaded.append(normalized)
        else:
            downloaded.append(filepath)

    if not downloaded:
        logger.warning(lmsg("instagram_embed.downloads_all_failed", n=len(items), shortcode=shortcode))
        return [], msg("downloader_status.instagram_embed_fail"), "", ""

    try:
        os.chmod(unique_folder, 0o777)
        for f in downloaded:
            os.chmod(f, 0o666)
    except Exception:
        pass

    info_for_caption = {
        "uploader": f"@{username}" if username else "",
        "description": caption_text,
    }
    caption_short, caption_full = _build_caption(info_for_caption, url)

    has_video = any(f.endswith(".mp4") for f in downloaded)
    label = msg("media_type_labels.ig_video") if has_video else msg("media_type_labels.ig_album")
    return downloaded, msg("downloader_status.instagram_embed", media_type=label), caption_short, caption_full
