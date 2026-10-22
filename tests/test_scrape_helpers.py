from downloaders._scrape_helpers import (
    classify_media_url,
    dedupe_key,
    extract_iframes,
    extract_jsonld_media,
    extract_meta_media,
    extract_player_configs,
    is_junk_url,
    merge_media_lists,
    rewrite_to_max_resolution,
)


def test_is_junk_url_data_uri():
    assert is_junk_url("data:image/gif;base64,R0lGODlhAQABAA==")


def test_is_junk_url_tracking_host():
    assert is_junk_url("https://www.googletagmanager.com/p.gif")


def test_is_junk_url_tracking_pixel():
    assert is_junk_url("https://x.com/pixel.gif")


def test_is_junk_url_favicon():
    assert is_junk_url("https://example.com/favicon.ico")


def test_is_junk_url_real_image():
    assert not is_junk_url("https://images.unsplash.com/photo-12345.jpg")


def test_is_junk_url_avatar_not_filtered_by_url():
    assert not is_junk_url("https://cdn.example.com/avatars/123.jpg")
    assert not is_junk_url("https://example.com/static/icons/sprite.png")


def test_is_junk_url_facebook_static_assets():
    assert is_junk_url("https://static.xx.fbcdn.net/rsrc.php/v3/y8/r/sticker.png")
    assert is_junk_url("https://www.facebook.com/rsrc.php/v3/y8/r/asset.png")
    assert is_junk_url("https://external.fbcdn.net/safe_image.php?url=https://x.com/y.jpg")


def test_is_junk_url_reddit_ui_assets():
    assert is_junk_url("https://styles.redditmedia.com/t5_xxx/styles/communityIcon_abc.png?frame=1")
    assert is_junk_url("https://preview.redd.it/snoovatar/avatars/abc-headshot.png")
    assert is_junk_url("https://alb.reddit.com/i.gif?za=...")


def test_is_junk_url_keeps_real_fb_post_image():
    assert not is_junk_url("https://scontent-xx1-1.xx.fbcdn.net/v/t39.30808-6/123_456_789_n.jpg")
    assert not is_junk_url("https://scontent.cdninstagram.com/v/t51/post.jpg")


def test_classify_video_extension():
    assert classify_media_url("https://x.com/foo.mp4") == "video"


def test_classify_image_extension():
    assert classify_media_url("https://x.com/foo.PNG?bar=1") == "image"


def test_classify_hls_extension():
    assert classify_media_url("https://x.com/playlist.m3u8?auth=1") == "hls"


def test_classify_dash_extension():
    assert classify_media_url("https://x.com/manifest.mpd") == "dash"


def test_classify_via_content_type_video():
    assert classify_media_url("https://x.com/blob/abc", "video/mp4; codecs=avc1") == "video"


def test_classify_via_content_type_hls():
    assert (
        classify_media_url("https://x.com/blob/abc", "application/vnd.apple.mpegurl") == "hls"
    )


def test_classify_unknown():
    assert classify_media_url("https://x.com/page") is None


def test_rewrite_twimg_adds_orig():
    out = rewrite_to_max_resolution("https://pbs.twimg.com/media/abc.jpg?format=jpg&name=large")
    assert "name=orig" in out
    assert "name=large" not in out


def test_rewrite_twimg_no_query():
    out = rewrite_to_max_resolution("https://pbs.twimg.com/media/abc.jpg")
    assert "name=orig" in out


def test_rewrite_fbcdn_strips_size():
    out = rewrite_to_max_resolution(
        "https://scontent-iad3-1.fbcdn.net/v/t39/12345_s640x640_n.jpg"
    )
    assert "_s640x640_" not in out
    assert "_n.jpg" in out


def test_rewrite_pinimg_to_originals():
    out = rewrite_to_max_resolution("https://i.pinimg.com/236x/aa/bb/cc/abc.jpg")
    assert "/originals/" in out
    assert "/236x/" not in out


def test_rewrite_unknown_host_unchanged():
    url = "https://random.example.com/image.jpg?v=1"
    assert rewrite_to_max_resolution(url) == url


def test_dedupe_key_extracts_hex_id():
    a = "https://cdn1.example.com/path/abc123def456789012345678.jpg?w=100"
    b = "https://cdn2.example.com/other/abc123def456789012345678.jpg?w=200"
    assert dedupe_key(a) == dedupe_key(b)


def test_dedupe_key_different_assets():
    a = "https://cdn.example.com/abc123def456789012345678.jpg"
    b = "https://cdn.example.com/zzz999fff888777666555444.jpg"
    assert dedupe_key(a) != dedupe_key(b)


def test_extract_meta_media_og_video():
    html = '<meta property="og:video" content="https://example.com/video.mp4">'
    out = extract_meta_media(html)
    assert ("video", "https://example.com/video.mp4") in out


def test_extract_meta_media_og_image():
    html = '<meta property="og:image" content="https://example.com/img.jpg">'
    out = extract_meta_media(html)
    assert ("image", "https://example.com/img.jpg") in out


def test_extract_meta_media_relative_url():
    html = '<meta property="og:image" content="/img.jpg">'
    out = extract_meta_media(html, base_url="https://example.com/post/1")
    assert ("image", "https://example.com/img.jpg") in out


def test_extract_meta_media_attribute_order_swapped():
    html = '<meta content="https://example.com/img.jpg" property="og:image">'
    out = extract_meta_media(html)
    assert ("image", "https://example.com/img.jpg") in out


def test_extract_jsonld_video_object():
    html = """
    <script type="application/ld+json">
    {"@type": "VideoObject", "contentUrl": "https://ex.com/v.mp4",
     "thumbnailUrl": "https://ex.com/t.jpg"}
    </script>
    """
    out = extract_jsonld_media(html)
    assert ("video", "https://ex.com/v.mp4") in out
    assert ("image", "https://ex.com/t.jpg") in out


def test_extract_jsonld_nested():
    html = """
    <script type="application/ld+json">
    {"@graph": [{"@type": "VideoObject", "contentUrl": "https://ex.com/v.mp4"}]}
    </script>
    """
    out = extract_jsonld_media(html)
    assert ("video", "https://ex.com/v.mp4") in out


def test_extract_player_configs_jw_file():
    html = 'jwplayer().setup({file: "https://ex.com/stream.m3u8", width: 640});'
    out = extract_player_configs(html)
    assert ("hls", "https://ex.com/stream.m3u8") in out


def test_extract_player_configs_videojs_src():
    html = 'videojs("p").src({src: "https://ex.com/v.mp4", type: "video/mp4"});'
    out = extract_player_configs(html)
    assert ("video", "https://ex.com/v.mp4") in out


def test_extract_iframes_youtube():
    html = '<iframe src="https://www.youtube.com/embed/abc123"></iframe>'
    out = extract_iframes(html)
    assert "https://www.youtube.com/embed/abc123" in out


def test_extract_iframes_vimeo():
    html = '<iframe src="https://player.vimeo.com/video/12345"></iframe>'
    out = extract_iframes(html)
    assert "https://player.vimeo.com/video/12345" in out


def test_extract_iframes_ignores_unrelated():
    html = '<iframe src="https://random.com/widget"></iframe>'
    assert extract_iframes(html) == []


def test_merge_media_lists_dedupes_and_caps():
    a = [("image", "https://cdn.example.com/abc123def456789012345678.jpg?w=100")]
    b = [("image", "https://cdn2.example.com/abc123def456789012345678.jpg?w=200")]
    c = [("image", "https://cdn.example.com/zzz999fff888777666555444.jpg")]
    out = merge_media_lists(a, b, c, cap=10)
    assert len(out) == 2


def test_merge_media_lists_preserves_order():
    a = [("video", "https://ex.com/aaa111bbb222ccc333ddd444.mp4")]
    b = [("image", "https://ex.com/eee555fff666ggg777hhh888.jpg")]
    out = merge_media_lists(a, b)
    assert out[0][0] == "video"
    assert out[1][0] == "image"


def test_gallery_dl_handles_known_site():
    from downloaders.fallback import _can_handle_with_gallery_dl
    assert _can_handle_with_gallery_dl("https://www.pinterest.com/pin/12345/")
    assert _can_handle_with_gallery_dl("https://imgur.com/gallery/abc123")


def test_gallery_dl_skips_unknown_site():
    from downloaders.fallback import _can_handle_with_gallery_dl
    assert not _can_handle_with_gallery_dl("https://random.unknownsite999.example/whatever")


def test_extract_article_returns_title_and_body():
    from downloaders._scrape_helpers import extract_article
    html = """
    <html><head><title>Notícia importante</title></head><body>
    <article>
    <h1>Notícia importante</h1>
    <p>Este é o primeiro parágrafo do artigo, contendo uma boa quantidade de
    texto para que o extractor consiga reconhecer como um artigo legítimo
    e não apenas um trecho qualquer.</p>
    <p>Este é o segundo parágrafo, que adiciona mais conteúdo substancial
    pra garantir que o threshold de 300 caracteres seja superado tranquilamente
    pelo somatório das partes textuais reconhecidas.</p>
    <p>Terceiro parágrafo só pra reforçar o sinal de que isso é um artigo
    de verdade, com várias frases bem articuladas e contexto narrativo.</p>
    </article>
    </body></html>
    """
    result = extract_article(html, min_chars=100)
    assert result is not None
    title, body = result
    assert "Notícia importante" in title
    assert "primeiro parágrafo" in body
    assert "\n\nEste é o segundo parágrafo" in body
    assert "\n\nTerceiro parágrafo" in body


def test_format_article_body_separates_paragraphs_and_groups_lists():
    from downloaders._scrape_helpers import _format_article_body

    raw = (
        "Introdução da matéria.\r\n"
        "\r\n"
        "Primeiro parágrafo.\n"
        "- Primeiro item\n"
        "- Segundo item\n"
        "Conclusão."
    )

    assert _format_article_body(raw) == (
        "Introdução da matéria.\n\n"
        "Primeiro parágrafo.\n\n"
        "- Primeiro item\n"
        "- Segundo item\n\n"
        "Conclusão."
    )


def test_extract_article_returns_none_for_short_content():
    from downloaders._scrape_helpers import extract_article
    html = "<html><body><p>Curto demais.</p></body></html>"
    assert extract_article(html, min_chars=300) is None


def test_extract_article_returns_none_for_empty():
    from downloaders._scrape_helpers import extract_article
    assert extract_article("") is None
    assert extract_article("   ") is None
