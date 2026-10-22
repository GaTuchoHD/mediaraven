import logging
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import state
from config import AIOHTTP_UA_DEFAULT
from utils import safe_url
from messages import lmsg
from url_safety import safe_aiohttp_get

logger = logging.getLogger(__name__)


@dataclass
class Platform:
    threads: bool = False
    instagram: bool = False
    youtube: bool = False
    reddit: bool = False
    facebook: bool = False
    x: bool = False
    tiktok: bool = False
    pinterest: bool = False
    bluesky: bool = False
    twitch: bool = False
    urlebird: bool = False


_PLATFORM_HOSTS = {
    'threads': ('threads.net', 'threads.com'),
    'instagram': ('instagram.com',),
    'youtube': ('youtube.com', 'youtu.be', 'm.youtube.com', 'music.youtube.com'),
    'reddit': ('reddit.com', 'redd.it', 'old.reddit.com', 'new.reddit.com'),
    'facebook': ('facebook.com', 'fb.com', 'fb.watch', 'm.facebook.com'),
    'x': ('x.com', 'twitter.com', 'mobile.twitter.com', 'fxtwitter.com', 'vxtwitter.com', 'fixupx.com'),
    'tiktok': ('tiktok.com', 'vm.tiktok.com', 'vt.tiktok.com'),
    'pinterest': ('pinterest.com', 'pin.it', 'pinterest.co.uk', 'pinterest.cl', 'pinterest.fr'),
    'bluesky': ('bsky.app', 'bsky.social'),
    'twitch': ('twitch.tv', 'clips.twitch.tv'),
    'urlebird': ('urlebird.com',),
}


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    for suffix in suffixes:
        if host == suffix or host.endswith('.' + suffix):
            return True
    return False


def _detect_platform(url: str) -> Platform:
    try:
        host = (urlparse(url).netloc or '').lower()
    except Exception:
        host = ''
    if host.startswith('www.'):
        host = host[4:]
    return Platform(
        threads=_host_matches(host, _PLATFORM_HOSTS['threads']),
        instagram=_host_matches(host, _PLATFORM_HOSTS['instagram']),
        youtube=_host_matches(host, _PLATFORM_HOSTS['youtube']),
        reddit=_host_matches(host, _PLATFORM_HOSTS['reddit']),
        facebook=_host_matches(host, _PLATFORM_HOSTS['facebook']),
        x=_host_matches(host, _PLATFORM_HOSTS['x']),
        tiktok=_host_matches(host, _PLATFORM_HOSTS['tiktok']),
        pinterest=_host_matches(host, _PLATFORM_HOSTS['pinterest']),
        bluesky=_host_matches(host, _PLATFORM_HOSTS['bluesky']),
        twitch=_host_matches(host, _PLATFORM_HOSTS['twitch']),
        urlebird=_host_matches(host, _PLATFORM_HOSTS['urlebird']),
    )


async def _resolve_short_reddit_url(url: str) -> str:
    if not _detect_platform(url).reddit or "/s/" not in url:
        return url
    try:
        headers = {'User-Agent': AIOHTTP_UA_DEFAULT}
        async with safe_aiohttp_get(state.AIOHTTP_SESSION, url, headers=headers, timeout=15) as resp:
            new_url = str(resp.url)
        # Reddit occasionally resolves a /s/ shortlink to the subreddit root
        # instead of the post. Keep the original link in that case so the
        # downstream Reddit and yt-dlp handlers still receive the post token.
        if _detect_platform(new_url).reddit:
            resolved_path = (urlparse(new_url).path or '').lower()
            if '/comments/' not in resolved_path and '/s/' not in resolved_path:
                logger.warning(
                    "Reddit shortlink resolveu sem post; mantendo URL original: %s",
                    safe_url(new_url),
                )
                return url
        return new_url
    except Exception:
        return url


async def _resolve_threads_share_url(url: str) -> str:
    """Resolve official Threads ``/share/`` links to a canonical post URL.

    Threads' downloader extracts the post code from ``/@user/post/<code>``.
    The short share URL contains no code, so it must be followed first.  Only
    redirects that remain on an official Threads host and contain ``/post/``
    are trusted; every other result falls back to the original URL.
    """
    parsed = urlparse(url)
    host = (parsed.netloc or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    if not _host_matches(host, _PLATFORM_HOSTS['threads']):
        return url
    if not (parsed.path or '').startswith('/share/'):
        return url

    session = state.AIOHTTP_SESSION
    if session is None or getattr(session, 'closed', False):
        return url

    try:
        headers = {'User-Agent': AIOHTTP_UA_DEFAULT}
        async with safe_aiohttp_get(session, url, headers=headers, timeout=15) as resp:
            new_url = str(resp.url)
        resolved = urlparse(new_url)
        resolved_host = (resolved.netloc or '').lower()
        if resolved_host.startswith('www.'):
            resolved_host = resolved_host[4:]
        if (
            _host_matches(resolved_host, _PLATFORM_HOSTS['threads'])
            and '/post/' in (resolved.path or '')
        ):
            return new_url
        logger.warning(
            "Threads share resolveu para destino inválido; mantendo URL original: %s",
            safe_url(new_url),
        )
    except Exception as exc:
        logger.warning(
            "Falha ao resolver Threads share %s: %s", safe_url(url), exc,
        )
    return url


_KWAI_HOSTS = ('kwai-video.com', 'kwai.com', 'm.kwai.com', 'kw.ai', 's.kw.ai', 'snackvideo.com', 'snackvideo.in')


def _is_kwai_host(host: str) -> bool:
    if host.startswith('www.'):
        host = host[4:]
    return any(host == h or host.endswith('.' + h) for h in _KWAI_HOSTS)


async def _resolve_kwai_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or '').lower()
    if not _is_kwai_host(host):
        return url
    new_url = url
    try:
        headers = {'User-Agent': AIOHTTP_UA_DEFAULT}
        async with safe_aiohttp_get(state.AIOHTTP_SESSION, url, headers=headers, timeout=15) as resp:
            new_url = str(resp.url)
    except Exception:
        pass
    final_parsed = urlparse(new_url)
    cleaned = urlunparse(final_parsed._replace(query="", fragment=""))
    return cleaned


async def _resolve_facebook_share_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    if not (host == 'facebook.com' or host.endswith('.facebook.com')):
        return url
    if '/share/' not in parsed.path:
        return url
    try:
        headers = {'User-Agent': AIOHTTP_UA_DEFAULT}
        async with safe_aiohttp_get(state.AIOHTTP_SESSION, url, headers=headers, timeout=15) as resp:
            new_url = str(resp.url)
        return new_url
    except Exception:
        return url


def _normalize_youtube_url(url: str) -> str:
    url = url.split("?si=")[0].split("&si=")[0]
    if "/shorts/" in url:
        try:
            video_id = url.split("/shorts/")[1].split("?")[0]
            url = f"https://www.youtube.com/watch?v={video_id}"
        except Exception:
            pass
    return url
