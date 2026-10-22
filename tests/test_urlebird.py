from unittest.mock import AsyncMock, patch

import pytest

from downloaders import urlebird


PAGE_URL = (
    "https://urlebird.com/video/"
    "%E4%B9%85%E3%81%97%E3%81%B6%E3%82%8A%E3%81%AE%E6%8A%95%E7%A8%BF-"
    "7638961428594019604/"
)
VIDEO_URL = "https://v16-web.example/video/path/?mime_type=video_mp4&amp;token=abc"
HTML = f"""
<html><head>
  <meta property="og:title" content="Urlebird · ふうか">
  <meta property="og:description"
        content="324.7K views | 久しぶりの投稿#復活 #散歩 #fyp | ふうか (@fuuka_040)">
  <meta property="og:video" content="{VIDEO_URL}">
  <meta property="og:video:type" content="video/mp4">
</head></html>
"""


def test_extract_urlebird_payload_prefers_video_and_builds_caption_info():
    media, info = urlebird._extract_urlebird_payload(HTML, PAGE_URL)

    assert media == [
        ("video", "https://v16-web.example/video/path/?mime_type=video_mp4&token=abc")
    ]
    assert info["uploader_id"] == "@fuuka_040"
    assert info["description"] == "久しぶりの投稿#復活 #散歩 #fyp"
    assert info["webpage_url"] == PAGE_URL


@pytest.mark.asyncio
async def test_download_urlebird_uses_direct_media_and_referer(tmp_path):
    output = str(tmp_path / "scrape_media_0.mp4")
    download_mock = AsyncMock(return_value=([output], 0))

    with patch.object(urlebird, "_fetch_html", new=AsyncMock(return_value=HTML)), \
         patch.object(urlebird, "_download_all", new=download_mock):
        files, status, short, full = await urlebird.download_urlebird(
            PAGE_URL, str(tmp_path)
        )

    assert files == [output]
    assert status
    assert "@fuuka_040" in short
    assert "久しぶりの投稿" in short
    assert full
    media_arg, referer_arg, folder_arg = download_mock.await_args.args
    assert media_arg[0][0] == "video"
    assert referer_arg == PAGE_URL
    assert folder_arg == str(tmp_path)


@pytest.mark.asyncio
async def test_download_urlebird_falls_through_when_cloudflare_blocks(tmp_path):
    with patch.object(urlebird, "_fetch_html", new=AsyncMock(return_value=None)):
        result = await urlebird.download_urlebird(PAGE_URL, str(tmp_path))

    assert result == ([], "", "", "")


@pytest.mark.asyncio
async def test_download_urlebird_uses_playwright_media_when_fast_path_is_blocked(tmp_path):
    output = str(tmp_path / "scrape_media_0.mp4")
    download_mock = AsyncMock(return_value=([output], 0))
    playwright_mock = AsyncMock(return_value=([("video", VIDEO_URL)], [], HTML))

    with patch.object(urlebird, "_fetch_html", new=AsyncMock(return_value=None)), \
         patch.object(urlebird, "_gather_media_via_playwright", new=playwright_mock), \
         patch.object(urlebird, "_download_all", new=download_mock):
        files, status, short, full = await urlebird.download_urlebird(
            PAGE_URL, str(tmp_path)
        )

    assert files == [output]
    assert status
    assert "@fuuka_040" in short
    playwright_mock.assert_awaited_once_with(PAGE_URL, wait_for_video_ms=10000)
