from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cookies
import handlers
import state
from lifecycle import instagram_login


class _ReplyMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))


@pytest.mark.asyncio
async def test_cookies_status_command_reports_missing_database(monkeypatch):
    message = _ReplyMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        message=message,
    )
    monkeypatch.setattr(handlers, "ALLOW_ALL", True)
    monkeypatch.setattr(state, "FIREFOX_COOKIES_CACHE", [])
    monkeypatch.setattr(cookies, "get_firefox_cookie_status", lambda: {
        'path': '/safe/cookies/firefox/cookies.sqlite',
        'exists': False,
        'readable': False,
    })

    await handlers.cookies_status_command_handler(update, SimpleNamespace())

    assert len(message.calls) == 1
    text, kwargs = message.calls[0]
    assert "não encontrado" in text
    assert "/safe/cookies/firefox/cookies.sqlite" in text
    assert kwargs['parse_mode'] == 'HTML'


@pytest.mark.asyncio
async def test_cookies_status_command_never_includes_cookie_values(monkeypatch):
    message = _ReplyMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        message=message,
    )
    monkeypatch.setattr(handlers, "ALLOW_ALL", True)
    monkeypatch.setattr(state, "FIREFOX_COOKIES_CACHE", [{}])
    monkeypatch.setattr(cookies, "get_firefox_cookie_status", lambda: {
        'path': '/safe/cookies/firefox/cookies.sqlite',
        'exists': True,
        'readable': True,
        'modified_at': 1000,
        'total': 3,
        'active': 2,
        'expired': 1,
        'session': 1,
        'domains': 2,
        'platforms': {'YouTube/Google': 2, 'Instagram': 1},
        'readiness': {
            'YouTube/Google': True,
            'Instagram': True,
            'X/Twitter': False,
        },
    })
    monkeypatch.setattr(cookies, "get_reddit_cookie_status", lambda: {
        'path': '/safe/reddit_cookies.txt',
        'exists': True,
        'readable': True,
        'total': 26,
        'active': 26,
        'expired': 0,
        'error': None,
    })

    await handlers.cookies_status_command_handler(update, SimpleNamespace())

    text, _kwargs = message.calls[0]
    assert "Ativos:</b> 2" in text
    assert "YouTube/Google: 2" in text
    assert "Reddit (arquivo dedicado): 26 ativos / 0 expirados" in text
    assert "Reddit: ✅ pronto" in text
    assert "cookie_value" not in text


class _CookieCommandMessage:
    def __init__(self):
        self.reply_to_message = None
        self.text = "/cookies_add instagram sessionid=fresh"
        self.caption = None
        self.deleted = False

    async def reply_text(self, *_args, **_kwargs):
        return None

    async def delete(self):
        self.deleted = True


class _Confirmation:
    def __init__(self, text):
        self.text_html = text

    async def edit_text(self, *_args, **_kwargs):
        return None


class _CookieBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, *, text, **_kwargs):
        self.messages.append(text)
        return _Confirmation(text)


@pytest.mark.asyncio
async def test_cookies_add_instagram_reauthenticates_active_client(monkeypatch):
    message = _CookieCommandMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=123, username="owner"),
        effective_chat=SimpleNamespace(id=123, type="private"),
        effective_message=message,
        message=message,
    )
    bot = _CookieBot()
    context = SimpleNamespace(
        args=["instagram", "sessionid=fresh"],
        bot=bot,
    )
    reauthenticate = AsyncMock(return_value=True)
    monkeypatch.setattr(handlers, "ALLOW_ALL", True)
    monkeypatch.setattr(state, "PW_CONTEXT", None)
    monkeypatch.setattr(
        cookies,
        "install_raw_cookies",
        lambda *_args: {"platform": "Instagram", "installed": 1},
    )
    monkeypatch.setattr(cookies, "extract_firefox_cookies", lambda: [])
    monkeypatch.setattr(instagram_login, "init_instagrapi_async", reauthenticate)

    await handlers.cookies_add_command_handler(update, context)

    reauthenticate.assert_awaited_once_with()
    assert message.deleted is True
    assert "Instagrapi:</b> reautenticado" in bot.messages[-1]
