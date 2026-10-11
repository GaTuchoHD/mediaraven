"""Testes dos helpers compartilhados entre os extratores de Reddit."""
from downloaders.reddit_common import (
    build_reddit_caption,
    clean_reddit_media_url,
    is_reddit_media_url,
    looks_like_image,
    reddit_external_link,
)


def _news_post(**over):
    base = {
        "is_self": False,
        "title": "Notícia importante",
        "domain": "g1.globo.com",
        "url_overridden_by_dest": "https://g1.globo.com/materia.html",
        "url": "https://g1.globo.com/materia.html",
        "preview": {"images": [{"source": {"url": "https://preview.redd.it/x.jpg?width=640&amp;s=abc"}}]},
    }
    base.update(over)
    return base


def test_reddit_external_link_news_returns_url_title_thumb():
    out = reddit_external_link(_news_post())
    assert out["external_url"] == "https://g1.globo.com/materia.html"
    assert out["title"] == "Notícia importante"
    assert out["thumbnail_url"] == "https://preview.redd.it/x.jpg?width=640&s=abc"


def test_reddit_external_link_youtube_link():
    out = reddit_external_link(_news_post(domain="youtube.com",
                                         url_overridden_by_dest="https://youtu.be/abc",
                                         url="https://youtu.be/abc"))
    assert out["external_url"] == "https://youtu.be/abc"


def test_reddit_external_link_none_for_self_post():
    assert reddit_external_link(_news_post(is_self=True, domain="self.brasil")) is None


def test_reddit_external_link_none_for_video():
    assert reddit_external_link(_news_post(is_video=True)) is None
    assert reddit_external_link(_news_post(post_hint="hosted:video")) is None


def test_reddit_external_link_rich_video_is_external():
    """rich:video = vídeo externo embedado (YouTube etc.) -> deve virar link externo."""
    out = reddit_external_link(_news_post(
        post_hint="rich:video", domain="youtu.be",
        url_overridden_by_dest="https://youtu.be/d0avNDsWslg", url="https://youtu.be/d0avNDsWslg",
    ))
    assert out["external_url"] == "https://youtu.be/d0avNDsWslg"


def test_reddit_external_link_none_for_gallery():
    assert reddit_external_link(_news_post(media_metadata={"a": {}})) is None


def test_reddit_external_link_none_for_reddit_native_hosts():
    for dest in ("https://i.redd.it/x.jpg", "https://v.redd.it/x", "https://www.reddit.com/r/a/comments/b/"):
        assert reddit_external_link(_news_post(url_overridden_by_dest=dest, url=dest)) is None


def test_reddit_external_link_none_for_direct_image():
    assert reddit_external_link(_news_post(domain="imgur.com",
                                           url_overridden_by_dest="https://i.imgur.com/x.jpg",
                                           url="https://i.imgur.com/x.jpg")) is None


def test_reddit_external_link_no_preview_thumb_is_none():
    out = reddit_external_link(_news_post(preview={}))
    assert out["thumbnail_url"] is None


def test_clean_unescapes_entities():
    raw = "https://preview.redd.it/x.jpg?amp;width=640&amp;crop=smart"
    out = clean_reddit_media_url(raw)
    assert out == "https://i.redd.it/x.jpg"


def test_clean_replaces_preview_with_i():
    raw = "https://preview.redd.it/abc.jpg"
    assert clean_reddit_media_url(raw) == "https://i.redd.it/abc.jpg"


def test_clean_strips_query():
    raw = "https://i.redd.it/abc.png?foo=1"
    assert clean_reddit_media_url(raw) == "https://i.redd.it/abc.png"


def test_clean_returns_none_for_falsy():
    assert clean_reddit_media_url("") is None
    assert clean_reddit_media_url(None) is None


def test_is_reddit_media_accepts_redd_it():
    assert is_reddit_media_url("https://i.redd.it/abc.jpg") is True
    assert is_reddit_media_url("https://preview.redd.it/xyz.png") is True


def test_is_reddit_media_rejects_junk():
    assert is_reddit_media_url("https://i.redd.it/snoovatar/xyz.png") is False
    assert is_reddit_media_url("https://i.redd.it/award_images/foo.png") is False
    assert is_reddit_media_url("https://i.redd.it/icon/xyz.png") is False


def test_is_reddit_media_rejects_other_hosts():
    assert is_reddit_media_url("https://example.com/x.jpg") is False
    assert is_reddit_media_url("") is False


def test_looks_like_image_detects_extensions():
    assert looks_like_image("https://x.com/a.jpg") is True
    assert looks_like_image("https://x.com/a.PNG") is True
    assert looks_like_image("https://x.com/a.webp?foo=1") is True
    assert looks_like_image("https://x.com/a.gif?x") is True
    assert looks_like_image("https://x.com/page") is False
    assert looks_like_image("") is False


def test_build_reddit_caption_with_title_and_selftext():
    short, full = build_reddit_caption(
        "Cute puppy playing",
        "Found this little guy in my yard today.",
        "https://reddit.com/r/aww/comments/x/",
    )
    assert "Cute puppy playing" in short
    assert "Found this little guy" in short
    assert "<b>" in short
    assert "<a href=" in short
    assert "Found this little guy" in full


def test_build_reddit_caption_only_title():
    short, full = build_reddit_caption("Just a photo", "", "https://reddit.com/r/pics/comments/x/")
    assert "Just a photo" in short
    assert "<a href=" in short
    assert "Just a photo" in full


def test_build_reddit_caption_empty_returns_empty_string():
    short, _full = build_reddit_caption("", "", "https://reddit.com/r/x/comments/y/")
    assert short == ""


def test_build_reddit_caption_html_escapes_specials():
    short, full = build_reddit_caption("Title <with> & stuff", "body & <stuff>", "https://reddit.com/r/x/comments/y/")
    assert "&lt;" in short
    assert "&amp;" in short
    assert "<with>" not in short
    assert "&lt;" in full
    assert "<with>" not in full


def test_build_reddit_caption_full_not_truncated():
    """A versão full não trunca; permite chunking lá no handlers."""
    body = "x" * 5000
    short, full = build_reddit_caption("Long body", body, "https://reddit.com/r/x/comments/y/")
    assert len(short) <= 1024
    assert "..." in short
    assert len(full) > 5000
    assert "..." not in full[:5000]


def test_build_reddit_caption_titulo_longo_vai_inteiro():
    titulo = "T" * 299 + "Z"
    short, full = build_reddit_caption(titulo, "corpo " * 400, "https://www.reddit.com/r/x/comments/abc/y/")
    assert titulo in short
    assert len(short) <= 1024
    assert titulo in full
