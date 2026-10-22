import pytest

from downloaders.reddit_playwright import (
    _extract_reddit_media,
    _reddit_html_candidates,
    normalize_reddit_url,
)


@pytest.mark.parametrize("input_url,expected", [
    ("https://reddit.com/r/foo/comments/abc/x/", "https://old.reddit.com/r/foo/comments/abc/x/"),
    ("https://www.reddit.com/r/foo/comments/abc/x/", "https://old.reddit.com/r/foo/comments/abc/x/"),
    ("https://new.reddit.com/r/foo/comments/abc/x/", "https://old.reddit.com/r/foo/comments/abc/x/"),
    ("https://np.reddit.com/r/foo/comments/abc/x/", "https://old.reddit.com/r/foo/comments/abc/x/"),
    ("https://www.reddit.com/r/pics/comments/abc/?utm_source=share", "https://old.reddit.com/r/pics/comments/abc/"),
])
def test_force_old_reddit_swaps_to_old(input_url, expected):
    assert normalize_reddit_url(input_url) == expected


@pytest.mark.parametrize("url", [
    "https://old.reddit.com/r/foo/",
    "https://i.redd.it/abc.jpg",
    "https://v.redd.it/xyz/DASH_720.mp4",
    "https://redd.it/abc",
    "https://example.com/r/foo/",
])
def test_force_old_reddit_leaves_other_hosts_unchanged(url):
    expected = "https://old.reddit.com/comments/abc/" if url == "https://redd.it/abc" else url
    assert normalize_reddit_url(url) == expected


def test_force_old_reddit_handles_invalid_url():
    assert normalize_reddit_url("") == ""
    assert normalize_reddit_url("not-a-url") == "not-a-url"


def test_reddit_html_candidates_strip_fragment_and_try_current_first():
    url = "https://www.reddit.com/r/foo/comments/abc/post/?utm_source=share#lightbox"
    assert _reddit_html_candidates(url) == [
        "https://www.reddit.com/r/foo/comments/abc/post/",
        "https://old.reddit.com/r/foo/comments/abc/post/",
    ]


def test_reddit_html_candidates_preserve_source_id_for_crosspost_target():
    url = "https://www.reddit.com/r/foo/submit?source_id=t3_1v2lmxf&utm_source=share#lightbox"
    assert _reddit_html_candidates(url) == [
        "https://www.reddit.com/r/foo/submit",
        "https://old.reddit.com/comments/1v2lmxf/",
    ]


def test_extract_reddit_media_prefers_explicit_post_video_over_og_thumbnail():
    html = """
    <html><head>
      <meta property="og:image" content="https://preview.redd.it/video-cover.jpg?width=640">
    </head><body>
      <div class="expando" data-url="https://v.redd.it/postvideo/DASH_720.mp4"></div>
      <a href="https://v.redd.it/recommendedvideo">recommended</a>
    </body></html>
    """

    _title, _author, video_url, image_urls = _extract_reddit_media(html)

    assert video_url == "https://v.redd.it/postvideo/DASH_720.mp4"
    assert image_urls == ["https://preview.redd.it/video-cover.jpg?width=640"]


def test_extract_reddit_media_prefers_post_metadata_over_recommendations():
    page_url = "https://www.reddit.com/r/foo/comments/abc/post/"
    html = """
    <html><head>
      <title>Post title : r/foo</title>
      <meta property="og:image" content="https://preview.redd.it/post-image.jpg?width=640">
    </head><body>
      <a href="https://v.redd.it/recommendedvideo">recommended</a>
    </body></html>
    """

    title, author, video_url, image_urls = _extract_reddit_media(html, page_url)

    assert title == "Post title"
    assert author == ""
    assert video_url == ""
    assert image_urls == ["https://preview.redd.it/post-image.jpg?width=640"]
