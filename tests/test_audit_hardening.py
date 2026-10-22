"""Regression tests for the audited security and stability improvements.

No external Telegram, social-media or internal-network traffic is used.
"""
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import handlers
import metrics
import state
import utils
from lifecycle import periodic_cleanup
from url_safety import UnsafeURLError, validate_url_sync


def test_admin_does_not_inherit_group_or_download_permissions(monkeypatch):
    monkeypatch.setattr(handlers, "ALLOWED_CHAT_ID", [-1001234])
    monkeypatch.setattr(handlers, "ALLOWED_USER_ID", [20])
    monkeypatch.setattr(handlers, "ALLOWED_USERNAMES", frozenset())
    monkeypatch.setattr(handlers, "ADMIN_USER_ID", [10])
    monkeypatch.setattr(handlers, "ALLOW_ALL", "no")
    ordinary = SimpleNamespace(
        effective_chat=SimpleNamespace(id=-1001234),
        effective_user=SimpleNamespace(id=20, username=None),
    )
    admin = SimpleNamespace(
        effective_chat=SimpleNamespace(id=12345),
        effective_user=SimpleNamespace(id=10, username=None),
    )
    assert handlers._is_authorized_update(ordinary)
    assert not handlers._is_admin_update(ordinary)
    assert handlers._is_admin_update(admin)


def test_admin_does_not_inherit_allow_all(monkeypatch):
    monkeypatch.setattr(handlers, "ALLOW_ALL", "yes")
    monkeypatch.setattr(handlers, "ADMIN_USER_ID", [])
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=54321),
        effective_user=SimpleNamespace(id=20, username=None),
    )
    assert handlers._is_authorized_update(update)
    assert not handlers._is_admin_update(update)


def test_language_variants_produce_distinct_cache_keys():
    url = "https://example.com/watch?v=123#description"
    key_pt = handlers._cache_key_for_language(url, "pt")
    key_en = handlers._cache_key_for_language(url, "en")
    assert key_pt != key_en
    assert key_pt != url
    assert handlers._cache_key_for_language(url, None) == url
    assert "v=123" in key_pt


def test_bounded_pending_queue(monkeypatch):
    monkeypatch.setattr(state, "MAX_PENDING_REQUESTS", 1)
    monkeypatch.setattr(state, "_pending_requests", 0)
    assert state.try_request_queued() is True
    assert state.try_request_queued() is False
    assert state.request_stats()["pending"] == 1
    state.request_cancelled_while_pending()
    assert state.request_stats()["pending"] == 0


@pytest.mark.asyncio
async def test_pending_reservation_expires_without_consuming_active_slot(monkeypatch):
    import asyncio

    monkeypatch.setattr(state, "MAX_PENDING_REQUESTS", 10)
    monkeypatch.setattr(state, "_pending_requests", 0)
    monkeypatch.setattr(state, "_active_requests", 0)
    monkeypatch.setattr(state, "REQUEST_SEMAPHORE", asyncio.Semaphore(0))
    monkeypatch.setattr(handlers, "MAX_QUEUE_WAIT_SECONDS", 0.01)
    ctx = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

    await handlers._bounded_media_request(ctx, 12345, 2, "https://example.com/video")

    assert state.request_stats()["pending"] == 0
    assert state.request_stats()["active"] == 0


def test_metrics_writes_serially_in_original_event_order(monkeypatch):
    writes = []
    monkeypatch.setattr(metrics, "_persist_platform", lambda platform, stats: writes.append((platform, stats.success)))
    name = "audit_parallel_probe"
    metrics._metrics.platforms.pop(name, None)
    metrics.record_success(name, 1.0)
    metrics.record_success(name, 1.0)
    assert writes == [(name, 1), (name, 2)]
    metrics._metrics.platforms.pop(name, None)


def test_cleanup_never_deletes_generic_external_tmp_items(tmp_path, monkeypatch):
    own = tmp_path / "managed"
    other = tmp_path / "test_other_program_data"
    own.mkdir()
    other.mkdir()
    (other / "keep.txt").write_text("not ours", encoding="utf-8")
    old_time = time.time() - 7200
    os.utime(other, (old_time, old_time))
    monkeypatch.setattr(periodic_cleanup, "BASE_DOWNLOAD_DIR", str(own))
    monkeypatch.setattr(periodic_cleanup, "cleanup_and_vacuum_cache", lambda: None)
    periodic_cleanup._clean_old_temp_folders()
    assert (other / "keep.txt").read_text(encoding="utf-8") == "not ours"


def test_private_destination_urls_rejected():
    with pytest.raises(UnsafeURLError):
        validate_url_sync("http://127.0.0.1/private")
    with pytest.raises(UnsafeURLError):
        validate_url_sync("http://169.254.169.254/latest/meta-data/")


@pytest.mark.asyncio
async def test_plain_http_downloader_blocks_private_destination(tmp_path):
    target = tmp_path / "download.bin"
    result = await utils.async_download_file("http://127.0.0.1/private", str(target))
    assert result is False
    assert not target.exists()


@pytest.mark.asyncio
async def test_ffmpeg_remote_source_blocks_private_destination(tmp_path):
    target = tmp_path / "stream.mp4"
    result = await utils.async_ffmpeg_remux("http://127.0.0.1/stream.m3u8", str(target))
    assert result is False
    assert not target.exists()


@pytest.mark.asyncio
async def test_playwright_authenticated_fallback_uses_streaming(monkeypatch, tmp_path):
    class FakeContext:
        async def cookies(self, urls):
            assert urls == ["https://example.com/image"]
            return [{"name": "sessionid", "value": "dummy-test-only"}]

    monkeypatch.setattr(state, "PW_CONTEXT", FakeContext())
    download = AsyncMock(return_value=True)
    monkeypatch.setattr(utils, "async_download_file", download)
    target = tmp_path / "test.jpg"
    assert await utils.async_download_via_playwright("https://example.com/image", str(target)) is True
    assert download.await_args.kwargs["cookies"] == {"sessionid": "dummy-test-only"}
