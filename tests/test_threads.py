import json
import os

import pytest

from downloaders.threads import (
    _build_threads_caption,
    _extract_media,
    _extract_post_code,
    _find_post_by_code,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "threads")


def _load_fixture(name: str) -> dict:
    with open(os.path.join(FIXTURES_DIR, f"{name}.json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("url,expected", [
    ("https://www.threads.com/@user/post/ABC123", "ABC123"),
    ("https://www.threads.com/@user/post/ABC123?xmt=foo&slof=1", "ABC123"),
    ("https://www.threads.com/@user/post/ABC123/", "ABC123"),
    ("https://threads.net/@user/post/Xyz_-9", "Xyz_-9"),
    ("https://www.threads.com/@user", None),
    ("https://example.com/", None),
])
def test_extract_post_code(url, expected):
    assert _extract_post_code(url) == expected


def test_extract_media_photo_single():
    post = _load_fixture("photo_single")
    media = _extract_media(post)
    assert len(media) == 1
    kind, url = media[0]
    assert kind == "image"
    assert url.startswith("https://")
    assert "fbcdn" in url


def test_extract_media_carousel_photos():
    post = _load_fixture("carousel_photos")
    media = _extract_media(post)
    assert len(media) == 2
    assert all(kind == "image" for kind, _ in media)
    assert all(url.startswith("https://") for _, url in media)


def test_extract_media_carousel_mixed():
    post = _load_fixture("carousel_mixed")
    media = _extract_media(post)
    assert len(media) == 2
    kinds = [k for k, _ in media]
    assert "video" in kinds
    assert "image" in kinds


def test_extract_media_quoted_repost_falls_back():
    post = _load_fixture("video")
    media = _extract_media(post)
    assert len(media) >= 1
    kinds = [k for k, _ in media]
    assert "video" in kinds


def test_extract_media_picks_highest_resolution_for_image():
    post = _load_fixture("photo_single")
    iv = post["image_versions2"]
    cands = iv["candidates"]
    max_width = max(c.get("width") or 0 for c in cands)

    media = _extract_media(post)
    _, picked = media[0]

    matching = [c for c in cands if c.get("width") == max_width]
    assert any(c["url"] == picked for c in matching)


def test_extract_media_empty_post_returns_empty():
    assert _extract_media({}) == []
    assert _extract_media({"media_type": 1}) == []


def test_extract_media_from_linked_inline_media():
    post = {
        "media_type": 19,
        "image_versions2": {"candidates": []},
        "text_post_app_info": {
            "linked_inline_media": {
                "media_type": 2,
                "video_versions": [
                    {"width": 640, "url": "https://cdn/video640.mp4"},
                    {"width": 1280, "url": "https://cdn/video1280.mp4"},
                ],
            },
        },
    }
    media = _extract_media(post)
    assert media == [("video", "https://cdn/video1280.mp4")]


def test_extract_media_linked_inline_media_image():
    post = {
        "media_type": 19,
        "image_versions2": {"candidates": []},
        "text_post_app_info": {
            "linked_inline_media": {
                "media_type": 1,
                "image_versions2": {"candidates": [{"width": 1080, "url": "https://cdn/x.jpg"}]},
            },
        },
    }
    media = _extract_media(post)
    assert media == [("image", "https://cdn/x.jpg")]


def test_extract_media_prefers_direct_media_over_linked():
    post = {
        "media_type": 2,
        "video_versions": [{"width": 720, "url": "https://cdn/direct.mp4"}],
        "text_post_app_info": {
            "linked_inline_media": {
                "media_type": 2,
                "video_versions": [{"width": 720, "url": "https://cdn/linked.mp4"}],
            },
        },
    }
    media = _extract_media(post)
    assert media == [("video", "https://cdn/direct.mp4")]


def test_find_post_by_code_returns_match_with_media_type():
    tree = {
        "data": {
            "edges": [
                {"node": {"thread_items": [{"post": {"code": "X1", "media_type": 1}}]}},
                {"node": {"thread_items": [{"post": {"code": "X2", "media_type": 8}}]}},
            ]
        }
    }
    found = _find_post_by_code(tree, "X2")
    assert found is not None
    assert found["code"] == "X2"
    assert found["media_type"] == 8


def test_find_post_by_code_skips_match_without_media_type():
    tree = {"post": {"code": "X1"}, "other": {"code": "X1", "media_type": 1}}
    found = _find_post_by_code(tree, "X1")
    assert found is not None
    assert "media_type" in found


def test_find_post_by_code_returns_none_when_absent():
    assert _find_post_by_code({"a": 1}, "MISSING") is None
    assert _find_post_by_code([], "MISSING") is None


def test_build_threads_caption_includes_text_user_and_link():
    post = _load_fixture("photo_single")
    short, full = _build_threads_caption(post, "https://threads.net/@lindamaah_/post/X")
    assert "@lindamaah_" in short
    assert "Mulheres" in short
    assert "threads.net" in short
    assert "@lindamaah_" in full


def test_build_threads_caption_empty_when_no_text():
    post = _load_fixture("video")
    assert _build_threads_caption(post, "https://threads.net/@u/post/X") == ("", "")


def test_build_threads_caption_handles_missing_post():
    assert _build_threads_caption({}, "https://x.com/") == ("", "")
    assert _build_threads_caption(None, "https://x.com/") == ("", "")
