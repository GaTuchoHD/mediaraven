from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from downloaders.x import download_x


@pytest.mark.asyncio
async def test_download_x_rejects_url_without_status(tmp_path):
    files, _status, short, full = await download_x(
        "https://x.com/example", str(tmp_path)
    )
    assert files == []
    assert short == ""
    assert full == ""


@pytest.mark.asyncio
async def test_download_x_handles_api_failure(tmp_path):
    response = Mock(status_code=503)
    with patch("downloaders.x.requests.get", return_value=response):
        files, _status, short, full = await download_x(
            "https://x.com/example/status/123", str(tmp_path)
        )
    assert files == []
    assert short == ""
    assert full == ""


@pytest.mark.asyncio
async def test_download_x_downloads_photo_and_best_video(tmp_path):
    response = Mock(status_code=200)
    response.json.return_value = {
        "tweet": {
            "author": {"name": "Alice", "screen_name": "alice"},
            "text": "Veja <isso> #tag @alguem",
            "media": {
                "all": [
                    {"type": "photo", "url": "https://cdn.example/photo.jpg"},
                    {
                        "type": "video",
                        "variants": [
                            {
                                "content_type": "video/mp4",
                                "bitrate": 256000,
                                "url": "https://cdn.example/low.mp4",
                            },
                            {
                                "content_type": "video/mp4",
                                "bitrate": 2176000,
                                "url": "https://cdn.example/high.mp4",
                            },
                            {
                                "content_type": "application/x-mpegURL",
                                "url": "https://cdn.example/stream.m3u8",
                            },
                        ],
                    },
                ]
            },
        }
    }

    async def fake_download(url, destination, **_kwargs):
        Path(destination).write_bytes(b"x" * 2048)
        return True

    mocked_download = AsyncMock(side_effect=fake_download)
    with (
        patch("downloaders.x.requests.get", return_value=response),
        patch("downloaders.x.async_download_file", mocked_download),
    ):
        files, _status, short, full = await download_x(
            "https://x.com/example/status/123", str(tmp_path)
        )

    assert len(files) == 2
    called_urls = [call.args[0] for call in mocked_download.await_args_list]
    assert called_urls == [
        "https://cdn.example/photo.jpg",
        "https://cdn.example/high.mp4",
    ]
    assert "Alice" in short
    assert "&lt;isso&gt;" in short
    assert "#tag" not in short
    assert "@alguem" not in short
    assert "https://x.com/example/status/123" in full


@pytest.mark.asyncio
async def test_download_x_ignores_failed_or_tiny_downloads(tmp_path):
    response = Mock(status_code=200)
    response.json.return_value = {
        "tweet": {
            "media": {
                "all": [
                    {"type": "photo", "url": "https://cdn.example/failed.jpg"},
                    {"type": "photo", "url": "https://cdn.example/tiny.jpg"},
                ]
            }
        }
    }

    async def fake_download(url, destination, **_kwargs):
        if url.endswith("failed.jpg"):
            return False
        Path(destination).write_bytes(b"tiny")
        return True

    with (
        patch("downloaders.x.requests.get", return_value=response),
        patch("downloaders.x.async_download_file", AsyncMock(side_effect=fake_download)),
    ):
        files, _status, _short, _full = await download_x(
            "https://x.com/example/status/123", str(tmp_path)
        )

    assert files == []


@pytest.mark.asyncio
async def test_download_x_uses_quote_media_when_main_tweet_has_none(tmp_path):
    response = Mock(status_code=200)
    response.json.return_value = {
        "tweet": {
            "author": {"name": "Bob", "screen_name": "bob"},
            "text": "Veja este post citado",
            "quote": {
                "media": {
                    "all": [
                        {
                            "type": "photo",
                            "url": "https://cdn.example/quoted-one.jpg",
                        },
                        {
                            "type": "photo",
                            "url": "https://cdn.example/quoted-two.jpg",
                        },
                    ]
                }
            },
        }
    }

    async def fake_download(url, destination, **_kwargs):
        Path(destination).write_bytes(b"x" * 2048)
        return True

    mocked_download = AsyncMock(side_effect=fake_download)
    with (
        patch("downloaders.x.requests.get", return_value=response),
        patch("downloaders.x.async_download_file", mocked_download),
    ):
        files, _status, short, full = await download_x(
            "https://x.com/example/status/123", str(tmp_path)
        )

    assert len(files) == 2
    assert [call.args[0] for call in mocked_download.await_args_list] == [
        "https://cdn.example/quoted-one.jpg",
        "https://cdn.example/quoted-two.jpg",
    ]
    assert "Veja este post citado" in short
    assert "https://x.com/example/status/123" in full
