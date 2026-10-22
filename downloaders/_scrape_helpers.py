"""Helpers puros para o scrape genérico de fallback.

Funções aqui não têm I/O (exceto parse de HTML em memória) — são testáveis em
isolamento e reutilizáveis pelo fast path HTTP e pelo Playwright scraper.
"""
import html as _html
import json
import logging
import re
from typing import Iterable, Optional
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse
from messages import lmsg

logger = logging.getLogger(__name__)


MediaTuple = tuple[str, str]  # (kind, url) — kind ∈ {"image", "video", "hls"}


_VIDEO_EXT_PATTERN = re.compile(r"\.(mp4|webm|mov|m4v|mkv)(?:[?#]|$)", re.I)
_IMAGE_EXT_PATTERN = re.compile(r"\.(jpg|jpeg|png|webp|gif|avif|bmp|jfif)(?:[?#]|$)", re.I)
_HLS_EXT_PATTERN = re.compile(r"\.m3u8(?:[?#]|$)", re.I)
_DASH_EXT_PATTERN = re.compile(r"\.mpd(?:[?#]|$)", re.I)


_JUNK_PATH_HINTS = (
    'pixel.gif', 'pixel.png', '/1x1.', '/spacer.',
    'tracking', 'analytics', 'beacon',
    'gtag', 'doubleclick', 'googletagmanager',
    '/favicon.',
    '/rsrc.php/', '/safe_image.php',
    'headshot', 'snoovatar', '_profile', 'communityicon', 'profileicon',
    'communityicon_', 'awardicon', 'profileicon_',
)
_JUNK_HOST_HINTS = (
    'doubleclick.net', 'googletagmanager.com', 'google-analytics.com',
    'facebook.com/tr', 'connect.facebook.net',
    'scorecardresearch.com', 'quantserve.com',
    'static.xx.fbcdn.net', 'static.cdninstagram.com',
    'styles.redditmedia.com', 'alb.reddit.com', 'id.rlcdn.com',
)


def is_junk_url(url: str, *, min_len: int = 8) -> bool:
    """Heurística: avatar/sprite/tracking pixel/asset estático."""
    if not url or len(url) < min_len:
        return True
    low = url.lower()
    if low.startswith('data:'):
        return True
    try:
        host = (urlparse(url).netloc or '').lower()
    except Exception:
        host = ''
    if any(h in host for h in _JUNK_HOST_HINTS):
        return True
    return any(h in low for h in _JUNK_PATH_HINTS)


def classify_media_url(url: str, content_type: Optional[str] = None) -> Optional[str]:
    """Retorna 'image' | 'video' | 'hls' | 'dash' ou None se indeterminado."""
    if content_type:
        ct = content_type.lower().split(';', 1)[0].strip()
        if ct in ('application/vnd.apple.mpegurl', 'application/x-mpegurl', 'audio/x-mpegurl'):
            return 'hls'
        if ct == 'application/dash+xml':
            return 'dash'
        if ct.startswith('video/'):
            return 'video'
        if ct.startswith('image/'):
            return 'image'
    if _HLS_EXT_PATTERN.search(url):
        return 'hls'
    if _DASH_EXT_PATTERN.search(url):
        return 'dash'
    if _VIDEO_EXT_PATTERN.search(url):
        return 'video'
    if _IMAGE_EXT_PATTERN.search(url):
        return 'image'
    return None


_TWIMG_NAME_RE = re.compile(r"([?&])name=[^&]+")
_FBCDN_SIZE_RE = re.compile(r"_s\d{2,4}x\d{2,4}_", re.I)
_PINIMG_SIZE_RE = re.compile(r"/(\d+x|\d+x\d+)/")
_REDD_PREVIEW_PARAMS = ('width', 'height', 'crop', 'auto', 's', 'format')


def rewrite_to_max_resolution(url: str) -> str:
    """Reescreve URL pra resolução máxima quando o host é uma CDN conhecida."""
    try:
        parsed = urlparse(url)
    except Exception:
        return url
    host = (parsed.netloc or '').lower()

    if 'pbs.twimg.com' in host:
        if 'name=' in (parsed.query or ''):
            new_q = _TWIMG_NAME_RE.sub(r"\1name=orig", parsed.query)
        else:
            sep = '&' if parsed.query else ''
            new_q = (parsed.query or '') + f"{sep}name=orig"
        return urlunparse(parsed._replace(query=new_q))

    if host.endswith('fbcdn.net') or 'cdninstagram' in host:
        path = _FBCDN_SIZE_RE.sub('_', parsed.path)
        return urlunparse(parsed._replace(path=path))

    if host.endswith('pinimg.com'):
        path = _PINIMG_SIZE_RE.sub('/originals/', parsed.path)
        return urlunparse(parsed._replace(path=path))

    if 'redd.it' in host or host.endswith('reddit.com') or 'redditmedia.com' in host:
        if parsed.query:
            qs = parse_qs(parsed.query)
            qs = {k: v for k, v in qs.items() if k.lower() not in _REDD_PREVIEW_PARAMS}
            new_q = '&'.join(f"{k}={v[0]}" for k, v in qs.items())
            return urlunparse(parsed._replace(query=new_q))

    return url


_DEDUPE_ID_RES = (
    re.compile(r"/([a-f0-9]{16,64})(?:[._/-]|$)", re.I),
    re.compile(r"/([A-Za-z0-9_-]{11,})\.(?:jpg|jpeg|png|webp|mp4|m4v|webm)", re.I),
)


def dedupe_key(url: str) -> str:
    """Chave de dedupe estável: path sem query, ou 'asset id' extraído quando possível.

    Resolve o caso de mesma mídia entregue em CDNs/resoluções/queries diferentes.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return url
    path = parsed.path or url
    for pat in _DEDUPE_ID_RES:
        m = pat.search(path)
        if m:
            return m.group(1).lower()
    return path.lower()


_META_RE = re.compile(
    r"<meta\s+[^>]*?(?:property|name)\s*=\s*['\"]([^'\"]+)['\"][^>]*?content\s*=\s*['\"]([^'\"]+)['\"]",
    re.I | re.S,
)
_META_RE_REV = re.compile(
    r"<meta\s+[^>]*?content\s*=\s*['\"]([^'\"]+)['\"][^>]*?(?:property|name)\s*=\s*['\"]([^'\"]+)['\"]",
    re.I | re.S,
)
_VIDEO_META_PROPS = (
    'og:video', 'og:video:url', 'og:video:secure_url',
    'twitter:player:stream', 'twitter:player', 'og:video:tag',
)
_IMAGE_META_PROPS = (
    'og:image', 'og:image:secure_url', 'og:image:url',
    'twitter:image', 'twitter:image:src',
)


def extract_meta_media(html: str, base_url: str = "") -> list[MediaTuple]:
    out: list[MediaTuple] = []
    seen: set[str] = set()
    for m in _META_RE.finditer(html):
        prop = m.group(1).lower()
        content = _html.unescape(m.group(2))
        _classify_meta(prop, content, base_url, out, seen)
    for m in _META_RE_REV.finditer(html):
        content = _html.unescape(m.group(1))
        prop = m.group(2).lower()
        _classify_meta(prop, content, base_url, out, seen)
    return out


def _classify_meta(prop: str, content: str, base_url: str, out: list, seen: set) -> None:
    if not content or content in seen:
        return
    if prop in _VIDEO_META_PROPS:
        url = _absolute(content, base_url)
        seen.add(content)
        kind = classify_media_url(url) or 'video'
        out.append((kind if kind in ('video', 'hls', 'dash') else 'video', url))
    elif prop in _IMAGE_META_PROPS:
        url = _absolute(content, base_url)
        seen.add(content)
        out.append(('image', url))


def _absolute(url: str, base_url: str) -> str:
    if not base_url or url.startswith(('http://', 'https://', '//')):
        return url if not url.startswith('//') else 'https:' + url
    try:
        return urljoin(base_url, url)
    except Exception:
        return url


_JSONLD_RE = re.compile(
    r"<script[^>]*?type\s*=\s*['\"]application/ld\+json['\"][^>]*>(.*?)</script>",
    re.I | re.S,
)


def extract_jsonld_media(html: str, base_url: str = "") -> list[MediaTuple]:
    out: list[MediaTuple] = []
    for m in _JSONLD_RE.finditer(html):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            try:
                data = json.loads(raw.replace('\n', ' '))
            except json.JSONDecodeError:
                continue
        _walk_jsonld(data, base_url, out)
    seen: set[str] = set()
    deduped: list[MediaTuple] = []
    for kind, url in out:
        if url in seen:
            continue
        seen.add(url)
        deduped.append((kind, url))
    return deduped


_VIDEO_TYPES = ('VideoObject', 'Movie', 'TVEpisode', 'Clip')
_IMAGE_TYPES = ('ImageObject', 'Photograph')


def _walk_jsonld(node, base_url: str, out: list[MediaTuple]) -> None:
    if isinstance(node, dict):
        types_raw = node.get('@type') or node.get('type')
        types = [types_raw] if isinstance(types_raw, str) else (types_raw or [])

        for vt in _VIDEO_TYPES:
            if vt in types:
                for k in ('contentUrl', 'embedUrl', 'url'):
                    v = node.get(k)
                    if isinstance(v, str):
                        out.append(('video', _absolute(v, base_url)))
        for it in _IMAGE_TYPES:
            if it in types:
                for k in ('contentUrl', 'url'):
                    v = node.get(k)
                    if isinstance(v, str):
                        out.append(('image', _absolute(v, base_url)))

        thumb = node.get('thumbnailUrl') or node.get('thumbnail')
        if isinstance(thumb, str):
            out.append(('image', _absolute(thumb, base_url)))
        elif isinstance(thumb, list):
            for t in thumb:
                if isinstance(t, str):
                    out.append(('image', _absolute(t, base_url)))

        img = node.get('image')
        if isinstance(img, str):
            out.append(('image', _absolute(img, base_url)))
        elif isinstance(img, list):
            for t in img:
                if isinstance(t, str):
                    out.append(('image', _absolute(t, base_url)))

        for v in node.values():
            _walk_jsonld(v, base_url, out)
    elif isinstance(node, list):
        for v in node:
            _walk_jsonld(v, base_url, out)


_PLAYER_PATTERNS = (
    re.compile(r"""['"]?file['"]?\s*:\s*['"]([^'"]+\.(?:mp4|m3u8|mpd|webm))['"]""", re.I),
    re.compile(r"""['"]?src['"]?\s*:\s*['"]([^'"]+\.(?:mp4|m3u8|mpd|webm))['"]""", re.I),
    re.compile(r"""['"]?source['"]?\s*:\s*['"]([^'"]+\.(?:mp4|m3u8|mpd|webm))['"]""", re.I),
    re.compile(r"""['"]?contentUrl['"]?\s*:\s*['"]([^'"]+\.(?:mp4|m3u8|mpd|webm))['"]""", re.I),
    re.compile(r"""hlsManifestUrl['"]?\s*:\s*['"]([^'"]+\.m3u8[^'"]*)['"]""", re.I),
    re.compile(r"""dashManifestUrl['"]?\s*:\s*['"]([^'"]+\.mpd[^'"]*)['"]""", re.I),
)


def extract_player_configs(html: str, base_url: str = "") -> list[MediaTuple]:
    out: list[MediaTuple] = []
    seen: set[str] = set()
    for pat in _PLAYER_PATTERNS:
        for m in pat.finditer(html):
            raw = _html.unescape(m.group(1))
            url = _absolute(raw, base_url)
            if url in seen or is_junk_url(url):
                continue
            seen.add(url)
            kind = classify_media_url(url) or 'video'
            out.append((kind, url))
    return out


_IFRAME_RE = re.compile(r"<iframe[^>]+?src\s*=\s*['\"]([^'\"]+)['\"]", re.I)
_EMBED_HOST_HINTS = (
    'youtube.com/embed', 'youtube-nocookie.com/embed', 'youtu.be/',
    'player.vimeo.com', 'vimeo.com/video',
    'streamable.com/e/', 'streamable.com/o/',
    'dailymotion.com/embed',
    'player.twitch.tv', 'clips.twitch.tv/embed',
    'tiktok.com/embed',
)


def extract_iframes(html: str, base_url: str = "") -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in _IFRAME_RE.finditer(html):
        raw = _html.unescape(m.group(1))
        url = _absolute(raw, base_url)
        if not url.startswith(('http://', 'https://')):
            continue
        if not any(h in url for h in _EMBED_HOST_HINTS):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def merge_media_lists(*lists: Iterable[MediaTuple], cap: Optional[int] = None) -> list[MediaTuple]:
    """Concatena, deduplica por dedupe_key (preservando ordem), opcionalmente limitando."""
    out: list[MediaTuple] = []
    seen: set[str] = set()
    for lst in lists:
        for kind, url in lst:
            key = dedupe_key(url)
            if key in seen:
                continue
            seen.add(key)
            out.append((kind, url))
            if cap and len(out) >= cap:
                return out
    return out


_ARTICLE_LIST_ITEM_RE = re.compile(r"^(?:[-*\u2022\u2023\u25aa]|\d+[.)])\s+")


def _format_article_body(text: str) -> str:
    """Transforma os blocos semanticos do extrator em paragrafos legiveis.

    O Trafilatura devolve cada paragrafo, titulo ou item em uma linha, mas sem
    linhas vazias. No Telegram isso deixa toda a materia visualmente colada.
    Itens consecutivos de uma lista continuam juntos; os demais blocos recebem
    uma linha vazia entre si.
    """
    if not text:
        return ""

    lines = [
        re.sub(r"[ \t]+", " ", line).strip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    lines = [line for line in lines if line]
    if not lines:
        return ""

    formatted = [lines[0]]
    previous_is_list_item = bool(_ARTICLE_LIST_ITEM_RE.match(lines[0]))
    for line in lines[1:]:
        is_list_item = bool(_ARTICLE_LIST_ITEM_RE.match(line))
        separator = "\n" if is_list_item and previous_is_list_item else "\n\n"
        formatted.extend((separator, line))
        previous_is_list_item = is_list_item
    return "".join(formatted)


def extract_article(
    html: str,
    url: str = "",
    min_chars: int = 300,
) -> Optional[tuple[str, str]]:
    if not html:
        return None
    try:
        import trafilatura
    except ImportError:
        logger.debug(lmsg("_scrape_helpers.trafilatura_n_o_instalado"))
        return None
    try:
        body = trafilatura.extract(
            html,
            url=url or None,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
            favor_precision=True,
        )
    except Exception as e:
        logger.debug(lmsg("_scrape_helpers.trafilatura_extract_falhou", e=e))
        return None
    if not body or len(body.strip()) < min_chars:
        return None
    title = ""
    try:
        meta = trafilatura.extract_metadata(html)
        if meta and meta.title:
            title = meta.title.strip()
    except Exception:
        pass
    return title, _format_article_body(body)
