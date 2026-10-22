import sqlite3

import media_cache
from media_cache import hash_url


def test_hash_url_keeps_semantic_query_parameters():
    assert hash_url("https://youtube.com/watch?v=AAA") != hash_url(
        "https://youtube.com/watch?v=BBB"
    )


def test_hash_url_ignores_tracking_parameters():
    plain = hash_url("https://example.com/video?id=42")
    tracked = hash_url(
        "https://example.com/video?utm_source=telegram&id=42&fbclid=abc"
    )
    assert tracked == plain


def test_hash_url_normalizes_query_order():
    assert hash_url("https://example.com/watch?b=2&a=1") == hash_url(
        "https://example.com/watch?a=1&b=2"
    )


def test_cache_does_not_store_raw_url(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(media_cache, "DB_PATH", str(db_path))
    monkeypatch.setattr(media_cache, "MEDIA_CACHE_ENABLED", True)
    media_cache.init_cache_db()
    media_cache.save_cached_media(
        "https://example.com/private?id=secret",
        "video",
        ["telegram-file-id"],
    )

    with sqlite3.connect(db_path) as conn:
        original_url = conn.execute(
            "SELECT original_url FROM media_cache"
        ).fetchone()[0]

    assert original_url == ""


def test_disabled_cache_does_not_create_database(tmp_path, monkeypatch):
    db_path = tmp_path / "cache.db"
    monkeypatch.setattr(media_cache, "DB_PATH", str(db_path))
    monkeypatch.setattr(media_cache, "MEDIA_CACHE_ENABLED", False)

    media_cache.init_cache_db()
    media_cache.save_cached_media(
        "https://example.com/video",
        "video",
        ["telegram-file-id"],
    )

    assert media_cache.get_cached_media("https://example.com/video") is None
    assert not db_path.exists()
