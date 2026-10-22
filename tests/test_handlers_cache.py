from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import handlers
from downloaders._caption import CAPTION_MAX


def test_cached_caption_payload_replays_long_text_separately():
    full = "parágrafo\n\n" * 500

    caption, separate_text = handlers._cached_caption_payload({
        "caption_short": "resumo",
        "caption_full": full,
    })

    assert caption is None
    assert separate_text == full


def test_cached_caption_payload_keeps_short_caption_on_media():
    caption, separate_text = handlers._cached_caption_payload({
        "caption_short": "texto curto",
        "caption_full": "texto completo curto",
    })

    assert caption == "texto curto"
    assert separate_text == ""


@pytest.mark.asyncio
async def test_long_article_cache_saves_short_and_full_text(monkeypatch, tmp_path):
    saved = {}
    sent = {}
    full = "x" * (CAPTION_MAX + 50)

    async def fake_send_downloaded_media(*args, caption=None, **kwargs):
        sent["caption"] = caption
        return ["telegram-file-id"]

    def fake_save_cached_media(*args):
        saved["args"] = args

    monkeypatch.setattr(handlers, "should_show_prompt", lambda *args: False)
    monkeypatch.setattr(
        handlers,
        "cfg",
        lambda key: "yes" if key in {"ASK_ARTICLE_DEFAULT", "ASK_CAPTION_DEFAULT"} else 0,
    )
    monkeypatch.setattr(handlers, "_safe_edit", AsyncMock())
    monkeypatch.setattr(handlers, "_safe_delete", AsyncMock())
    monkeypatch.setattr(handlers, "_send_text_in_chunks", AsyncMock())
    monkeypatch.setattr(handlers, "send_downloaded_media", fake_send_downloaded_media)
    monkeypatch.setattr(handlers, "save_cached_media", fake_save_cached_media)
    monkeypatch.setattr(handlers, "infer_media_type", lambda path: "photo")

    media_file = tmp_path / "article.jpg"
    media_file.write_bytes(b"image")

    await handlers._send_files_and_cleanup_status(
        SimpleNamespace(),
        123,
        456,
        SimpleNamespace(),
        [str(media_file)],
        "resumo",
        full,
        "",
        394625529,
        1,
        is_article=True,
        cache_url="https://example.com/article",
    )

    assert sent["caption"] is None
    assert saved["args"][3] == "resumo"
    assert saved["args"][4] == full
    handlers._send_text_in_chunks.assert_awaited_once()
