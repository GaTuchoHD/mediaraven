import logging
import os
import re
import html
from typing import Optional

from curl_cffi import requests
from messages import msg
from utils import safe_url, async_download_file
from url_safety import safe_curl_get
from ._caption import _clean_text_no_tags_mentions, _normalize_caption_text

logger = logging.getLogger(__name__)

async def download_x(url: str, unique_folder: str) -> tuple[list[str], str, str, str]:
    if not os.path.exists(unique_folder):
        os.makedirs(unique_folder)

    m = re.search(r'status/([0-9]+)', url)
    if not m:
        return [], msg("downloader_status.x_fail"), "", ""

    tweet_id = m.group(1)
    api_url = f"https://api.fxtwitter.com/i/status/{tweet_id}"
    logger.info(f"Extraindo mídias do X/Twitter via FXTwitter API: {safe_url(url)}")

    try:
        r = safe_curl_get(requests.get, api_url, impersonate="chrome124", timeout=12)
        if r.status_code != 200:
            logger.warning(f"FXTwitter retornou HTTP {r.status_code} para {safe_url(url)}")
            return [], msg("downloader_status.x_fail"), "", ""

        data = r.json()
        tweet = data.get("tweet", {})
        author_info = tweet.get("author", {})
        author_name = author_info.get("name", "")
        author_user = author_info.get("screen_name", "")
        text_raw = tweet.get("text", "")

        cleaned_desc = _clean_text_no_tags_mentions(text_raw)

        media_info = tweet.get("media") or {}
        all_media = media_info.get("all") or []
        if not all_media:
            quoted_tweet = tweet.get("quote") or {}
            quoted_media = quoted_tweet.get("media") or {}
            all_media = quoted_media.get("all") or []
            if all_media:
                logger.info(
                    "Post principal do X sem mídia; usando mídia do post citado."
                )

        # Text is a valid result only when the post has no media to download.
        # Preserve media failures as failures rather than concealing them as text.
        if not all_media:
            post_text = _normalize_caption_text(text_raw)
            if post_text:
                author = author_name or author_user
                if author_name and author_user:
                    author = f"{author_name} (@{author_user})"
                header = f"📌 <b>{html.escape(author)}</b>\n\n" if author else ""
                link = f"🔗 <a href='{html.escape(url, quote=True)}'>Link Original</a>"
                full_text = f"{header}{html.escape(post_text)}\n\n{link}"
                short_text = (
                    full_text if len(full_text) <= 1024
                    else f"{header}📝 Texto do post do X\n\n{link}"
                )
                return [], "📝 Texto do post do X extraído.", short_text, full_text

        downloaded_files = []
        count = 0

        for m_item in all_media:
            m_type = m_item.get("type")
            if m_type == "photo":
                img_u = m_item.get("url")
                if img_u:
                    filepath = os.path.join(unique_folder, f"x_photo_{count}.jpg")
                    ok = await async_download_file(img_u, filepath)
                    if ok and os.path.exists(filepath) and os.path.getsize(filepath) > 1000:
                        downloaded_files.append(filepath)
                        count += 1
            elif m_type in ("video", "gif"):
                variants = m_item.get("variants", [])
                best_mp4 = None
                best_bitrate = -1
                for v in variants:
                    if v.get("content_type") == "video/mp4":
                        b = v.get("bitrate", 0)
                        if b > best_bitrate:
                            best_bitrate = b
                            best_mp4 = v.get("url")
                if best_mp4:
                    filepath = os.path.join(unique_folder, f"x_video_{count}.mp4")
                    ok = await async_download_file(best_mp4, filepath)
                    if ok and os.path.exists(filepath) and os.path.getsize(filepath) > 1000:
                        downloaded_files.append(filepath)
                        count += 1

        if downloaded_files:
            try:
                os.chmod(unique_folder, 0o777)
                for f in downloaded_files:
                    os.chmod(f, 0o666)
            except Exception:
                pass

            link_html = f"🔗 <a href='{html.escape(url, quote=True)}'>Link Original</a>"
            header = f"📌 <b>{html.escape(author_name or author_user)}</b>\n\n" if (author_name or author_user) else ""
            caption_full = f"{header}{html.escape(cleaned_desc)}\n\n{link_html}" if cleaned_desc else f"{header}{link_html}"
            caption_short = caption_full[:1024]
            return downloaded_files, msg("downloader_status.ytdlp_success"), caption_short, caption_full

    except Exception as e:
        logger.error(f"Erro ao baixar do X/Twitter: {e}", exc_info=True)

    return [], msg("downloader_status.x_fail"), "", ""
