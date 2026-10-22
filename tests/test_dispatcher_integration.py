"""Testes de integração do download_media: protegem a assinatura e o fluxo de fallbacks.

Mocka yt-dlp e cada fallback para garantir que o orquestrador:
  - Tenta fallbacks quando yt-dlp retorna vazio
  - Passa os parâmetros corretos
  - Gera tupla (files, status, caption_short, caption_full, is_article) consistente
  - Trata MULTILANG corretamente
"""
import os
from unittest.mock import AsyncMock, patch

import pytest

from downloaders import dispatcher
from downloaders._platform import Platform


@pytest.fixture(autouse=True)
def stub_article_enrichment():
    with patch.object(
        dispatcher, "fetch_article_caption",
        new=AsyncMock(return_value=("", "")),
    ):
        yield


@pytest.fixture
def tmp_folder(tmp_path):
    folder = tmp_path / "task"
    folder.mkdir()
    return str(folder)


def _passthrough_async_mock():
    """AsyncMock que devolve o primeiro argumento recebido (para _resolve_short_reddit_url)."""
    async def passthrough(url):
        return url
    return AsyncMock(side_effect=passthrough)


def test_platform_label_includes_urlebird():
    assert dispatcher._platform_label(Platform(urlebird=True)) == "urlebird"


@pytest.mark.asyncio
async def test_download_media_urlebird_uses_direct_fast_path(tmp_folder):
    fake_file = os.path.join(tmp_folder, "urlebird.mp4")
    direct_mock = AsyncMock(return_value=(
        [fake_file], "STATUS_URLEBIRD", "@user caption", "@user caption",
    ))
    ytdlp_mock = AsyncMock(return_value=([], {}, None))

    with patch.object(dispatcher, "download_urlebird", new=direct_mock), \
         patch.object(dispatcher, "_run_ytdlp_with_cookie_fallback", new=ytdlp_mock):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://urlebird.com/video/example-7638961428594019604/",
            tmp_folder,
            target_lang=None,
        )

    assert files == [fake_file]
    assert status == "STATUS_URLEBIRD"
    assert short == "@user caption"
    assert full == "@user caption"
    assert is_article is False
    direct_mock.assert_awaited_once()
    ytdlp_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_media_ytdlp_success(tmp_folder):
    """yt-dlp baixa arquivo -> retorna (files, status_success, short, full)."""
    fake_file = os.path.join(tmp_folder, "video.mp4")
    with open(fake_file, "wb") as f:
        f.write(b"x")

    with patch.object(dispatcher, "_run_ytdlp_with_cookie_fallback",
                      new=AsyncMock(return_value=([fake_file], {'title': 'Teste', 'description': 'desc'}, None))), \
         patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "_detect_youtube_languages", new=AsyncMock(return_value=None)):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://example.com/video", tmp_folder, target_lang=None
        )

    assert files == [fake_file]
    assert "Teste" in short
    assert "Teste" in full
    assert status


@pytest.mark.asyncio
async def test_download_media_falls_back_to_scraper(tmp_folder):
    """yt-dlp vazio -> cai no scrape_fallback e retorna o resultado dele."""
    scraped_file = os.path.join(tmp_folder, "scraped.jpg")

    with patch.object(dispatcher, "_run_ytdlp_with_cookie_fallback",
                      new=AsyncMock(return_value=([], {}, None))), \
         patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "scrape_fallback",
                      new=AsyncMock(return_value=([scraped_file], "STATUS_SCRAPE", "", "", False))):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://example.com/page", tmp_folder, target_lang=None
        )

    assert files == [scraped_file]
    assert status == "STATUS_SCRAPE"
    assert short == ""
    assert full == ""


@pytest.mark.asyncio
async def test_download_media_scraper_returns_article_caption(tmp_folder):
    scraped_file = os.path.join(tmp_folder, "article_cover.jpg")
    article_short = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 6
    article_full = article_short * 4

    with patch.object(dispatcher, "_run_ytdlp_with_cookie_fallback",
                      new=AsyncMock(return_value=([], {}, None))), \
         patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "scrape_fallback",
                      new=AsyncMock(return_value=([scraped_file], "STATUS_SCRAPE", article_short, article_full, True))):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://news.example.com/article/123", tmp_folder, target_lang=None
        )

    assert files == [scraped_file]
    assert status == "STATUS_SCRAPE"
    assert short == article_short
    assert full == article_full
    assert is_article is True


@pytest.mark.asyncio
async def test_download_media_reddit_tries_json_first(tmp_folder):
    """Reddit: deve tentar reddit_json antes de reddit_playwright e scrape_fallback."""
    reddit_json_file = os.path.join(tmp_folder, "rj.jpg")
    call_order = []

    async def reddit_json_mock(url, folder):
        call_order.append("json")
        return [reddit_json_file], "OK_JSON", "OK_CAP", "OK_CAP_FULL"

    async def reddit_pw_mock(url, folder):
        call_order.append("pw")
        return [], "fail", "", ""

    async def ytdlp_mock(*a, **kw):
        call_order.append("ytdlp")
        return [], {}, None

    async def scrape_mock(url, folder):
        call_order.append("scrape")
        return [], "fail", "", "", False

    with patch.object(dispatcher, "_run_ytdlp_with_cookie_fallback", new=AsyncMock(side_effect=ytdlp_mock)), \
         patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "download_reddit_json", new=AsyncMock(side_effect=reddit_json_mock)), \
         patch.object(dispatcher, "download_reddit_playwright", new=AsyncMock(side_effect=reddit_pw_mock)), \
         patch.object(dispatcher, "scrape_fallback", new=AsyncMock(side_effect=scrape_mock)):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://reddit.com/r/foo/comments/x/", tmp_folder, target_lang=None
        )

    assert files == [reddit_json_file]
    assert status == "OK_JSON"
    assert call_order == ["json"]
    assert "ytdlp" not in call_order


@pytest.mark.asyncio
async def test_download_media_reddit_video_falls_back_to_ytdlp(tmp_folder):
    """Reddit: se reddit_json retorna vazio (vídeo), yt-dlp deve ser tentado."""
    video_file = os.path.join(tmp_folder, "v.mp4")
    with open(video_file, "wb") as f:
        f.write(b"x")
    call_order = []

    async def reddit_json_mock(url, folder):
        call_order.append("json")
        return [], dispatcher.REDDIT_JSON_VIDEO, "", ""

    async def ytdlp_mock(*a, **kw):
        call_order.append("ytdlp")
        return [video_file], {"title": "vídeo legal", "description": "selftext aqui"}, None

    with patch.object(dispatcher, "_run_ytdlp_with_cookie_fallback", new=AsyncMock(side_effect=ytdlp_mock)), \
         patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "download_reddit_json", new=AsyncMock(side_effect=reddit_json_mock)):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://reddit.com/r/foo/comments/y/", tmp_folder, target_lang=None
        )

    assert files == [video_file]
    assert call_order == ["json", "ytdlp"]
    assert "vídeo legal" in short


@pytest.mark.asyncio
async def test_removed_reddit_post_returns_user_message_without_fallback(tmp_folder):
    url = 'https://reddit.com/r/foo/comments/removed/'
    html = AsyncMock()
    ytdlp = AsyncMock()
    with (
        patch.object(dispatcher, '_resolve_short_reddit_url', AsyncMock(return_value=url)),
        patch.object(dispatcher, 'download_reddit_json', AsyncMock(
            return_value=([], dispatcher.REDDIT_UNAVAILABLE, '', ''))),
        patch.object(dispatcher, '_record_failure', AsyncMock()),
        patch.object(dispatcher, 'download_reddit_playwright', html),
        patch.object(dispatcher, '_run_ytdlp_with_cookie_fallback', ytdlp),
    ):
        files, status, short, full, _ = await dispatcher.download_media(url, tmp_folder)

    assert not files
    assert 'removido ou indisponível' in status
    assert not short
    assert 'removido ou indisponível' in full
    html.assert_not_awaited()
    ytdlp.assert_not_awaited()


@pytest.mark.asyncio
async def test_reddit_extraction_failure_returns_cautious_user_message(tmp_folder):
    url = 'https://reddit.com/r/foo/comments/unavailable/'
    record_failure = AsyncMock()
    record_success = AsyncMock()
    with (
        patch.object(dispatcher, '_resolve_short_reddit_url', AsyncMock(return_value=url)),
        patch.object(dispatcher, 'download_reddit_json', AsyncMock(
            return_value=([], 'json_fail', '', ''))),
        patch.object(dispatcher, '_record_failure', record_failure),
        patch.object(dispatcher, '_record_success', record_success),
        patch.object(dispatcher, 'download_reddit_playwright', AsyncMock(
            return_value=([], 'html_fail', '', ''))),
        patch.object(dispatcher, '_run_ytdlp_with_cookie_fallback', AsyncMock(
            return_value=([], {}, None))),
        patch.object(dispatcher, 'scrape_fallback', AsyncMock(
            return_value=([], '', '', '', False))),
    ):
        files, status, short, full, _ = await dispatcher.download_media(url, tmp_folder)

    assert not files
    assert 'Reddit' in status
    assert not short
    assert 'removido' in full.lower()
    assert 'temporariamente' in full.lower()
    record_failure.assert_awaited_once()
    record_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_reddit_playwright_warning_without_files_is_not_success(tmp_folder):
    url = 'https://reddit.com/r/foo/comments/video/'
    record_failure = AsyncMock()
    record_success = AsyncMock()
    with (
        patch.object(dispatcher, '_resolve_short_reddit_url', AsyncMock(return_value=url)),
        patch.object(dispatcher, 'download_reddit_json', AsyncMock(
            return_value=([], dispatcher.REDDIT_JSON_VIDEO, '', ''))),
        patch.object(dispatcher, '_record_failure', record_failure),
        patch.object(dispatcher, '_record_success', record_success),
        patch.object(dispatcher, 'download_reddit_playwright', AsyncMock(
            return_value=([], '⚠️ Falha ao extrair imagens do Reddit.', '', ''))),
        patch.object(dispatcher, '_run_ytdlp_with_cookie_fallback', AsyncMock(
            return_value=([], {}, None))),
        patch.object(dispatcher, 'scrape_fallback', AsyncMock(
            return_value=([], '', '', '', False))),
    ):
        files, status, _short, full, _ = await dispatcher.download_media(url, tmp_folder)

    assert not files
    assert 'Reddit' in status
    assert 'removido' in full.lower()
    record_failure.assert_awaited_once()
    record_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_media_reddit_json_failure_tries_html_before_ytdlp(tmp_folder):
    image_file = os.path.join(tmp_folder, "reddit.jpg")
    with open(image_file, "wb") as f:
        f.write(b"image")
    call_order = []

    async def reddit_json_mock(url, folder):
        call_order.append("json")
        return [], "json_fail", "", ""

    async def reddit_html_mock(url, folder):
        call_order.append("html")
        return [image_file], "OK_HTML", "CAP", "CAP_FULL"

    async def ytdlp_mock(*args, **kwargs):
        call_order.append("ytdlp")
        return [], {}, None

    with patch.object(dispatcher, "_run_ytdlp_with_cookie_fallback", new=AsyncMock(side_effect=ytdlp_mock)), \
         patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "download_reddit_json", new=AsyncMock(side_effect=reddit_json_mock)), \
         patch.object(dispatcher, "download_reddit_playwright", new=AsyncMock(side_effect=reddit_html_mock)):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://reddit.com/r/foo/comments/image/", tmp_folder, target_lang=None,
        )

    assert files == [image_file]
    assert status == "OK_HTML"
    assert call_order == ["json", "html"]


@pytest.mark.asyncio
async def test_download_media_threads_goes_straight_to_playwright(tmp_folder):
    """Threads pula yt-dlp e vai direto para o Playwright."""
    pw_file = os.path.join(tmp_folder, "threads.mp4")
    ytdlp_mock = AsyncMock(return_value=([], {}, None))

    with patch.object(dispatcher, "_run_ytdlp_with_cookie_fallback", new=ytdlp_mock), \
         patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "download_threads",
                      new=AsyncMock(return_value=([pw_file], "OK_THREADS", "", ""))):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://threads.net/@user/post/1", tmp_folder, target_lang=None
        )

    assert ytdlp_mock.await_count == 0
    assert files == [pw_file]
    assert status == "OK_THREADS"


@pytest.mark.asyncio
async def test_download_media_multilang(tmp_folder):
    """YouTube com múltiplos idiomas deve sinalizar MULTILANG em vez de baixar."""
    lang_buttons = [('original', 'ORIGINAL [EN]'), ('pt', 'PT')]

    with patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "_detect_youtube_languages", new=AsyncMock(return_value=lang_buttons)):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://youtube.com/watch?v=abc", tmp_folder, target_lang=None
        )

    assert status == "MULTILANG"
    assert files == lang_buttons


@pytest.mark.asyncio
async def test_download_media_skips_fallbacks_on_unrecoverable(tmp_folder):
    scrape_mock = AsyncMock(return_value=([], "fail", "", "", False))
    with patch.object(dispatcher, "_run_ytdlp_with_cookie_fallback",
                      new=AsyncMock(return_value=([], {}, "private"))), \
         patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "scrape_fallback", new=scrape_mock):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://youtube.com/watch?v=privado", tmp_folder, target_lang=None
        )

    assert files == []
    assert scrape_mock.await_count == 0
    assert "privado" in status.lower() or "private" in status.lower()


@pytest.mark.asyncio
async def test_download_media_prefers_authenticated_instagrapi_before_ytdlp(
    tmp_folder, monkeypatch,
):
    ig_file = os.path.join(tmp_folder, "reel.mp4")
    with open(ig_file, "wb") as f:
        f.write(b"x")

    instagrapi_mock = AsyncMock(return_value=([ig_file], "IG_STATUS", "leg", "leg"))
    ytdlp_mock = AsyncMock(return_value=([], {}, "sign_in_required"))
    monkeypatch.setattr(dispatcher.state, "IG_CLIENT", object())

    with patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "download_instagram_embed",
                      new=AsyncMock(return_value=([], "", "", ""))), \
         patch.object(dispatcher, "download_instagram_instagrapi", new=instagrapi_mock), \
         patch.object(dispatcher, "_run_ytdlp_with_cookie_fallback", new=ytdlp_mock):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://www.instagram.com/reel/ABC123/", tmp_folder, target_lang=None
        )

    assert files == [ig_file]
    assert status == "IG_STATUS"
    assert instagrapi_mock.await_count == 1
    assert ytdlp_mock.await_count == 0


@pytest.mark.asyncio
async def test_download_media_instagram_sign_in_required_tries_instagrapi(tmp_folder):
    ig_file = os.path.join(tmp_folder, "reel.mp4")
    with open(ig_file, "wb") as f:
        f.write(b"x")
    instagrapi_mock = AsyncMock(return_value=([ig_file], "IG_STATUS", "leg", "leg"))
    scrape_mock = AsyncMock(return_value=([], "fail", "", "", False))
    with patch.object(dispatcher, "_run_ytdlp_with_cookie_fallback",
                      new=AsyncMock(return_value=([], {}, "sign_in_required"))), \
         patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "download_instagram_embed",
                      new=AsyncMock(return_value=([], "", "", ""))), \
         patch.object(dispatcher, "download_instagram_instagrapi", new=instagrapi_mock), \
         patch.object(dispatcher, "scrape_fallback", new=scrape_mock):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://www.instagram.com/reels/ABC123/", tmp_folder, target_lang=None
        )

    assert files == [ig_file]
    assert status == "IG_STATUS"
    assert instagrapi_mock.await_count == 1
    assert scrape_mock.await_count == 0


@pytest.mark.asyncio
async def test_download_media_instagram_sign_in_required_gives_up_without_scraper(tmp_folder):
    instagrapi_mock = AsyncMock(return_value=([], "", "", ""))
    scrape_mock = AsyncMock(return_value=([], "fail", "", "", False))
    with patch.object(dispatcher, "_run_ytdlp_with_cookie_fallback",
                      new=AsyncMock(return_value=([], {}, "sign_in_required"))), \
         patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "download_instagram_embed",
                      new=AsyncMock(return_value=([], "", "", ""))), \
         patch.object(dispatcher, "download_instagram_instagrapi", new=instagrapi_mock), \
         patch.object(dispatcher, "scrape_fallback", new=scrape_mock):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://www.instagram.com/reels/ABC123/", tmp_folder, target_lang=None
        )

    assert files == []
    assert instagrapi_mock.await_count == 1
    assert scrape_mock.await_count == 0
    assert "login" in status.lower()


@pytest.mark.asyncio
async def test_download_media_instagram_private_does_not_retry_instagrapi(tmp_folder):
    instagrapi_mock = AsyncMock(return_value=([], "", "", ""))
    scrape_mock = AsyncMock(return_value=([], "fail", "", "", False))
    with patch.object(dispatcher, "_run_ytdlp_with_cookie_fallback",
                      new=AsyncMock(return_value=([], {}, "private"))), \
         patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "download_instagram_embed",
                      new=AsyncMock(return_value=([], "", "", ""))), \
         patch.object(dispatcher, "download_instagram_instagrapi", new=instagrapi_mock), \
         patch.object(dispatcher, "scrape_fallback", new=scrape_mock):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://www.instagram.com/reels/ABC123/", tmp_folder, target_lang=None
        )

    assert files == []
    assert instagrapi_mock.await_count == 0
    assert scrape_mock.await_count == 0


@pytest.mark.asyncio
async def test_download_media_returns_generic_fail_when_all_fail(tmp_folder):
    """yt-dlp e scrape vazios -> retorna status semântico generic_fail (handler usa generic_fail msg)."""
    with patch.object(dispatcher, "_run_ytdlp_with_cookie_fallback",
                      new=AsyncMock(return_value=([], {'title': 'Só texto', 'description': 'conteúdo'}, None))), \
         patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "scrape_fallback",
                      new=AsyncMock(return_value=([], "fail", "", "", False))):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://example.com/post", tmp_folder, target_lang=None
        )

    assert files == []
    assert status
    assert short == ""
    assert full == ""


@pytest.mark.asyncio
async def test_threads_enriches_caption_from_article(tmp_folder):
    pw_file = os.path.join(tmp_folder, "threads.mp4")
    article_short = "📄 <b>Título</b>\n\nCorpo do artigo\n\n🔗 link"
    article_full = article_short + " (versão completa muito mais longa) "

    with patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "download_threads",
                      new=AsyncMock(return_value=([pw_file], "OK_THREADS", "", ""))), \
         patch.object(dispatcher, "fetch_article_caption",
                      new=AsyncMock(return_value=(article_short, article_full))):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://threads.net/@user/post/1", tmp_folder, target_lang=None
        )

    assert files == [pw_file]
    assert short == article_short
    assert full == article_full
    assert is_article is True


@pytest.mark.asyncio
async def test_x_keeps_caption_when_already_present(tmp_folder):
    x_file = os.path.join(tmp_folder, "x.mp4")
    tweet_short = "📄 <b>Tweet legal</b>\n\nTexto do tweet aqui\n\n🔗 link"
    tweet_full = tweet_short + " mais texto"
    article_mock = AsyncMock(return_value=("ARTIGO_NAO_DEVERIA", "ARTIGO_NAO_DEVERIA"))

    with patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "download_x",
                      new=AsyncMock(return_value=([x_file], "OK_X", tweet_short, tweet_full))), \
         patch.object(dispatcher, "fetch_article_caption", new=article_mock):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://x.com/u/status/1", tmp_folder, target_lang=None
        )

    assert short == tweet_short
    assert full == tweet_full
    assert is_article is False
    article_mock.assert_not_called()


def test_caption_is_weak_detects_empty_and_link_only():
    from downloaders.dispatcher import _caption_is_weak
    assert _caption_is_weak("") is True
    assert _caption_is_weak("   ") is True
    assert _caption_is_weak("🔗 <a href='https://x.com/'>Link Original</a>") is True
    assert _caption_is_weak("📄 <b>T</b>\n\nDesc\n\n🔗 <a>L</a>") is False
    assert _caption_is_weak("Just text content") is False


@pytest.mark.asyncio
async def test_x_text_only_returns_caption_without_files(tmp_folder):
    text_short = "📄 <b>@user</b>\n\nTexto puro do tweet\n\n🔗 link"
    text_full = text_short + " (full)"

    with patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "download_x",
                      new=AsyncMock(return_value=([], "X_TEXT_ONLY", text_short, text_full))):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://x.com/u/status/1", tmp_folder, target_lang=None
        )

    assert files == []
    assert short == text_short
    assert full == text_full
    assert status == "X_TEXT_ONLY"


@pytest.mark.asyncio
async def test_threads_text_only_returns_caption_without_files(tmp_folder):
    text_short = "📄 <b>@user</b>\n\nThread só com texto\n\n🔗 link"
    text_full = text_short + " (full)"

    with patch.object(dispatcher, "_resolve_short_reddit_url", new=_passthrough_async_mock()), \
         patch.object(dispatcher, "download_threads",
                      new=AsyncMock(return_value=([], "THREADS_TEXT_ONLY", text_short, text_full))):
        files, status, short, full, is_article = await dispatcher.download_media(
            "https://threads.net/@u/post/X", tmp_folder, target_lang=None
        )

    assert files == []
    assert short == text_short
    assert full == text_full
    assert status == "THREADS_TEXT_ONLY"
