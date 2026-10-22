import unittest
from unittest.mock import AsyncMock, patch

from downloaders import kick


class TestKickDownloader(unittest.TestCase):
    def test_extract_kick_username(self):
        self.assertEqual(kick._extract_kick_username("https://kick.com/kodhak"), "kodhak")
        self.assertEqual(kick._extract_kick_username("https://kick.com/kodhak/"), "kodhak")
        self.assertIsNone(kick._extract_kick_username("https://kick.com/video/123456"))
        self.assertIsNone(kick._extract_kick_username("https://kick.com/terms"))

    @patch("downloaders.kick._fetch_kick_channel_api")
    @patch("downloaders.kick.async_download_file", new_callable=AsyncMock)
    @patch("downloaders.kick.validate_url", new_callable=AsyncMock)
    def test_download_kick_channel_live(self, mock_val, mock_dl, mock_api):
        mock_api.return_value = {
            "user": {"username": "kodhak", "bio": "Streamer Gamer"},
            "livestream": {
                "session_title": "Playing Valorant",
                "viewers": 1500,
                "thumbnail": {"url": "https://example.com/thumb.jpg"},
                "categories": [{"name": "VALORANT"}]
            }
        }
        mock_dl.return_value = True

        files, status, short_cap, full_cap = asyncio_run(
            kick.download_kick("https://kick.com/kodhak", "/tmp/test_kick_folder")
        )

        self.assertTrue(status.startswith("🔴 Ao Vivo"))
        self.assertIn("kodhak", short_cap.lower())
        self.assertIn("valorant", short_cap.lower())


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
