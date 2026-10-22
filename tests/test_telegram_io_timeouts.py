from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import telegram_io
from telegram.error import BadRequest, RetryAfter, TimedOut


@pytest.fixture(autouse=True)
def _shared_download_root(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram_io, "BASE_DOWNLOAD_DIR", str(tmp_path))


class _FakeBot:
    def __init__(self):
        self.send_photo = AsyncMock()
        self.send_video = AsyncMock()
        self.send_animation = AsyncMock()
        self.send_document = AsyncMock()
        self.send_media_group = AsyncMock()


def _make_context():
    ctx = MagicMock()
    ctx.bot = _FakeBot()
    return ctx


@pytest.mark.parametrize(
    "message",
    [
        "Chat_write_forbidden",
        "Not enough rights to send photos to the chat",
        "Have no rights to send a message",
    ],
)
def test_permission_errors_are_permanent(message):
    assert telegram_io.is_permanent_telegram_error(BadRequest(message)) is True


@pytest.mark.asyncio
async def test_send_video_passes_path_and_timeouts(tmp_path):
    f = tmp_path / "vid.mp4"
    f.write_bytes(b"x")
    ctx = _make_context()

    with patch.object(telegram_io, "cfg", lambda key: 600 if key == "TELEGRAM_UPLOAD_TIMEOUT" else 10):
        await telegram_io.send_downloaded_media(
            context=ctx,
            chat_id=1,
            files=[str(f)],
            original_msg_id=10,
            upload_kwargs={},
        )

    kwargs = ctx.bot.send_video.await_args.kwargs
    assert kwargs["video"] == str(f)
    assert kwargs["read_timeout"] == 600
    assert kwargs["write_timeout"] == 600
    assert kwargs["connect_timeout"] == 600


@pytest.mark.asyncio
async def test_send_photo_passes_path_and_timeouts(tmp_path):
    from PIL import Image
    f = tmp_path / "img.jpg"
    Image.new("RGB", (100, 100), color="red").save(f, format="JPEG")
    ctx = _make_context()

    with patch.object(telegram_io, "cfg", lambda key: 600 if key == "TELEGRAM_UPLOAD_TIMEOUT" else 10):
        await telegram_io.send_downloaded_media(
            context=ctx,
            chat_id=1,
            files=[str(f)],
            original_msg_id=10,
            upload_kwargs={},
        )

    kwargs = ctx.bot.send_photo.await_args.kwargs
    assert kwargs["photo"] == str(f)
    assert kwargs["read_timeout"] == 600
    assert kwargs["write_timeout"] == 600
    assert kwargs["connect_timeout"] == 600


@pytest.mark.asyncio
async def test_send_document_passes_path_and_timeouts(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"x")
    ctx = _make_context()

    with patch.object(telegram_io, "cfg", lambda key: 600 if key == "TELEGRAM_UPLOAD_TIMEOUT" else 10):
        await telegram_io.send_downloaded_media(
            context=ctx,
            chat_id=1,
            files=[str(f)],
            original_msg_id=10,
            upload_kwargs={},
        )

    kwargs = ctx.bot.send_document.await_args.kwargs
    assert kwargs["document"] == str(f)
    assert kwargs["read_timeout"] == 600
    assert kwargs["write_timeout"] == 600
    assert kwargs["connect_timeout"] == 600


@pytest.mark.asyncio
async def test_gif_converts_to_mp4_and_routes_to_send_animation(tmp_path):
    from PIL import Image
    f = tmp_path / "loop.gif"
    Image.new("RGB", (480, 360), color="red").save(f, format="GIF")
    mp4 = tmp_path / "loop.gif.mp4"

    async def fake_gif_to_mp4(input_path, output_path, timeout=60):
        with open(output_path, "wb") as fp:
            fp.write(b"fake-mp4")
        return True

    ctx = _make_context()
    with patch.object(telegram_io, "cfg", lambda key: 600 if key == "TELEGRAM_UPLOAD_TIMEOUT" else 10), \
         patch.object(telegram_io, "async_gif_to_mp4", fake_gif_to_mp4):
        await telegram_io.send_downloaded_media(
            context=ctx,
            chat_id=1,
            files=[str(f)],
            original_msg_id=10,
            upload_kwargs={},
        )

    assert ctx.bot.send_animation.await_count == 1
    assert ctx.bot.send_video.await_count == 0
    kwargs = ctx.bot.send_animation.await_args.kwargs
    assert kwargs["animation"] == str(mp4)
    assert kwargs["width"] == 480
    assert kwargs["height"] == 360


@pytest.mark.asyncio
async def test_gif_falls_back_to_original_when_conversion_fails(tmp_path):
    from PIL import Image
    f = tmp_path / "loop.gif"
    Image.new("RGB", (320, 200), color="blue").save(f, format="GIF")

    async def failing_gif_to_mp4(input_path, output_path, timeout=60):
        return False

    ctx = _make_context()
    with patch.object(telegram_io, "cfg", lambda key: 600 if key == "TELEGRAM_UPLOAD_TIMEOUT" else 10), \
         patch.object(telegram_io, "async_gif_to_mp4", failing_gif_to_mp4):
        await telegram_io.send_downloaded_media(
            context=ctx,
            chat_id=1,
            files=[str(f)],
            original_msg_id=10,
            upload_kwargs={},
        )

    kwargs = ctx.bot.send_animation.await_args.kwargs
    assert kwargs["animation"] == str(f)
    assert kwargs["width"] == 320
    assert kwargs["height"] == 200


@pytest.mark.asyncio
async def test_caller_overrides_take_precedence(tmp_path):
    f = tmp_path / "vid.mp4"
    f.write_bytes(b"x")
    ctx = _make_context()

    with patch.object(telegram_io, "cfg", lambda key: 600 if key == "TELEGRAM_UPLOAD_TIMEOUT" else 10):
        await telegram_io.send_downloaded_media(
            context=ctx,
            chat_id=1,
            files=[str(f)],
            original_msg_id=10,
            upload_kwargs={"write_timeout": 9999},
        )

    kwargs = ctx.bot.send_video.await_args.kwargs
    assert kwargs["write_timeout"] == 9999
    assert kwargs["read_timeout"] == 600


@pytest.mark.asyncio
async def test_video_webm_converts_before_send(tmp_path):
    f = tmp_path / "clip.webm"
    f.write_bytes(b"x")
    converted = tmp_path / "clip.tg.mp4"

    async def fake_ensure(filepath, timeout=600):
        with open(converted, "wb") as fp:
            fp.write(b"converted")
        return str(converted)

    ctx = _make_context()
    with patch.object(telegram_io, "cfg", lambda key: 600 if key in ("TELEGRAM_UPLOAD_TIMEOUT", "VIDEO_CONVERT_TIMEOUT") else 10), \
         patch.object(telegram_io, "async_ensure_telegram_video", fake_ensure):
        await telegram_io.send_downloaded_media(
            context=ctx,
            chat_id=1,
            files=[str(f)],
            original_msg_id=10,
            upload_kwargs={},
        )

    kwargs = ctx.bot.send_video.await_args.kwargs
    assert kwargs["video"] == str(converted)


@pytest.mark.asyncio
async def test_video_webm_falls_back_to_original_when_convert_fails(tmp_path):
    f = tmp_path / "clip.webm"
    f.write_bytes(b"x")

    async def fake_ensure(filepath, timeout=600):
        return None

    ctx = _make_context()
    with patch.object(telegram_io, "cfg", lambda key: 600 if key in ("TELEGRAM_UPLOAD_TIMEOUT", "VIDEO_CONVERT_TIMEOUT") else 10), \
         patch.object(telegram_io, "async_ensure_telegram_video", fake_ensure):
        await telegram_io.send_downloaded_media(
            context=ctx,
            chat_id=1,
            files=[str(f)],
            original_msg_id=10,
            upload_kwargs={},
        )

    kwargs = ctx.bot.send_video.await_args.kwargs
    assert kwargs["video"] == str(f)


@pytest.mark.asyncio
async def test_video_mp4_is_normalized_before_send(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    called = {"count": 0}

    async def fake_ensure(filepath, timeout=600):
        called["count"] += 1
        return filepath

    ctx = _make_context()
    with patch.object(telegram_io, "cfg", lambda key: 600 if key in ("TELEGRAM_UPLOAD_TIMEOUT", "VIDEO_CONVERT_TIMEOUT") else 10), \
         patch.object(telegram_io, "async_ensure_telegram_video", fake_ensure):
        await telegram_io.send_downloaded_media(
            context=ctx,
            chat_id=1,
            files=[str(f)],
            original_msg_id=10,
            upload_kwargs={},
        )

    assert called["count"] == 1
    kwargs = ctx.bot.send_video.await_args.kwargs
    assert kwargs["video"] == str(f)


@pytest.mark.asyncio
async def test_media_group_converts_webm_video(tmp_path):
    from PIL import Image
    img = tmp_path / "a.jpg"
    vid = tmp_path / "b.webm"
    Image.new("RGB", (100, 100), color="red").save(img, format="JPEG")
    vid.write_bytes(b"y")
    converted = tmp_path / "b.tg.mp4"

    async def fake_ensure(filepath, timeout=600):
        with open(converted, "wb") as fp:
            fp.write(b"converted")
        return str(converted)

    ctx = _make_context()

    def fake_cfg(key):
        return {
            "TELEGRAM_UPLOAD_TIMEOUT": 600,
            "VIDEO_CONVERT_TIMEOUT": 600,
            "MEDIA_GROUP_CHUNK_SIZE": 10,
            "MEDIA_GROUP_DELAY": 0,
            "TELEGRAM_MAX_UPLOAD_MB": 2000,
        }[key]

    with patch.object(telegram_io, "cfg", fake_cfg), \
         patch.object(telegram_io, "async_ensure_telegram_video", fake_ensure):
        await telegram_io.send_downloaded_media(
            context=ctx,
            chat_id=1,
            files=[str(img), str(vid)],
            original_msg_id=10,
            upload_kwargs={},
        )

    kwargs = ctx.bot.send_media_group.await_args.kwargs
    media = kwargs["media"]
    assert len(media) == 2
    assert media[1].media == converted.absolute().as_uri()


@pytest.mark.asyncio
async def test_media_group_passes_paths_and_timeouts(tmp_path):
    from PIL import Image
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    Image.new("RGB", (100, 100), color="red").save(a, format="JPEG")
    Image.new("RGB", (100, 100), color="blue").save(b, format="JPEG")
    ctx = _make_context()

    def fake_cfg(key):
        return {
            "TELEGRAM_UPLOAD_TIMEOUT": 600,
            "MEDIA_GROUP_CHUNK_SIZE": 10,
            "MEDIA_GROUP_DELAY": 0,
            "TELEGRAM_MAX_UPLOAD_MB": 2000,
        }[key]

    with patch.object(telegram_io, "cfg", fake_cfg):
        await telegram_io.send_downloaded_media(
            context=ctx,
            chat_id=1,
            files=[str(a), str(b)],
            original_msg_id=10,
            upload_kwargs={},
        )

    kwargs = ctx.bot.send_media_group.await_args.kwargs
    media = kwargs["media"]
    assert len(media) == 2
    assert media[0].media == a.absolute().as_uri()
    assert media[1].media == b.absolute().as_uri()
    assert kwargs["read_timeout"] == 600
    assert kwargs["write_timeout"] == 600
    assert kwargs["connect_timeout"] == 600


@pytest.mark.asyncio
async def test_invalid_image_is_sent_as_document(tmp_path):
    f = tmp_path / "not-really-an-image.jpg"
    f.write_bytes(b"<html>blocked</html>")
    ctx = _make_context()

    with patch.object(telegram_io, "cfg", lambda key: 600 if key == "TELEGRAM_UPLOAD_TIMEOUT" else 10):
        await telegram_io.send_downloaded_media(
            context=ctx,
            chat_id=1,
            files=[str(f)],
            original_msg_id=10,
            upload_kwargs={},
        )

    assert ctx.bot.send_photo.await_count == 0
    assert ctx.bot.send_document.await_args.kwargs["document"] == str(f)


@pytest.mark.asyncio
async def test_send_video_timeout_retries_video_without_document_fallback(tmp_path):
    f = tmp_path / "slow.mp4"
    f.write_bytes(b"x")
    ctx = _make_context()
    ctx.bot.send_video.side_effect = [TimedOut(), MagicMock(video=MagicMock(file_id="ok"), photo=None, animation=None, document=None)]

    with patch.object(telegram_io, "cfg", lambda key: 600 if key == "TELEGRAM_UPLOAD_TIMEOUT" else 10), \
         patch.object(telegram_io.asyncio, "sleep", AsyncMock()):
        sent = await telegram_io.send_downloaded_media(
            context=ctx, chat_id=1, files=[str(f)], original_msg_id=10, upload_kwargs={},
        )

    assert ctx.bot.send_video.await_count == 2
    assert ctx.bot.send_document.await_count == 0
    assert sent == ["ok"]


@pytest.mark.asyncio
async def test_send_video_retry_after_retries_video_without_document_fallback(tmp_path):
    f = tmp_path / "limited.mp4"
    f.write_bytes(b"x")
    ctx = _make_context()
    ctx.bot.send_video.side_effect = [RetryAfter(2), MagicMock(video=MagicMock(file_id="ok"), photo=None, animation=None, document=None)]

    with patch.object(telegram_io, "cfg", lambda key: 600 if key == "TELEGRAM_UPLOAD_TIMEOUT" else 10), \
         patch.object(telegram_io.asyncio, "sleep", AsyncMock()) as sleep_mock:
        sent = await telegram_io.send_downloaded_media(
            context=ctx, chat_id=1, files=[str(f)], original_msg_id=10, upload_kwargs={},
        )

    assert ctx.bot.send_video.await_count == 2
    assert ctx.bot.send_document.await_count == 0
    sleep_mock.assert_awaited_once_with(3)
    assert sent == ["ok"]


@pytest.mark.asyncio
async def test_missing_reply_retries_same_video_without_reply_reference(tmp_path):
    f = tmp_path / "reply.mp4"
    f.write_bytes(b"x")
    ctx = _make_context()
    ctx.bot.send_video.side_effect = [
        BadRequest("Message to be replied not found"),
        MagicMock(video=MagicMock(file_id="ok"), photo=None, animation=None, document=None),
    ]

    with patch.object(telegram_io, "cfg", lambda key: 600 if key == "TELEGRAM_UPLOAD_TIMEOUT" else 10):
        sent = await telegram_io.send_downloaded_media(
            context=ctx,
            chat_id=1,
            files=[str(f)],
            original_msg_id=10,
            upload_kwargs={"reply_to_message_id": 10},
        )

    assert ctx.bot.send_video.await_count == 2
    first, second = ctx.bot.send_video.await_args_list
    assert first.kwargs["reply_to_message_id"] == 10
    assert "reply_to_message_id" not in second.kwargs
    assert ctx.bot.send_document.await_count == 0
    assert sent == ["ok"]


@pytest.mark.asyncio
async def test_send_video_retry_exhaustion_is_not_silent(tmp_path):
    f = tmp_path / "never-sent.mp4"
    f.write_bytes(b"x")
    ctx = _make_context()
    ctx.bot.send_video.side_effect = TimedOut()

    with patch.object(telegram_io, "cfg", lambda key: 600 if key == "TELEGRAM_UPLOAD_TIMEOUT" else 10), \
         patch.object(telegram_io.asyncio, "sleep", AsyncMock()), \
         pytest.raises(TimedOut):
        await telegram_io.send_downloaded_media(
            context=ctx, chat_id=1, files=[str(f)], original_msg_id=10, upload_kwargs={},
        )

    assert ctx.bot.send_video.await_count == 4
    assert ctx.bot.send_document.await_count == 0


@pytest.mark.asyncio
async def test_video_is_prepared_only_once_across_upload_retries(tmp_path):
    f = tmp_path / "once.mp4"
    f.write_bytes(b"x")
    ctx = _make_context()
    ctx.bot.send_video.side_effect = [TimedOut(), MagicMock(video=MagicMock(file_id="ok"), photo=None, animation=None, document=None)]
    ensure = AsyncMock(return_value=str(f))

    with patch.object(telegram_io, "cfg", lambda key: 600 if key == "TELEGRAM_UPLOAD_TIMEOUT" else 10), \
         patch.object(telegram_io.asyncio, "sleep", AsyncMock()), \
         patch.object(telegram_io, "_ensure_video", ensure):
        await telegram_io.send_downloaded_media(
            context=ctx, chat_id=1, files=[str(f)], original_msg_id=10, upload_kwargs={},
        )

    ensure.assert_awaited_once_with(str(f))


@pytest.mark.asyncio
async def test_video_format_fallback_removes_missing_reply(tmp_path):
    f = tmp_path / "odd.mp4"
    f.write_bytes(b"x")
    ctx = _make_context()
    ctx.bot.send_video.side_effect = BadRequest("Wrong file type")
    ctx.bot.send_document.side_effect = [
        BadRequest("Message to be replied not found"),
        MagicMock(video=None, photo=None, animation=None, document=MagicMock(file_id="doc")),
    ]

    with patch.object(telegram_io, "cfg", lambda key: 600 if key == "TELEGRAM_UPLOAD_TIMEOUT" else 10):
        sent = await telegram_io.send_downloaded_media(
            context=ctx,
            chat_id=1,
            files=[str(f)],
            original_msg_id=10,
            upload_kwargs={"reply_to_message_id": 10},
        )

    assert ctx.bot.send_document.await_count == 2
    assert "reply_to_message_id" not in ctx.bot.send_document.await_args_list[1].kwargs
    assert sent == ["doc"]


@pytest.mark.asyncio
async def test_animation_file_id_is_not_duplicated(tmp_path):
    from PIL import Image
    f = tmp_path / "loop.gif"
    Image.new("RGB", (32, 32), color="green").save(f, format="GIF")
    ctx = _make_context()
    ctx.bot.send_animation.return_value = MagicMock(
        animation=MagicMock(file_id="anim"), video=None, photo=None, document=None,
    )

    with patch.object(telegram_io, "cfg", lambda key: 600 if key == "TELEGRAM_UPLOAD_TIMEOUT" else 10), \
         patch.object(telegram_io, "_gif_to_mp4", AsyncMock(return_value=str(f))):
        sent = await telegram_io.send_downloaded_media(
            context=ctx, chat_id=1, files=[str(f)], original_msg_id=10, upload_kwargs={},
        )

    assert sent == ["anim"]


@pytest.mark.asyncio
async def test_animation_is_prepared_only_once_across_upload_retries(tmp_path):
    from PIL import Image
    f = tmp_path / "retry.gif"
    Image.new("RGB", (32, 32), color="purple").save(f, format="GIF")
    ctx = _make_context()
    ctx.bot.send_animation.side_effect = [
        TimedOut(),
        MagicMock(animation=MagicMock(file_id="anim"), video=None, photo=None, document=None),
    ]
    convert = AsyncMock(return_value=str(f))

    with patch.object(telegram_io, "cfg", lambda key: 600 if key == "TELEGRAM_UPLOAD_TIMEOUT" else 10), \
         patch.object(telegram_io.asyncio, "sleep", AsyncMock()), \
         patch.object(telegram_io, "_gif_to_mp4", convert):
        sent = await telegram_io.send_downloaded_media(
            context=ctx, chat_id=1, files=[str(f)], original_msg_id=10, upload_kwargs={},
        )

    convert.assert_awaited_once_with(str(f))
    assert ctx.bot.send_animation.await_count == 2
    assert sent == ["anim"]
