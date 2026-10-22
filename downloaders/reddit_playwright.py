import asyncio
import html
import json
import logging
import os
import re
from typing import Optional

import aiohttp
from curl_cffi import requests as cffi_requests

from messages import msg
from utils import safe_url, normalize_image, safe_chmod
import state
from url_safety import install_public_navigation_guard, safe_aiohttp_get, safe_curl_get

from ._scrape_helpers import extract_meta_media

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def build_reddit_caption(title: str, author: str, url: str) -> tuple[str, str]:
    title_clean = re.sub(r'#[a-zA-Z0-9_\.-]+', '', title or '')
    title_clean = re.sub(r'@[a-zA-Z0-9_\.-]+', '', title_clean)
    title_clean = re.sub(r'\s+', ' ', title_clean).strip()

    link_html = f"\U0001f517 <a href='{html.escape(url, quote=True)}'>Link Original</a>"
    header = f"\U0001f4cc <b>{html.escape(author)}</b>\n\n" if author else ""
    caption_full = f"{header}{html.escape(title_clean)}\n\n{link_html}" if title_clean else f"{header}{link_html}"
    caption_short = caption_full[:1024]
    return caption_short, caption_full


def normalize_reddit_url(url: str) -> str:
    """Normalize any Reddit URL (crosspost submit, redd.it, shortlink) to old.reddit target."""
    # 1. Handle submit?source_id=t3_1v2lmxf (crosspost/share menu links)
    m_src = re.search(r'source_id=t3_([a-z0-9]+)', url, re.IGNORECASE)
    if m_src:
        post_id = m_src.group(1)
        return f"https://old.reddit.com/comments/{post_id}/"

    # 2. Handle redd.it/1v2lmxf shortlinks
    m_short = re.search(r'redd\.it/([a-z0-9]+)', url, re.IGNORECASE)
    if m_short and 'i.redd.it' not in url and 'v.redd.it' not in url and 'preview.redd.it' not in url:
        post_id = m_short.group(1)
        return f"https://old.reddit.com/comments/{post_id}/"

    # 3. Standard clean URL (strip fragments & query parameters)
    clean_url = url.split('#')[0].split('?')[0]
    old_url = re.sub(
        r'https?://(?:(?:www|m|v|new|np|old)\.)?reddit\.com',
        'https://old.reddit.com',
        clean_url,
    )
    return old_url


def _reddit_html_candidates(url: str) -> list[str]:
    """Prefer the current Reddit HTML, then try the legacy page."""
    clean_url = url.split('#')[0].split('?')[0]
    # Keep the original query while normalizing: /submit?source_id=t3_<id>
    # contains the only canonical reference to the crosspost's source post.
    url_without_fragment = url.split('#')[0]
    candidates = [clean_url, normalize_reddit_url(url_without_fragment)]
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))


def _is_post_image(url: str) -> bool:
    low = url.lower()
    if any(k in low for k in ('avatar', 'snoovatar', 'headshot', 'profileicon',
                               'bannerbackground', 'image_widget', 'favicon',
                               'communityicon', 'redditstatic', 'redditmedia.com')):
        return False
    return True


def _extract_reddit_media(
    html_text: str, page_url: str = "",
) -> tuple[str, str, str, list[str]]:
    """Extract title, author, video_url, and list of image URLs from old.reddit HTML."""
    title = ""
    author = ""
    v_src = ""
    image_urls = []

    # Title extraction
    m_title = re.search(r'<a class="title[^"]*"[^>]*>([^<]+)</a>', html_text)
    if m_title:
        title = html.unescape(m_title.group(1).strip())
    else:
        m_t2 = re.search(r'<title>([^<]+)</title>', html_text)
        if m_t2:
            title = html.unescape(m_t2.group(1)).split(" : ")[0].split(" - ")[0].strip()

    # Author extraction
    m_auth = re.search(r'data-author="([^"]+)"', html_text)
    if m_auth:
        author = m_auth.group(1)

    # On legacy Reddit, data-url/data-mp4-url belongs to the post expando.
    # Prefer that explicit video marker over og:image, which is often only a
    # thumbnail for a hosted video or a crosspost.
    m_post_video = re.search(
        r"data-(?:url|mp4-url)=[\"'](https://v\.redd\.it/[^\"']+)",
        html_text,
        re.I,
    )
    if m_post_video:
        v_src = html.unescape(m_post_video.group(1))

    # Open Graph metadata belongs to the requested post and is therefore safer
    # than scanning the whole modern page, which also contains recommendations.
    for kind, media_url in extract_meta_media(html_text, page_url):
        if not v_src and kind in ("video", "hls", "dash"):
            v_src = media_url
            break
        if kind == "image" and _is_post_image(media_url):
            image_urls.append(media_url)

    if v_src or image_urls:
        return title, author, v_src, image_urls

    # Video extraction (check data-url, data-mp4-url, or v.redd.it)
    m_v = re.search(r'data-url="(https://v\.redd\.it/[^"]+)"', html_text)
    if m_v:
        v_src = html.unescape(m_v.group(1))
    else:
        m_v2 = re.search(r'https://v\.redd\.it/[a-zA-Z0-9]+', html_text)
        if m_v2:
            v_src = m_v2.group(0)

    # Direct i.redd.it images (highest priority - full res)
    i_imgs = re.findall(r'https://i\.redd\.it/([-a-zA-Z0-9_.]+\.(?:jpg|png|jpeg|webp|gif))', html_text)
    seen = set()
    for fn in i_imgs:
        full = f"https://i.redd.it/{fn}"
        if fn not in seen and _is_post_image(full):
            seen.add(fn)
            image_urls.append(full)

    # Preview images
    prev_imgs = re.findall(r'https://preview\.redd\.it/([-a-zA-Z0-9_.]+\.(?:jpg|png|jpeg|webp|gif))', html_text)
    for fn in prev_imgs:
        full = f"https://i.redd.it/{fn}"
        if fn not in seen and _is_post_image(full):
            seen.add(fn)
            image_urls.append(full)

    return title, author, v_src, image_urls


async def _download_image(session: aiohttp.ClientSession, img_url: str, filepath: str) -> Optional[str]:
    try:
        headers = {"User-Agent": _UA}
        async with safe_aiohttp_get(
            session, img_url, headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 200:
                ct = resp.headers.get('content-type', '')
                if 'text/html' in ct:
                    return None
                body = await resp.read()
                if len(body) > 2000:
                    with open(filepath, 'wb') as f_out:
                        f_out.write(body)
                    normalized = normalize_image(filepath, min_size=1)
                    return normalized or filepath
            else:
                logger.debug(f"Reddit img download {resp.status}: {img_url}")
    except Exception as e:
        logger.warning(f"Erro download Reddit img: {e}")
    return None


async def download_reddit_playwright(url: str, unique_folder: str) -> tuple[list[str], str, str, str]:
    if not os.path.exists(unique_folder):
        os.makedirs(unique_folder)

    old_url = normalize_reddit_url(url)
    logger.info(f"Extração Reddit: {safe_url(url)} -> {safe_url(old_url)}")

    downloaded_files: list[str] = []
    title = ""
    author = ""

    # ── METHOD 1: Fast HTTP via current/old Reddit HTML ─────────────
    try:
        cookies = {"over18": "1"}
        v_src = ""
        img_urls: list[str] = []
        for html_url in _reddit_html_candidates(url):
            resp = await asyncio.to_thread(
                safe_curl_get,
                (state.CFFI_SESSION or cffi_requests).get,
                html_url,
                cookies=cookies,
                headers={"User-Agent": _UA},
                impersonate="chrome124",
                timeout=10,
            )
            if resp.status_code != 200:
                logger.warning(
                    "Reddit HTML retornou status %s: %s",
                    resp.status_code,
                    safe_url(html_url),
                )
                continue

            found_title, found_author, found_video, found_images = _extract_reddit_media(
                resp.text, html_url,
            )
            title = found_title or title
            author = found_author or author
            if found_images:
                # Keep an image result as a fallback, but still ask old.reddit:
                # a modern page often exposes only the thumbnail of a video.
                img_urls = found_images
            if found_video:
                v_src = found_video
            logger.info(
                "Reddit HTML: title='%s', author='%s', video=%s, imgs=%d",
                title,
                author,
                bool(found_video),
                len(found_images),
            )
            if v_src:
                break

        # Download Video via yt-dlp (priority if video present)
        if v_src:
            out_file = os.path.join(unique_folder, "reddit_video.mp4")
            ytdlp_bin = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'venv', 'bin', 'yt-dlp')
            proc = await asyncio.create_subprocess_exec(
                ytdlp_bin, '-o', out_file, v_src,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=120)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            if os.path.exists(out_file) and os.path.getsize(out_file) > 1000:
                downloaded_files.append(out_file)
                caption_short, caption_full = build_reddit_caption(title, author, url)
                try:
                    safe_chmod(unique_folder, 0o777)
                    for f in downloaded_files:
                        safe_chmod(f, 0o666)
                except Exception:
                    pass
                return downloaded_files, msg("downloader_status.reddit_playwright"), caption_short, caption_full

        # Download images using aiohttp
        if img_urls and not v_src:
            session = state.AIOHTTP_SESSION or aiohttp.ClientSession()
            own_session = state.AIOHTTP_SESSION is None
            try:
                count = 0
                for img_u in img_urls:
                    m_ext = re.search(r'\.(png|jpg|jpeg|webp|gif)', img_u, re.IGNORECASE)
                    ext = f".{m_ext.group(1).lower()}" if m_ext else ".jpg"
                    filepath = os.path.join(unique_folder, f"reddit_img_{count}{ext}")
                    result = await _download_image(session, img_u, filepath)
                    if result:
                        downloaded_files.append(result)
                        count += 1
            finally:
                if own_session:
                    await session.close()

            if downloaded_files:
                caption_short, caption_full = build_reddit_caption(title, author, url)
                try:
                    safe_chmod(unique_folder, 0o777)
                    for f in downloaded_files:
                        safe_chmod(f, 0o666)
                except Exception:
                    pass
                return downloaded_files, msg("downloader_status.reddit_playwright"), caption_short, caption_full

    except Exception as e_old:
        logger.warning(f"Erro no HTML do Reddit: {e_old}", exc_info=True)

    # ── METHOD 2: Playwright fallback ──────────────────────────────
    try:
        if state.PW_BROWSER:
            async with state.PW_SEMAPHORE:
                context = await state.PW_BROWSER.new_context(
                    user_agent=_UA,
                    viewport={"width": 1280, "height": 900},
                    service_workers="block",
                )
                await context.add_cookies([
                    {"name": "over18", "value": "1", "domain": ".reddit.com", "path": "/"},
                ])

                page = await context.new_page()
                await install_public_navigation_guard(page)
                try:
                    try:
                        await page.goto(old_url, wait_until="commit", timeout=12000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(4000)
                    content = await page.content()

                    pt = await page.title() or ""
                    if not title:
                        title = pt.split(" : r/")[0].split(" - Reddit")[0].strip()
                    if not author:
                        m_a = re.search(r'author="([^"]+)"', content)
                        if m_a:
                            author = m_a.group(1)

                    # Video search in Playwright content
                    m_v_pw = re.search(r'https://v\.redd\.it/[a-zA-Z0-9]+', content)
                    if m_v_pw and not downloaded_files:
                        v_src = m_v_pw.group(0)
                        out_file = os.path.join(unique_folder, "reddit_video.mp4")
                        ytdlp_bin = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'venv', 'bin', 'yt-dlp')
                        proc = await asyncio.create_subprocess_exec(
                            ytdlp_bin, '-o', out_file, v_src,
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                        )
                        try:
                            await asyncio.wait_for(proc.communicate(), timeout=120)
                        except asyncio.TimeoutError:
                            try:
                                proc.kill()
                                await proc.wait()
                            except Exception:
                                pass
                        if os.path.exists(out_file) and os.path.getsize(out_file) > 1000:
                            downloaded_files.append(out_file)

                finally:
                    await page.close()
                    await context.close()
    except Exception as e_pw:
        logger.error(f"Playwright fallback erro: {e_pw}", exc_info=True)

    if downloaded_files:
        caption_short, caption_full = build_reddit_caption(title, author, url)
        try:
            safe_chmod(unique_folder, 0o777)
            for f in downloaded_files:
                safe_chmod(f, 0o666)
        except Exception:
            pass
        return downloaded_files, msg("downloader_status.reddit_playwright"), caption_short, caption_full

    return [], msg("downloader_status.reddit_playwright_fail"), "", ""
