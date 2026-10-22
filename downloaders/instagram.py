import asyncio
import logging
import os

import aiofiles

import state
from config import cfg
from messages import lmsg, msg
from utils import async_merge_audio_image, safe_url
from url_safety import safe_aiohttp_get

from ._caption import _normalize_caption_text

logger = logging.getLogger(__name__)


async def download_instagram_instagrapi(url: str, unique_folder: str) -> tuple[list[str], str, str, str]:
    logger.info(lmsg("instagram.iniciando_instagrapi_para", arg0=safe_url(url)))
    if not os.path.exists(unique_folder):
        os.makedirs(unique_folder)

    def perform_instagrapi():
        try:
            client = state.IG_CLIENT
            guest_client = client is None
            if guest_client:
                # Public posts can still be resolved through Instagram's public
                # GraphQL endpoint. This is especially useful for Reels whose
                # embed page exposes only the cover image and no video URL.
                from instagrapi import Client

                logger.info("Instagrapi autenticado indisponível; tentando cliente público.")
                client = Client()

            media_pk = client.media_pk_from_url(url)
            media_info = (
                client.media_info_gql(media_pk)
                if guest_client
                else client.media_info(media_pk)
            )

            audio_url = None
            start_time_sec = None
            overlap_duration_sec = None
            track_duration_sec = None
            duration_sec = 15.0

            resource_count = len(getattr(media_info, 'resources', []) or [])
            is_single_photo = (
                media_info.media_type == 1
                or (media_info.media_type == 8 and resource_count <= 1)
            )
            if is_single_photo and not guest_client:
                try:
                    logger.info(lmsg("instagram.buscando_udio_e"))
                    raw_data = client.private_request(f"media/{media_pk}/info/")

                    def extract_audio_data(data):
                        nonlocal audio_url, start_time_sec, overlap_duration_sec, track_duration_sec
                        if isinstance(data, dict):
                            if data.get('progressive_download_url') and not audio_url:
                                audio_url = data['progressive_download_url']
                            if 'audio_asset_start_time_in_ms' in data and start_time_sec is None:
                                start_time_sec = data.get('audio_asset_start_time_in_ms', 0) / 1000.0
                            if 'overlap_duration_in_ms' in data and overlap_duration_sec is None:
                                overlap_duration_sec = data.get('overlap_duration_in_ms', 0) / 1000.0
                            if 'duration_in_ms' in data and track_duration_sec is None:
                                track_duration_sec = data.get('duration_in_ms', 0) / 1000.0
                            for k, v in data.items(): extract_audio_data(v)
                        elif isinstance(data, list):
                            for item in data: extract_audio_data(item)

                    extract_audio_data(raw_data)

                    if overlap_duration_sec and overlap_duration_sec > 0:
                        duration_sec = overlap_duration_sec
                    elif track_duration_sec and track_duration_sec > 0:
                        duration_sec = min(track_duration_sec, 90.0)
                    else:
                        duration_sec = 30.0

                except Exception as e:
                    logger.warning(lmsg("instagram.erro_ao_buscar", e=e))

            paths = []
            if media_info.media_type == 1:
                paths.append(str(client.photo_download_by_url(
                    str(media_info.thumbnail_url), folder=unique_folder)))
            elif media_info.media_type == 2:
                video_url = getattr(media_info, "video_url", None)
                if video_url:
                    paths.append(str(client.video_download_by_url(
                        str(video_url), folder=unique_folder)))
                else:
                    paths.append(str(client.video_download(media_pk, folder=unique_folder)))
            elif media_info.media_type == 8:
                paths.extend([str(p) for p in client.album_download(media_pk, folder=unique_folder)])

            caption_text = _normalize_caption_text(
                getattr(media_info, 'caption_text', "") or ""
            )
            return paths, audio_url, duration_sec, start_time_sec, media_info.media_type, caption_text, is_single_photo
        except Exception as e:
            logger.error(lmsg("instagram.instagrapi_falhou_x", e=e), exc_info=True)
            return None

    queue_size = state.ig_pending_inc()
    if queue_size >= cfg("IG_QUEUE_WARN_THRESHOLD"):
        logger.warning(lmsg("instagram.fila_do_instagrapi", queue_size=queue_size, IG_QUEUE_WARN_THRESHOLD=cfg("IG_QUEUE_WARN_THRESHOLD")))

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(state.IG_POOL, perform_instagrapi)
    finally:
        state.ig_pending_dec()

    if not result:
        return [], msg("downloader_status.instagrapi_fail"), "", ""

    paths, audio_url, duration_sec, start_time_sec, media_type, caption_text, is_single_photo = result

    audio_path = None
    if audio_url:
        try:
            logger.info(lmsg("instagram.baixando_udio_puro"))
            a_path = os.path.join(unique_folder, "temp_audio.m4a")
            headers_ig = {'User-Agent': cfg("IG_USER_AGENT")}
            async with safe_aiohttp_get(
                state.AIOHTTP_SESSION, audio_url, headers=headers_ig, timeout=15,
            ) as r:
                if r.status == 200:
                    async with aiofiles.open(a_path, 'wb') as f:
                        async for chunk in r.content.iter_chunked(8192):
                            await f.write(chunk)
                    audio_path = a_path
        except Exception as e:
            logger.error(lmsg("instagram.erro_ao_baixar", e=e), exc_info=True)

    if is_single_photo and audio_path and os.path.exists(audio_path):
        logger.info(lmsg("instagram.transformando_foto_em"))
        merged_paths = []
        for img_path in paths:
            if img_path.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                video_output = img_path.rsplit('.', 1)[0] + '_video.mp4'

                if await async_merge_audio_image(img_path, audio_path, video_output, start_time_sec, duration_sec):
                    merged_paths.append(video_output)
                    try:
                        os.remove(img_path)
                    except OSError as e:
                        logger.debug(lmsg("instagram.falha_ao_remover", img_path=img_path, e=e))
                else:
                    merged_paths.append(img_path)
            else:
                merged_paths.append(img_path)

        paths = merged_paths
        try:
            os.remove(audio_path)
        except OSError as e:
            logger.debug(lmsg("instagram.falha_ao_remover_2", audio_path=audio_path, e=e))

    has_video = any(f.endswith('.mp4') for f in paths)
    if has_video and audio_path:
        m_type = msg("media_type_labels.ig_video_music")
    elif has_video:
        m_type = msg("media_type_labels.ig_video")
    else:
        m_type = msg("media_type_labels.ig_album")

    caption_full = caption_text
    if caption_text and len(caption_text) > cfg("IG_CAPTION_MAX"):
        caption_short = caption_text[:cfg("IG_CAPTION_MAX")] + "..."
    else:
        caption_short = caption_text

    return paths, msg("downloader_status.instagrapi", media_type=m_type), caption_short, caption_full
