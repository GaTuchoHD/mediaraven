import hashlib
import json
import logging
import os
import sqlite3
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from config import MEDIA_CACHE_ENABLED

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("MEDIA_CACHE_DB_PATH", os.path.join(os.path.dirname(__file__), "media_cache.db"))


def _get_db():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_cache_db():
    if not MEDIA_CACHE_ENABLED:
        logger.info("⚡ Cache de mídia desativado (MEDIA_CACHE_ENABLED=no).")
        return
    try:
        with _get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS media_cache (
                    url_hash TEXT PRIMARY KEY,
                    original_url TEXT,
                    media_type TEXT,
                    file_ids TEXT,
                    caption_short TEXT,
                    caption_full TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        logger.info("⚡ Banco de dados de Cache de Mídia (SQLite WAL) inicializado com sucesso.")
    except Exception as e:
        logger.error(f"Erro ao inicializar media_cache.db: {e}")


def hash_url(url: str) -> str:
    # Keep semantic query parameters (notably YouTube's `v`). Removing the
    # whole query made every /watch URL share one cache entry.
    tracking = {
        "fbclid", "gclid", "igsh", "igshid", "si", "source", "feature",
        "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term",
    }
    parsed = urlsplit(url.strip())
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in tracking
    )
    clean = urlunsplit((
        parsed.scheme.lower(), parsed.netloc.lower(), parsed.path,
        urlencode(query, doseq=True), "",
    ))
    return hashlib.sha256(clean.encode('utf-8')).hexdigest()


def get_cached_media(url: str) -> Optional[dict[str, Any]]:
    """Retrieve cached file_ids for instant 0.1s delivery if available (< 7 days old)."""
    if not MEDIA_CACHE_ENABLED:
        return None
    u_hash = hash_url(url)
    try:
        with _get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT media_type, file_ids, caption_short, caption_full
                FROM media_cache
                WHERE url_hash = ? AND datetime(created_at, '+7 days') > datetime('now')
                """,
                (u_hash,)
            )
            row = cur.fetchone()
            if row:
                return {
                    "media_type": row[0],
                    "file_ids": json.loads(row[1]),
                    "caption_short": row[2],
                    "caption_full": row[3],
                }
    except Exception as e:
        logger.warning(f"Erro ao ler media_cache: {e}")
    return None


def save_cached_media(url: str, media_type: str, file_ids: list[str], caption_short: str = "", caption_full: str = ""):
    """Save Telegram file_ids into SQLite cache for instant reuse."""
    if not MEDIA_CACHE_ENABLED or not file_ids:
        return
    u_hash = hash_url(url)
    try:
        with _get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO media_cache (url_hash, original_url, media_type, file_ids, caption_short, caption_full)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (u_hash, "", media_type, json.dumps(file_ids), caption_short, caption_full)
            )
            logger.info("⚡ Mídia em cache salva (%s arquivo(s), chave=%s)", len(file_ids), u_hash[:12])
    except Exception as e:
        logger.warning(f"Erro ao salvar media_cache: {e}")


def delete_cached_media(url: str) -> None:
    """Remove a stale Telegram file_id entry after Telegram rejects it."""
    if not MEDIA_CACHE_ENABLED:
        return
    u_hash = hash_url(url)
    try:
        with _get_db() as conn:
            conn.execute("DELETE FROM media_cache WHERE url_hash = ?", (u_hash,))
    except Exception as e:
        logger.warning("Erro ao remover entrada do media_cache: %s", e)


def cleanup_and_vacuum_cache():
    """Delete expired entries and VACUUM only when rows were removed."""
    if not MEDIA_CACHE_ENABLED:
        return
    try:
        with _get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM media_cache WHERE datetime(created_at, '+7 days') < datetime('now')")
            deleted_count = cur.rowcount
            conn.commit()
            if deleted_count > 0:
                conn.execute("VACUUM")
                logger.info(f"🧹 Manutenção SQLite: {deleted_count} registros antigos removidos (VACUUM executado).")
    except Exception as e:
        logger.warning(f"Erro na manutenção do cache SQLite: {e}")
