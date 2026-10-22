"""Testes de integração do download_reddit_json com aiohttp mockado."""
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

import state
from downloaders import reddit_json


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    async def json(self):
        return self._payload

    async def read(self):
        return b""


@asynccontextmanager
async def _fake_get_ctx(payload):
    yield _FakeResponse(payload)


def _make_session_mock(payload):
    session = AsyncMock()
    # Emular session.get(...) retornando um async context manager
    session.get = lambda *a, **kw: _fake_get_ctx(payload)
    return session


def test_loads_dedicated_reddit_cookie_file(tmp_path, monkeypatch):
    cookie_file = tmp_path / "reddit_cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".reddit.com\tTRUE\t/\tTRUE\t2147483647\treddit_session\tsecret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(reddit_json, "_REDDIT_COOKIE_FILE", str(cookie_file))

    cookies = reddit_json._load_reddit_cookie_file()

    assert cookies == {"reddit_session": "secret"}


def test_missing_dedicated_reddit_cookie_file(monkeypatch):
    monkeypatch.setattr(reddit_json, "_REDDIT_COOKIE_FILE", "/missing/reddit-cookies.txt")
    assert reddit_json._load_reddit_cookie_file() == {}


@pytest.fixture(autouse=True)
def _reset_state():
    state.FIREFOX_COOKIES_CACHE = []
    yield
    state.FIREFOX_COOKIES_CACHE = []


@pytest.mark.asyncio
async def test_reddit_json_gallery_items(tmp_path):
    """Gallery com gallery_data.items: deve extrair cada media_id na ordem."""
    folder = str(tmp_path / "task")
    os.makedirs(folder)
    payload = [
        {'data': {'children': [{'data': {
            'gallery_data': {'items': [{'media_id': 'aaa'}, {'media_id': 'bbb'}]},
            'media_metadata': {
                'aaa': {'status': 'valid', 's': {'u': 'https://preview.redd.it/aaa.jpg?x=1'}},
                'bbb': {'status': 'valid', 's': {'u': 'https://preview.redd.it/bbb.jpg?y=2'}},
            },
        }}]}}
    ]
    state.AIOHTTP_SESSION = _make_session_mock(payload)
    with patch.object(reddit_json, 'async_download_file', new=AsyncMock(return_value=False)):
        files, status, _short, _full = await reddit_json.download_reddit_json("https://reddit.com/r/x/comments/z/", folder)

    # Como o download mockou False, files fica vazio; o que queremos validar é o fluxo sem explodir.
    assert files == []
    assert status


@pytest.mark.asyncio
async def test_reddit_json_single_image_url(tmp_path):
    folder = str(tmp_path / "task")
    os.makedirs(folder)
    payload = [
        {'data': {'children': [{'data': {
            'url': 'https://preview.redd.it/single.png?a=1',
        }}]}}
    ]
    state.AIOHTTP_SESSION = _make_session_mock(payload)

    captured_urls = []

    async def fake_download(url, filepath):
        captured_urls.append(url)
        with open(filepath, 'wb') as f:
            f.write(b"fakebytes")
        return True

    with patch.object(reddit_json, 'async_download_file', new=AsyncMock(side_effect=fake_download)), \
         patch.object(reddit_json, 'normalize_image', side_effect=lambda p, **kw: p):
        files, status, _short, _full = await reddit_json.download_reddit_json("https://reddit.com/r/x/comments/z/", folder)

    assert len(files) == 1
    assert captured_urls == ["https://i.redd.it/single.png"]


@pytest.mark.asyncio
async def test_reddit_json_preview_images_fallback(tmp_path):
    folder = str(tmp_path / "task")
    os.makedirs(folder)
    payload = [
        {'data': {'children': [{'data': {
            'preview': {'images': [{'source': {'url': 'https://preview.redd.it/prev.jpg?a=1'}}]},
        }}]}}
    ]
    state.AIOHTTP_SESSION = _make_session_mock(payload)

    captured = []

    async def fake_download(url, filepath):
        captured.append(url)
        return False

    with patch.object(reddit_json, 'async_download_file', new=AsyncMock(side_effect=fake_download)):
        files, status, _short, _full = await reddit_json.download_reddit_json("https://reddit.com/r/x/comments/z/", folder)

    assert captured == ["https://i.redd.it/prev.jpg"]
    assert files == []


@pytest.mark.asyncio
async def test_reddit_json_crosspost_video_does_not_send_preview(tmp_path):
    folder = str(tmp_path / "task")
    os.makedirs(folder)
    payload = [
        {'data': {'children': [{'data': {
            'is_video': False,
            'post_hint': 'link',
            'url': 'https://reddit.com/r/x/comments/wrapper/',
            'preview': {'images': [{'source': {'url': 'https://preview.redd.it/cover.jpg'}}]},
            'crosspost_parent_list': [{
                'is_video': True,
                'post_hint': 'hosted:video',
                'secure_media': {'reddit_video': {'fallback_url': 'https://v.redd.it/id/video.mp4'}},
            }],
        }}]}}
    ]
    state.AIOHTTP_SESSION = _make_session_mock(payload)

    with patch.object(reddit_json, 'async_download_file', new=AsyncMock()) as download_mock:
        files, status, _short, _full = await reddit_json.download_reddit_json(
            "https://reddit.com/r/x/comments/wrapper/", folder,
        )

    assert files == []
    assert status == reddit_json.REDDIT_JSON_VIDEO
    download_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_reddit_json_strips_lightbox_fragment_before_json_request(tmp_path):
    folder = str(tmp_path / "task")
    os.makedirs(folder)
    payload = [{'data': {'children': [{'data': {}}]}}]
    requested_urls = []
    session = AsyncMock()

    def fake_get(url, *args, **kwargs):
        requested_urls.append(url)
        return _fake_get_ctx(payload)

    session.get = fake_get
    state.AIOHTTP_SESSION = session

    await reddit_json.download_reddit_json(
        "https://reddit.com/r/x/comments/z/post/#lightbox", folder,
    )

    assert requested_urls == ["https://reddit.com/r/x/comments/z/post.json?raw_json=1"]


@pytest.mark.asyncio
async def test_reddit_json_handles_malformed(tmp_path):
    """JSON quebrado não deve derrubar o bot — retorna lista vazia."""
    folder = str(tmp_path / "task")
    os.makedirs(folder)
    state.AIOHTTP_SESSION = _make_session_mock([])
    files, status, _short, _full = await reddit_json.download_reddit_json("https://reddit.com/r/x/comments/z/", folder)
    assert files == []
    assert status
