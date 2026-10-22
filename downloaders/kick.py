import asyncio
import html
import json
import logging
import os
import re
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlparse

from curl_cffi import requests as curl_requests

import state
from config import cfg, FIREFOX_PROFILE_PATH
from messages import lmsg, msg
from url_safety import validate_url
from utils import async_download_file, normalize_image, safe_url
from ._caption import _build_caption
from ._ytdlp import _build_ytdlp_base_opts, _run_ytdlp_with_cookie_fallback

logger = logging.getLogger(__name__)

_KICK_CHANNEL_RE = re.compile(r"kick\.com/([a-zA-Z0-9_]+)(?:/|\?|$)", re.IGNORECASE)
_RESERVED_PATHS = {"video", "videos", "clip", "clips", "categories", "terms", "privacy", "search"}


def _extract_kick_username(url: str) -> Optional[str]:
    match = _KICK_CHANNEL_RE.search(url)
    if match:
        user = match.group(1).lower()
        if user not in _RESERVED_PATHS:
            return user
    return None


async def _fetch_kick_channel_api(username: str) -> Optional[dict]:
    api_url = f"https://kick.com/api/v2/channels/{username}"
    try:
        await validate_url(api_url)
    except Exception as e:
        logger.warning(f"URL de API Kick insegura: {e}")
        return None

    def _get():
        try:
            r = curl_requests.get(
                api_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "application/json",
                },
                impersonate="chrome124",
                timeout=12,
            )
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            logger.warning(f"Falha ao consultar API da Kick para {username}: {e}")
        return None

    return await asyncio.get_running_loop().run_in_executor(state.IO_POOL, _get)


async def _ytdlp_kick(url: str, unique_folder: str) -> Tuple[list[str], str, str, str]:
    has_firefox_cookie = os.path.exists(os.path.join(FIREFOX_PROFILE_PATH, 'cookies.sqlite'))
    base_opts = _build_ytdlp_base_opts(unique_folder)
    downloaded_files, info_dict, _ = await _run_ytdlp_with_cookie_fallback(
        base_opts, url, unique_folder, has_firefox_cookie
    )
    if downloaded_files:
        short_cap, full_cap = _build_caption(info_dict, url)
        return downloaded_files, msg("downloader_status.ytdlp_success"), short_cap, full_cap
    return [], msg("downloader_status.ytdlp_fail"), "", ""


async def download_kick(url: str, unique_folder: str) -> Tuple[list[str], str, str, str]:
    """Processa links da Kick.com (Canais/Lives, Clipes e VODs)."""
    logger.info(lmsg("kick.iniciando_extra_o", arg0=safe_url(url)))

    if not os.path.exists(unique_folder):
        os.makedirs(unique_folder, exist_ok=True)

    # 1. Se for VOD ou Clipe explícito, usar extractor de vídeo (yt-dlp)
    parsed = urlparse(url)
    is_video = "/video/" in parsed.path or "clip=" in parsed.query or "/clips/" in parsed.path
    if is_video:
        logger.info(f"Link da Kick identificado como VOD/Clipe: {safe_url(url)}")
        return await _ytdlp_kick(url, unique_folder)

    # 2. Se for link de Canal / Live
    username = _extract_kick_username(url)
    if not username:
        return await _ytdlp_kick(url, unique_folder)

    channel_data = await _fetch_kick_channel_api(username)
    if not channel_data or not isinstance(channel_data, dict):
        logger.warning(f"Não foi possível obter dados do canal Kick @{username}, caindo para yt-dlp/fallback")
        return await _ytdlp_kick(url, unique_folder)

    user_obj = channel_data.get("user") or {}
    display_name = user_obj.get("username") or username
    bio = user_obj.get("bio") or ""
    livestream = channel_data.get("livestream")
    is_live = isinstance(livestream, dict) and bool(livestream)

    # Escolher imagem (thumb da live ou foto de perfil do canal)
    image_url = None
    if is_live:
        thumbnail_dict = livestream.get("thumbnail") or {}
        image_url = thumbnail_dict.get("url")
        if not image_url and livestream.get("categories"):
            cats = livestream.get("categories")
            if cats and isinstance(cats[0], dict):
                image_url = cats[0].get("banner")

    if not image_url:
        image_url = user_obj.get("profile_pic") or (channel_data.get("banner_image") or {}).get("url")

    # Baixar a imagem da live ou foto de perfil
    downloaded_files: list[str] = []
    if image_url:
        img_file = os.path.join(unique_folder, f"kick_{username}.jpg")
        try:
            await validate_url(image_url)
            ok = await async_download_file(image_url, img_file)
            if ok and os.path.exists(img_file):
                norm = normalize_image(img_file, min_size=1)
                if norm:
                    downloaded_files.append(norm)
        except Exception as e:
            logger.warning(f"Erro ao baixar imagem do canal Kick @{username}: {e}")

    # Montar legenda formatada
    if is_live:
        title = livestream.get("session_title") or "Transmissão Ao Vivo"
        viewers = livestream.get("viewers", 0)
        category = ""
        categories = livestream.get("categories") or []
        if categories and isinstance(categories[0], dict):
            category = categories[0].get("name") or ""

        caption_text = (
            f"🔴 <b>AO VIVO NA KICK</b>\n"
            f"👤 <b>Canal:</b> @{display_name}\n"
            f"📝 <b>Título:</b> {html.escape(title)}\n"
        )
        if category:
            caption_text += f"🎮 <b>Categoria:</b> {html.escape(category)}\n"
        if viewers:
            caption_text += f"👁️ <b>Espectadores:</b> {viewers:,}\n"

        caption_text += (
            f"\nℹ️ <i>Transmissões ao vivo contínuas não podem ser baixadas na íntegra. "
            f"Envie o link de um Clipe ou VOD específico para baixar o vídeo.</i>"
        )
        status_msg = f"🔴 Ao Vivo na Kick: @{display_name}"
    else:
        caption_text = (
            f"⚪ <b>CANAL NA KICK (OFFLINE)</b>\n"
            f"👤 <b>Canal:</b> @{display_name}\n"
        )
        if bio:
            caption_text += f"📝 {html.escape(bio[:200])}\n"
        status_msg = f"Canal Kick: @{display_name}"

    info_dict = {
        "uploader": f"@{display_name}",
        "description": caption_text,
    }
    short_cap, full_cap = _build_caption(info_dict, url)

    return downloaded_files, status_msg, short_cap, full_cap
