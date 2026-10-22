import html
import re
from urllib.parse import urlsplit, urlunsplit

from ._caption import _build_caption


_REDDIT_MEDIA_HOSTS = ('i.redd.it', 'preview.redd.it')
_REDDIT_JUNK_MARKERS = ('award_images', 'snoovatar', 'avatars', 'icon')
_IMG_EXT_RE = re.compile(r'\.(jpg|jpeg|png|webp|gif)($|\?)', re.IGNORECASE)


def build_reddit_caption(title: str, selftext: str, url: str) -> tuple[str, str]:
    info = {"title": title or "", "description": selftext or ""}
    return _build_caption(info, url)


def clean_reddit_media_url(raw_url: str) -> str | None:
    if not raw_url:
        return None
    cleaned = html.unescape(raw_url)
    parsed = urlsplit(cleaned)
    host = (parsed.hostname or "").lower()

    if host == "preview.redd.it":
        parsed = parsed._replace(netloc="i.redd.it", query="", fragment="")
    elif host == "i.redd.it":
        parsed = parsed._replace(query="", fragment="")
    else:
        # External preview URLs use a signed query string. Rewriting the
        # substring in external-preview.redd.it creates the nonexistent
        # external-i.redd.it host and removing the query invalidates the URL.
        parsed = parsed._replace(fragment="")

    return urlunsplit(parsed)


def is_reddit_media_url(url: str) -> bool:
    if not url:
        return False
    if not any(host in url for host in _REDDIT_MEDIA_HOSTS):
        return False
    lowered = url.lower()
    if any(marker in lowered for marker in _REDDIT_JUNK_MARKERS):
        return False
    return True


def looks_like_image(url: str) -> bool:
    return bool(_IMG_EXT_RE.search(url or ''))
