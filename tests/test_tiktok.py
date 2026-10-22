import asyncio
from unittest.mock import AsyncMock

import pytest

import state
from downloaders import tiktok


def test_extract_video_id_only_accepts_video_path():
    assert tiktok._extract_video_id("https://www.tiktok.com/@user/video/7668713800765443335") == "7668713800765443335"
    assert tiktok._extract_video_id("https://www.tiktok.com/@user/photo/7668713800765443335") is None


@pytest.mark.parametrize(
    ("url", "content_type", "status", "expected"),
    [
        ("https://v16-webapp-prime.tiktok.com/video/tos/file", "video/mp4", 200, True),
        ("https://v16.tiktokcdn.com/video/file", "video/mp4", 206, True),
        ("https://sf16-website-login.neutral.ttwstatic.com/playback1.mp4", "video/mp4", 206, False),
        ("https://evil.example/video.mp4", "video/mp4", 200, False),
        ("https://v16-webapp.tiktok.com/video/file", "text/html", 200, False),
        ("https://v16-webapp.tiktok.com/video/file", "video/mp4", 403, False),
    ],
)
def test_official_media_response_allowlist(url, content_type, status, expected):
    assert tiktok._is_official_media_response(url, content_type, status) is expected


class _FakeResponse:
    url = "https://v16-webapp-prime.tiktok.com/video/tos/file"
    status = 200
    headers = {"content-type": "video/mp4", "content-length": "200000"}

    async def body(self):
        return b"v" * 200_000


class _FakeLocator:
    @property
    def first(self):
        return self

    async def click(self, **_kwargs):
        return None


class _FakePage:
    def __init__(self):
        self.handler = None

    def on(self, event, handler):
        assert event == "response"
        self.handler = handler

    async def goto(self, *_args, **_kwargs):
        self.handler(_FakeResponse())

    async def wait_for_timeout(self, *_args):
        await asyncio.sleep(0)

    def locator(self, *_args):
        return _FakeLocator()

    async def close(self):
        return None


class _FakeContext:
    async def new_page(self):
        return _FakePage()


@pytest.mark.asyncio
async def test_download_tiktok_captures_and_validates_real_video(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "PW_BROWSER", object())
    monkeypatch.setattr(state, "PW_CONTEXT", _FakeContext())
    monkeypatch.setattr(state, "PW_SEMAPHORE", asyncio.Semaphore(1))
    monkeypatch.setattr(state, "AIOHTTP_SESSION", None)
    monkeypatch.setattr(tiktok, "cfg", lambda _key: 50)
    monkeypatch.setattr(
        tiktok, "async_ffprobe_codecs", AsyncMock(return_value=("h264", "aac")),
    )

    files, status, short, full = await tiktok.download_tiktok(
        "https://www.tiktok.com/@user/video/7668713800765443335",
        str(tmp_path),
    )

    assert len(files) == 1
    assert (tmp_path / "tiktok_playwright.mp4").stat().st_size == 200_000
    assert "TikTok" in status
    assert short == full == ""
