from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from downloaders import _platform


def test_is_kwai_host_kwai_video_com():
    assert _platform._is_kwai_host("kwai-video.com") is True


def test_is_kwai_host_www_kwai_com():
    assert _platform._is_kwai_host("www.kwai.com") is True


def test_is_kwai_host_subdomain():
    assert _platform._is_kwai_host("m.kwai.com") is True
    assert _platform._is_kwai_host("s.kw.ai") is True


def test_is_kwai_host_snackvideo():
    assert _platform._is_kwai_host("snackvideo.com") is True
    assert _platform._is_kwai_host("snackvideo.in") is True


def test_is_kwai_host_rejects_others():
    assert _platform._is_kwai_host("example.com") is False
    assert _platform._is_kwai_host("youtube.com") is False


class _FakeResp:
    def __init__(self, final_url):
        self.url = final_url
        self.status = 200
        self.headers = {}


@asynccontextmanager
async def _fake_get(final_url):
    yield _FakeResp(final_url)


@pytest.mark.asyncio
async def test_resolve_kwai_url_follows_redirect_and_strips_query():
    short = "https://kwai-video.com/p/CX5r8xz0"
    final = (
        "https://www.kwai.com/@robo.dos.video/video/5193363433508233530"
        "?userId=150000120098511&photoId=5193363433508233530&cc=WHATS_APP&shareEnter=1"
    )

    class _Sess:
        def get(self, url, headers=None, allow_redirects=True, timeout=15):
            return _fake_get(final)

    with patch.object(_platform.state, "AIOHTTP_SESSION", _Sess()):
        out = await _platform._resolve_kwai_url(short)

    assert out == "https://www.kwai.com/@robo.dos.video/video/5193363433508233530"


@pytest.mark.asyncio
async def test_resolve_kwai_url_passthrough_for_non_kwai():
    url = "https://example.com/page?x=1"
    out = await _platform._resolve_kwai_url(url)
    assert out == url


@pytest.mark.asyncio
async def test_resolve_kwai_url_returns_cleaned_even_when_input_is_canonical():
    url = "https://www.kwai.com/@user/video/123?cc=WHATS_APP"

    class _Sess:
        def get(self, u, headers=None, allow_redirects=True, timeout=15):
            return _fake_get(u)

    with patch.object(_platform.state, "AIOHTTP_SESSION", _Sess()):
        out = await _platform._resolve_kwai_url(url)

    assert out == "https://www.kwai.com/@user/video/123"


@pytest.mark.asyncio
async def test_resolve_kwai_url_falls_back_to_input_on_network_error():
    url = "https://kwai-video.com/p/abc"

    class _BoomSess:
        def get(self, u, headers=None, allow_redirects=True, timeout=15):
            raise RuntimeError("network down")

    with patch.object(_platform.state, "AIOHTTP_SESSION", _BoomSess()):
        out = await _platform._resolve_kwai_url(url)

    assert out == "https://kwai-video.com/p/abc"
