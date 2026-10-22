import logging
import os
import re
import shutil
import sqlite3
import tempfile
import time
from urllib.parse import urlparse

import state
from config import FIREFOX_PROFILE_PATH
from messages import lmsg

logger = logging.getLogger(__name__)

_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_X_COOKIE_HOSTS = ('.x.com', 'x.com', '.twitter.com', 'twitter.com')
_X_REQUIRED_COOKIES = frozenset({'auth_token', 'ct0'})
_X_ALLOWED_COOKIES = frozenset({
    'auth_token',
    'ct0',
    'guest_id',
    'guest_id_ads',
    'guest_id_marketing',
    'kdt',
    'lang',
    'personalization_id',
    'twid',
})
_INSTAGRAM_COOKIE_HOSTS = ('.instagram.com', 'instagram.com')
_INSTAGRAM_REQUIRED_COOKIES = frozenset({
    'sessionid',
    'csrftoken',
    'ds_user_id',
})
_INSTAGRAM_ALLOWED_COOKIES = frozenset({
    'sessionid',
    'csrftoken',
    'ds_user_id',
    'datr',
    'dpr',
    'ig_did',
    'ig_direct_region_hint',
    'ig_nrcb',
    'mid',
    'ps_l',
    'ps_n',
    'rur',
    'shbid',
    'shbts',
    'wd',
})


def _parse_raw_cookie_header(raw_header: str) -> dict[str, str]:
    if not raw_header or len(raw_header) > 16_000:
        raise ValueError("Cookie vazio ou maior que 16 KB.")

    parsed: dict[str, str] = {}
    for part in raw_header.split(';'):
        piece = part.strip()
        if not piece:
            continue
        if '=' not in piece:
            raise ValueError("Formato inválido: use nome=valor separado por ponto e vírgula.")
        name, value = piece.split('=', 1)
        name = name.strip()
        value = value.strip()
        if not _COOKIE_NAME_RE.fullmatch(name):
            raise ValueError("O cookie contém um nome inválido.")
        if any(char in value for char in ('\r', '\n', '\x00')):
            raise ValueError("O cookie contém caracteres de controle inválidos.")
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        parsed[name] = value

    if not parsed:
        raise ValueError("Nenhum cookie válido foi encontrado.")
    if len(parsed) > 100:
        raise ValueError("Quantidade de cookies acima do limite de 100.")
    return parsed


def install_raw_cookies(platform: str, raw_header: str) -> dict[str, int | str]:
    """Install an allowlisted raw cookie header without logging secret values."""
    normalized = (platform or '').strip().casefold()
    if normalized in {'x', 'twitter'}:
        platform_name = 'X/Twitter'
        allowed_cookies = _X_ALLOWED_COOKIES
        required_cookies = _X_REQUIRED_COOKIES
        cookie_hosts = _X_COOKIE_HOSTS
        target_host = '.x.com'
        http_only_names = frozenset({'auth_token'})
        missing_message = "O cookie do X precisa conter auth_token e ct0."
    elif normalized in {'instagram', 'ig'}:
        platform_name = 'Instagram'
        allowed_cookies = _INSTAGRAM_ALLOWED_COOKIES
        required_cookies = _INSTAGRAM_REQUIRED_COOKIES
        cookie_hosts = _INSTAGRAM_COOKIE_HOSTS
        target_host = '.instagram.com'
        http_only_names = frozenset({'sessionid', 'datr'})
        missing_message = (
            "O cookie do Instagram precisa conter sessionid, csrftoken e ds_user_id."
        )
    else:
        raise ValueError("Plataforma não suportada. Use: x ou instagram")

    parsed = {
        name: value
        for name, value in _parse_raw_cookie_header(raw_header).items()
        if name in allowed_cookies
    }
    missing = {name for name in required_cookies if not parsed.get(name)}
    if missing:
        raise ValueError(missing_message)

    db_path = os.path.join(FIREFOX_PROFILE_PATH, 'cookies.sqlite')
    if not os.path.isfile(db_path):
        raise FileNotFoundError("Banco Firefox não encontrado; execute /cookies_status.")

    conn = sqlite3.connect(db_path, timeout=10)
    try:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(moz_cookies)").fetchall()
        }
        required_columns = {
            'name', 'value', 'host', 'path', 'expiry', 'isSecure', 'isHttpOnly'
        }
        if not required_columns.issubset(columns):
            raise RuntimeError("Schema moz_cookies incompatível.")

        insert_columns = [
            'name', 'value', 'host', 'path', 'expiry', 'isSecure', 'isHttpOnly'
        ]
        if 'originAttributes' in columns:
            insert_columns.insert(0, 'originAttributes')

        conn.execute('BEGIN IMMEDIATE')
        conn.execute(
            "DELETE FROM moz_cookies WHERE lower(host) IN "
            f"({', '.join('?' for _ in cookie_hosts)})",
            cookie_hosts,
        )
        placeholders = ', '.join('?' for _ in insert_columns)
        statement = (
            f"INSERT INTO moz_cookies ({', '.join(insert_columns)}) "
            f"VALUES ({placeholders})"
        )
        for name, value in parsed.items():
            values: list[str | int] = [
                name,
                value,
                target_host,
                '/',
                0,
                1,
                1 if name in http_only_names else 0,
            ]
            if 'originAttributes' in columns:
                values.insert(0, '')
            conn.execute(statement, values)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    os.chmod(db_path, 0o600)
    return {'platform': platform_name, 'installed': len(parsed)}


def get_firefox_cookie_value(name: str, domains: tuple[str, ...]) -> str | None:
    """Read one cookie for internal authentication without logging its value."""
    db_path = os.path.join(FIREFOX_PROFILE_PATH, 'cookies.sqlite')
    if not os.path.isfile(db_path) or not name or not domains:
        return None

    normalized_domains = tuple(domain.casefold().lstrip('.') for domain in domains)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
    try:
        rows = conn.execute(
            "SELECT value, host FROM moz_cookies WHERE name = ?",
            (name,),
        ).fetchall()
    finally:
        conn.close()

    for value, host in rows:
        normalized_host = str(host or '').casefold().lstrip('.')
        if normalized_host in normalized_domains and value:
            return str(value)
    return None


def get_firefox_cookie_status(now: float | None = None) -> dict:
    """Return cookie health metadata without reading or exposing cookie values."""
    db_path = os.path.join(FIREFOX_PROFILE_PATH, 'cookies.sqlite')
    result = {
        'path': db_path,
        'exists': os.path.exists(db_path),
        'readable': False,
        'size_bytes': 0,
        'modified_at': None,
        'total': 0,
        'active': 0,
        'expired': 0,
        'session': 0,
        'domains': 0,
        'platforms': {
            'YouTube/Google': 0,
            'Instagram': 0,
            'Facebook': 0,
            'Reddit': 0,
            'TikTok': 0,
            'X/Twitter': 0,
        },
        'readiness': {},
        'expiring_soon': 0,
        'error': None,
    }
    if not result['exists']:
        return result

    try:
        stat = os.stat(db_path)
        result['size_bytes'] = stat.st_size
        result['modified_at'] = stat.st_mtime

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
        try:
            rows = conn.execute("SELECT name, host, expiry FROM moz_cookies").fetchall()
        finally:
            conn.close()

        timestamp = int(now if now is not None else time.time())
        unique_domains: set[str] = set()
        active_names: dict[str, set[str]] = {
            'YouTube/Google': set(),
            'Instagram': set(),
            'X/Twitter': set(),
        }
        for name, host, expiry in rows:
            domain = str(host or '').lower().lstrip('.')
            if domain:
                unique_domains.add(domain)

            try:
                expiry_value = int(float(expiry or 0))
            except (TypeError, ValueError):
                expiry_value = 0

            if expiry_value <= 0:
                result['session'] += 1
                result['active'] += 1
                is_active = True
            elif expiry_value > timestamp:
                result['active'] += 1
                is_active = True
                if expiry_value - timestamp <= 7 * 86400:
                    result['expiring_soon'] += 1
            else:
                result['expired'] += 1
                is_active = False

            if domain.endswith(('youtube.com', 'google.com', 'googlevideo.com')):
                platform = 'YouTube/Google'
            elif domain.endswith('instagram.com'):
                platform = 'Instagram'
            elif domain.endswith('facebook.com'):
                platform = 'Facebook'
            elif domain.endswith('reddit.com'):
                platform = 'Reddit'
            elif domain.endswith('tiktok.com'):
                platform = 'TikTok'
            elif domain.endswith(('x.com', 'twitter.com')):
                platform = 'X/Twitter'
            else:
                platform = None

            if platform:
                result['platforms'][platform] += 1
                if is_active and platform in active_names:
                    active_names[platform].add(str(name or '').casefold())

        required = {
            'YouTube/Google': {'sid', 'sapisid'},
            'Instagram': {'sessionid', 'csrftoken', 'ds_user_id'},
            'X/Twitter': {'auth_token', 'ct0'},
        }
        result['readiness'] = {
            platform: required_names.issubset(active_names[platform])
            for platform, required_names in required.items()
        }

        result['total'] = len(rows)
        result['domains'] = len(unique_domains)
        result['readable'] = True
    except Exception as e:
        # Only the exception class is returned to Telegram. Database paths and
        # cookie values must never leak through this diagnostic command.
        result['error'] = type(e).__name__
        logger.warning("Falha ao validar banco de cookies do Firefox: %s", type(e).__name__)

    return result


def get_reddit_cookie_status(now: float | None = None) -> dict:
    """Return counts for the dedicated Netscape cookie file, never values."""
    path = os.path.join(os.path.dirname(__file__), "reddit_cookies.txt")
    result = {
        "path": path,
        "exists": os.path.exists(path),
        "readable": False,
        "total": 0,
        "active": 0,
        "expired": 0,
        "error": None,
    }
    if not result["exists"]:
        return result

    timestamp = int(now if now is not None else time.time())
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as cookie_file:
            for raw_line in cookie_file:
                line = raw_line.strip()
                if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
                    continue
                fields = line.split("\t")
                if len(fields) < 7:
                    continue
                result["total"] += 1
                try:
                    expires = int(float(fields[4] or 0))
                except (TypeError, ValueError):
                    expires = 0
                if expires <= 0 or expires > timestamp:
                    result["active"] += 1
                else:
                    result["expired"] += 1
        result["readable"] = True
    except Exception as e:
        result["error"] = type(e).__name__
        logger.warning("Falha ao validar arquivo dedicado do Reddit: %s", type(e).__name__)
    return result


def extract_firefox_cookies() -> list[dict]:
    db_path = os.path.join(FIREFOX_PROFILE_PATH, 'cookies.sqlite')
    if not os.path.exists(db_path):
        logger.warning(lmsg("cookies.banco_de_cookies", db_path=db_path))
        return []

    fd, temp_db = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    shutil.copy2(db_path, temp_db)

    cookies = []
    MAX_PLAYWRIGHT_EXPIRY = 253402300799

    try:
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name, value, host, path, expiry, isSecure, isHttpOnly FROM moz_cookies")

        for row in cursor.fetchall():
            name, value, host, path, expiry, isSecure, isHttpOnly = row

            clean_expires = -1
            if expiry is not None:
                try:
                    val = int(float(expiry))
                    if val > MAX_PLAYWRIGHT_EXPIRY:
                        clean_expires = MAX_PLAYWRIGHT_EXPIRY
                    elif val > 0:
                        clean_expires = val
                except (ValueError, TypeError):
                    pass

            cookies.append({
                'name': name,
                'value': value,
                'domain': host,
                'path': path,
                'expires': clean_expires,
                'secure': bool(isSecure),
                'httpOnly': bool(isHttpOnly)
            })
        conn.close()
    except Exception as e:
        logger.error(lmsg("cookies.erro_ao_ler", e=e), exc_info=True)
    finally:
        if os.path.exists(temp_db):
            os.remove(temp_db)

    return cookies


def get_aiohttp_cookies_for_url(url: str) -> dict[str, str]:
    domain = urlparse(url).netloc.lower()
    return {c['name']: c['value'] for c in state.FIREFOX_COOKIES_CACHE if c['domain'].lstrip('.').lower() in domain}
