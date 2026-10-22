from unittest.mock import Mock, patch

import pytest

from downloaders.x import download_x


@pytest.mark.asyncio
async def test_text_only_post_returns_author_text_and_original_link(tmp_path):
    response = Mock(status_code=200)
    response.json.return_value = {
        'tweet': {
            'author': {'name': 'Ana & João', 'screen_name': 'ana'},
            'text': 'Olá <mundo>!\n\nLeia @colega e #noticias.',
        },
    }
    with patch('downloaders.x.requests.get', return_value=response):
        files, status, short, full = await download_x(
            'https://x.com/ana/status/123?s=20', str(tmp_path),
        )
    assert files == []
    assert short
    assert 'Ana &amp; João' in full
    assert '@ana' in full
    assert 'Olá &lt;mundo&gt;!\n\nLeia @colega e #noticias.' in full
    assert 'https://x.com/ana/status/123?s=20' in full
    assert 'texto' in status.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize('tweet', [{}, {'text': '  \n  '}, {'author': {'name': 'Ana'}}])
async def test_post_without_text_or_media_stays_empty(tmp_path, tweet):
    response = Mock(status_code=200)
    response.json.return_value = {'tweet': tweet}
    with patch('downloaders.x.requests.get', return_value=response):
        files, _status, short, full = await download_x('https://x.com/ana/status/123', str(tmp_path))
    assert (files, short, full) == ([], '', '')


@pytest.mark.asyncio
async def test_api_error_is_not_reported_as_text_success(tmp_path):
    response = Mock(status_code=404)
    response.json.return_value = {'tweet': {'text': 'Not found'}}
    with patch('downloaders.x.requests.get', return_value=response):
        files, _status, short, full = await download_x('https://x.com/ana/status/123', str(tmp_path))
    assert (files, short, full) == ([], '', '')


@pytest.mark.asyncio
async def test_failed_media_keeps_failure_even_when_post_has_text(tmp_path):
    from unittest.mock import AsyncMock
    response = Mock(status_code=200)
    response.json.return_value = {'tweet': {
        'text': 'Legenda da foto',
        'media': {'all': [{'type': 'photo', 'url': 'https://cdn.example/photo.jpg'}]},
    }}
    with patch('downloaders.x.requests.get', return_value=response), patch(
        'downloaders.x.async_download_file', AsyncMock(return_value=False),
    ):
        files, _status, short, full = await download_x('https://x.com/ana/status/123', str(tmp_path))
    assert (files, short, full) == ([], '', '')


@pytest.mark.asyncio
async def test_bot_sends_long_x_text_in_valid_html_chunks(monkeypatch, tmp_path):
    from html.parser import HTMLParser
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import handlers
    import media_cache

    class TelegramHTML(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags = []
            self.content = []
        def handle_starttag(self, tag, attrs):
            self.tags.append(tag)
        def handle_endtag(self, tag):
            assert self.tags.pop() == tag
        def handle_data(self, text):
            self.content.append(text)

    monkeypatch.setattr(media_cache, 'DB_PATH', str(tmp_path / 'cache.sqlite3'))
    media_cache.init_cache_db()
    monkeypatch.setattr(handlers, 'BASE_DOWNLOAD_DIR', str(tmp_path))
    response = Mock(status_code=200)
    response.json.return_value = {'tweet': {
        'author': {'name': 'Ana & João', 'screen_name': 'ana'},
        'text': ('こんにちは <mundo> & @colega #noticias\n' * 250).strip(),
    }}
    status_message = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    bot = SimpleNamespace(send_message=AsyncMock(return_value=status_message))
    context = SimpleNamespace(bot=bot, bot_data={})
    with patch('downloaders.x.requests.get', return_value=response):
        await handlers.process_media_request(
            context, 123, 456,
            'https://x.com/ana/status/123', user_id=789, is_retry=True,
        )

    chunks = [c.kwargs for c in bot.send_message.await_args_list if c.kwargs.get('parse_mode') == 'HTML']
    assert len(chunks) >= 2
    visible = []
    for chunk in chunks:
        assert len(chunk['text']) <= 4096
        parser = TelegramHTML()
        parser.feed(chunk['text'])
        parser.close()
        assert not parser.tags
        visible.extend(parser.content)
    text = ''.join(visible)
    assert text.count('こんにちは <mundo> & @colega #noticias') == 250
    assert 'Ana & João (@ana)' in text
    assert 'Link Original' in text
    assert chunks[0]['reply_to_message_id'] == 456
    assert not context.bot_data.get('retries')
    status_message.delete.assert_awaited_once()
