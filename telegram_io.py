import asyncio
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from PIL import Image
from telegram import InputMediaPhoto, InputMediaVideo
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut

from config import (
    cfg,
    ANIMATION_EXTS,
    BASE_DOWNLOAD_DIR,
    IMAGE_EXTS,
    VIDEO_EXTS,
)
from messages import lmsg
from utils import async_ensure_telegram_video, async_gif_to_mp4

logger = logging.getLogger(__name__)


class UploadSizeLimitExceeded(Exception):
    """One or more files exceeded the configured Telegram upload limit."""

    def __init__(self, skipped_count: int, max_mb: int, sent_count: int) -> None:
        self.skipped_count = skipped_count
        self.max_mb = max_mb
        self.sent_count = sent_count
        super().__init__(
            f"{skipped_count} file(s) exceeded the Telegram upload limit ({max_mb} MiB)"
        )


def _is_missing_reply_error(exc: BaseException) -> bool:
    if not isinstance(exc, BadRequest):
        return False
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "message to be replied not found",
            "message to reply not found",
            "replied message not found",
        )
    )


def _is_video_format_error(exc: BaseException) -> bool:
    """BadRequest variants where changing from video to document can help."""
    if not isinstance(exc, BadRequest):
        return False
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "wrong file type",
            "wrong type of the web page content",
            "failed to get http url content",
            "can't parse inputmedia",
            "cannot parse inputmedia",
        )
    )


def _stage_log(
    request_id: str,
    stage: str,
    started: float,
    *,
    outcome: str,
    size_bytes: Optional[int] = None,
    attempt: Optional[int] = None,
    error: Optional[BaseException] = None,
) -> None:
    logger.info(
        "pipeline request_id=%s stage=%s outcome=%s elapsed_ms=%d size_bytes=%s attempt=%s error_type=%s",
        request_id,
        stage,
        outcome,
        round((time.monotonic() - started) * 1000),
        size_bytes if size_bytes is not None else "na",
        attempt if attempt is not None else "na",
        type(error).__name__ if error is not None else "none",
    )


async def _send_video_classified(
    context: Any,
    chat_id: int,
    video_path: str,
    upload_kwargs: dict[str, Any],
    request_id: str,
    attempt: int,
) -> Any:
    """Send video while preserving retries and using document fallback narrowly."""
    started = time.monotonic()
    size_bytes = os.path.getsize(video_path) if os.path.exists(video_path) else None
    try:
        result = await context.bot.send_video(
            chat_id=chat_id, video=video_path, supports_streaming=True, **upload_kwargs,
        )
        _stage_log(request_id, "telegram_send_video", started, outcome="ok", size_bytes=size_bytes, attempt=attempt)
        return result
    except (RetryAfter, TimedOut) as exc:
        _stage_log(request_id, "telegram_send_video", started, outcome="retryable", size_bytes=size_bytes, attempt=attempt, error=exc)
        raise
    except BadRequest as exc:
        if _is_missing_reply_error(exc) and "reply_to_message_id" in upload_kwargs:
            upload_kwargs.pop("reply_to_message_id", None)
            retry_kwargs = dict(upload_kwargs)
            _stage_log(request_id, "telegram_send_video", started, outcome="retry_without_reply", size_bytes=size_bytes, attempt=attempt, error=exc)
            retry_started = time.monotonic()
            result = await context.bot.send_video(
                chat_id=chat_id, video=video_path, supports_streaming=True, **retry_kwargs,
            )
            _stage_log(request_id, "telegram_send_video", retry_started, outcome="ok_without_reply", size_bytes=size_bytes, attempt=attempt)
            return result
        if _is_video_format_error(exc):
            _stage_log(request_id, "telegram_send_video", started, outcome="format_fallback", size_bytes=size_bytes, attempt=attempt, error=exc)
            fallback_started = time.monotonic()
            try:
                result = await context.bot.send_document(
                    chat_id=chat_id, document=video_path, **upload_kwargs,
                )
            except BadRequest as fallback_exc:
                if not (_is_missing_reply_error(fallback_exc) and "reply_to_message_id" in upload_kwargs):
                    raise
                upload_kwargs.pop("reply_to_message_id", None)
                result = await context.bot.send_document(
                    chat_id=chat_id, document=video_path, **upload_kwargs,
                )
            _stage_log(request_id, "telegram_send_document_fallback", fallback_started, outcome="ok", size_bytes=size_bytes, attempt=attempt)
            return result
        _stage_log(request_id, "telegram_send_video", started, outcome="permanent_error", size_bytes=size_bytes, attempt=attempt, error=exc)
        raise
    except Exception as exc:
        _stage_log(request_id, "telegram_send_video", started, outcome="error", size_bytes=size_bytes, attempt=attempt, error=exc)
        raise


def prepare_local_api_file(filepath: str) -> str:
    """Make a transient file traversable by the separate local Bot API container."""
    path = Path(filepath).resolve()
    base = Path(BASE_DOWNLOAD_DIR).resolve()
    try:
        relative = path.relative_to(base)
    except ValueError as exc:
        raise ValueError("arquivo fora de BASE_DOWNLOAD_DIR") from exc
    if not relative.parts or not path.is_file():
        raise FileNotFoundError(filepath)

    current = path.parent
    while current != base:
        os.chmod(current, 0o711)
        current = current.parent
    os.chmod(path, 0o644)
    return str(path)


def infer_media_type(filepath: str) -> str:
    lower = filepath.casefold()
    if lower.endswith(IMAGE_EXTS):
        return "photo"
    if lower.endswith(VIDEO_EXTS) or lower.endswith(ANIMATION_EXTS):
        return "video"
    return "document"


def is_permanent_telegram_error(exc: BaseException) -> bool:
    """Errors that retries cannot repair and should invalidate cached file IDs."""
    if isinstance(exc, Forbidden):
        return True
    if not isinstance(exc, BadRequest):
        return False
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "wrong file identifier",
            "file reference expired",
            "file_id",
            "chat not found",
            "message to reply not found",
            "message to be replied not found",
            "replied message not found",
            "chat_write_forbidden",
            "not enough rights",
            "have no rights",
        )
    )


def _get_image_dims(filepath: str) -> tuple[Optional[int], Optional[int]]:
    try:
        with Image.open(filepath) as img:
            return img.size
    except Exception:
        return None, None


async def _gif_to_mp4(gif_path: str) -> Optional[str]:
    mp4_path = os.path.splitext(gif_path)[0] + '.gif.mp4'
    if await async_gif_to_mp4(gif_path, mp4_path):
        return mp4_path
    return None


async def _ensure_video(f_path: str) -> str:
    converted = await async_ensure_telegram_video(f_path, timeout=cfg("VIDEO_CONVERT_TIMEOUT"))
    return converted or f_path


async def send_downloaded_media(
    context: Any,
    chat_id: int,
    files: list[str],
    original_msg_id: int,
    upload_kwargs: dict[str, Any],
    caption: Optional[str] = None,
    request_id: Optional[str] = None,
) -> list[str]:
    """Send downloaded files (single or MediaGroup album) and return sent Telegram file_ids."""
    request_id = request_id or uuid.uuid4().hex[:12]
    pipeline_started = time.monotonic()
    sent_file_ids = []
    max_upload_mb = max(1, int(cfg("TELEGRAM_MAX_UPLOAD_MB")))
    max_upload_bytes = max_upload_mb * 1024 * 1024
    preflight_started = time.monotonic()
    eligible_files: list[str] = []
    oversized_sizes: list[int] = []
    total_size_bytes = 0
    for path in files:
        size_bytes = os.path.getsize(path)
        total_size_bytes += size_bytes
        if size_bytes > max_upload_bytes:
            oversized_sizes.append(size_bytes)
        else:
            eligible_files.append(path)

    preflight_outcome = (
        "too_large" if oversized_sizes and not eligible_files
        else "partial" if oversized_sizes
        else "ok"
    )
    logger.info(
        "pipeline request_id=%s stage=upload_preflight outcome=%s elapsed_ms=%d files=%d skipped=%d total_size_bytes=%d max_upload_bytes=%d",
        request_id,
        preflight_outcome,
        round((time.monotonic() - preflight_started) * 1000),
        len(files),
        len(oversized_sizes),
        total_size_bytes,
        max_upload_bytes,
    )
    if oversized_sizes and not eligible_files:
        raise UploadSizeLimitExceeded(
            skipped_count=len(oversized_sizes), max_mb=max_upload_mb, sent_count=0,
        )

    files = [prepare_local_api_file(path) for path in eligible_files]
    upload_kwargs = dict(upload_kwargs)

    if len(files) == 1:
        f_path = files[0]
        if caption:
            upload_kwargs['caption'] = caption

        timeout = cfg("TELEGRAM_UPLOAD_TIMEOUT")
        upload_kwargs.setdefault('read_timeout', timeout)
        upload_kwargs.setdefault('write_timeout', timeout)
        upload_kwargs.setdefault('connect_timeout', timeout)

        filename_lower = f_path.lower()
        prepared_video_path: Optional[str] = None
        prepared_animation_path: Optional[str] = None
        if filename_lower.endswith(VIDEO_EXTS):
            prepare_started = time.monotonic()
            prepared_video_path = prepare_local_api_file(await _ensure_video(f_path))
            _stage_log(
                request_id, "video_prepare", prepare_started, outcome="ok",
                size_bytes=os.path.getsize(prepared_video_path),
            )
        elif filename_lower.endswith(ANIMATION_EXTS):
            prepared_animation_path = prepare_local_api_file(await _gif_to_mp4(f_path) or f_path)
        final_path = prepared_video_path or prepared_animation_path or f_path
        if os.path.getsize(final_path) > max_upload_bytes:
            raise UploadSizeLimitExceeded(len(oversized_sizes) + 1, max_upload_mb, 0)
        for attempt in range(4):
            try:
                msg_obj = None
                if filename_lower.endswith(IMAGE_EXTS):
                    if _get_image_dims(f_path) != (None, None):
                        msg_obj = await context.bot.send_photo(chat_id=chat_id, photo=f_path, **upload_kwargs)
                    else:
                        msg_obj = await context.bot.send_document(chat_id=chat_id, document=f_path, **upload_kwargs)
                elif filename_lower.endswith(ANIMATION_EXTS):
                    anim_kwargs = dict(upload_kwargs)
                    width, height = _get_image_dims(f_path)
                    if width and height:
                        anim_kwargs.setdefault('width', width)
                        anim_kwargs.setdefault('height', height)
                    msg_obj = await context.bot.send_animation(
                        chat_id=chat_id, animation=prepared_animation_path or f_path, **anim_kwargs,
                    )
                elif filename_lower.endswith(VIDEO_EXTS):
                    msg_obj = await _send_video_classified(
                        context, chat_id, prepared_video_path or f_path, upload_kwargs, request_id, attempt + 1,
                    )
                else:
                    msg_obj = await context.bot.send_document(chat_id=chat_id, document=f_path, **upload_kwargs)
                if msg_obj:
                    if msg_obj.photo:
                        sent_file_ids.append(msg_obj.photo[-1].file_id)
                    elif msg_obj.video:
                        sent_file_ids.append(msg_obj.video.file_id)
                    elif msg_obj.animation:
                        sent_file_ids.append(msg_obj.animation.file_id)
                    elif msg_obj.document:
                        sent_file_ids.append(msg_obj.document.file_id)
                break
            except RetryAfter as e:
                if attempt == 3:
                    raise
                logger.warning(lmsg("telegram_io.flood_control_telegram", arg0=e.retry_after))
                await asyncio.sleep(e.retry_after + 1)
            except TimedOut:
                if attempt == 3:
                    raise
                logger.warning(lmsg("telegram_io.timeout_no_envio"))
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(lmsg("telegram_io.erro_fatal_ao", e=e), exc_info=True)
                raise e
    else:
        # MEDIA GROUP (ALBUMS) for multiple files. Telegram accepts at most 10
        # items and document albums cannot be mixed with photos/videos.
        chunk_size = max(2, min(10, int(cfg("MEDIA_GROUP_CHUNK_SIZE"))))
        for i in range(0, len(files), chunk_size):
            chunk_files = files[i:i + chunk_size]
            media_group = []
            prepared_items: list[tuple[str, str, Optional[str]]] = []
            document_files: list[tuple[str, Optional[str]]] = []

            for f_path in chunk_files:
                filename_lower = f_path.lower()
                is_first_item = i == 0 and not prepared_items and not document_files
                item_caption = caption if is_first_item else None

                if filename_lower.endswith(IMAGE_EXTS):
                    if _get_image_dims(f_path) != (None, None):
                        media_group.append(InputMediaPhoto(media=f_path, parse_mode='HTML', caption=item_caption))
                        prepared_items.append(("photo", f_path, item_caption))
                    else:
                        document_files.append((f_path, item_caption))
                elif filename_lower.endswith(ANIMATION_EXTS):
                    video_path = await _gif_to_mp4(f_path)
                    if video_path:
                        if os.path.getsize(video_path) > max_upload_bytes:
                            oversized_sizes.append(os.path.getsize(video_path))
                            continue
                        media_group.append(InputMediaVideo(media=video_path, parse_mode='HTML', caption=item_caption))
                        prepared_items.append(("video", video_path, item_caption))
                    else:
                        document_files.append((f_path, item_caption))
                elif filename_lower.endswith(VIDEO_EXTS):
                    video_path = await _ensure_video(f_path)
                    if os.path.getsize(video_path) > max_upload_bytes:
                        oversized_sizes.append(os.path.getsize(video_path))
                        continue
                    media_group.append(InputMediaVideo(media=video_path, parse_mode='HTML', caption=item_caption))
                    prepared_items.append(("video", video_path, item_caption))
                else:
                    document_files.append((f_path, item_caption))

            if len(media_group) == 1:
                kind, media_path, item_caption = prepared_items[0]
                kwargs = {
                    "chat_id": chat_id,
                    "reply_to_message_id": original_msg_id,
                    "caption": item_caption,
                    "parse_mode": "HTML",
                    "read_timeout": cfg("TELEGRAM_UPLOAD_TIMEOUT"),
                    "write_timeout": cfg("TELEGRAM_UPLOAD_TIMEOUT"),
                    "connect_timeout": cfg("TELEGRAM_UPLOAD_TIMEOUT"),
                }
                if kind == "photo":
                    sent = await context.bot.send_photo(photo=media_path, **kwargs)
                    if sent.photo:
                        sent_file_ids.append(sent.photo[-1].file_id)
                else:
                    sent = await context.bot.send_video(video=media_path, supports_streaming=True, **kwargs)
                    if sent.video:
                        sent_file_ids.append(sent.video.file_id)
            elif media_group:
                sent_group = False
                for attempt in range(5):
                    try:
                        timeout = cfg("TELEGRAM_UPLOAD_TIMEOUT")
                        msgs = await context.bot.send_media_group(
                            chat_id=chat_id,
                            media=media_group,
                            reply_to_message_id=original_msg_id,
                            read_timeout=timeout,
                            write_timeout=timeout,
                            connect_timeout=timeout,
                        )
                        if msgs:
                            for m in msgs:
                                if m.photo:
                                    sent_file_ids.append(m.photo[-1].file_id)
                                elif m.video:
                                    sent_file_ids.append(m.video.file_id)
                        sent_group = True
                        break
                    except RetryAfter as e:
                        logger.warning(lmsg("telegram_io.flood_control_de", arg0=e.retry_after))
                        await asyncio.sleep(e.retry_after + 1)
                    except TimedOut:
                        logger.warning(lmsg("telegram_io.timeout_no_envio_2"))
                        await asyncio.sleep(5)
                    except Exception as e:
                        logger.error(lmsg("telegram_io.erro_desconhecido_ao", e=e), exc_info=True)
                        if attempt < 4:
                            await asyncio.sleep(2)

                if not sent_group:
                    # One malformed item must not discard the entire album.
                    for kind, media_path, item_caption in prepared_items:
                        kwargs = {
                            "chat_id": chat_id,
                            "reply_to_message_id": original_msg_id,
                            "caption": item_caption,
                            "parse_mode": "HTML",
                            "read_timeout": cfg("TELEGRAM_UPLOAD_TIMEOUT"),
                            "write_timeout": cfg("TELEGRAM_UPLOAD_TIMEOUT"),
                            "connect_timeout": cfg("TELEGRAM_UPLOAD_TIMEOUT"),
                        }
                        if kind == "photo":
                            sent = await context.bot.send_photo(photo=media_path, **kwargs)
                            if sent.photo:
                                sent_file_ids.append(sent.photo[-1].file_id)
                        else:
                            sent = await context.bot.send_video(video=media_path, supports_streaming=True, **kwargs)
                            if sent.video:
                                sent_file_ids.append(sent.video.file_id)

            for document_path, item_caption in document_files:
                sent = await context.bot.send_document(
                    chat_id=chat_id,
                    document=document_path,
                    caption=item_caption,
                    parse_mode="HTML",
                    reply_to_message_id=original_msg_id,
                    read_timeout=cfg("TELEGRAM_UPLOAD_TIMEOUT"),
                    write_timeout=cfg("TELEGRAM_UPLOAD_TIMEOUT"),
                    connect_timeout=cfg("TELEGRAM_UPLOAD_TIMEOUT"),
                )
                if sent.document:
                    sent_file_ids.append(sent.document.file_id)

            if i + chunk_size < len(files):
                await asyncio.sleep(cfg("MEDIA_GROUP_DELAY"))

    _stage_log(request_id, "pipeline_send_total", pipeline_started, outcome="partial" if oversized_sizes else "ok", size_bytes=sum(
        os.path.getsize(path) for path in files if os.path.exists(path)
    ))
    if oversized_sizes:
        raise UploadSizeLimitExceeded(
            skipped_count=len(oversized_sizes),
            max_mb=max_upload_mb,
            sent_count=len(sent_file_ids),
        )
    return sent_file_ids


async def send_cached_media_group(
    context: Any,
    chat_id: int,
    file_ids: list[str],
    original_msg_id: int,
    caption: Optional[str] = None,
    media_type: str = "photo",
) -> bool:
    """Send cached Telegram file_ids instantly in 0.1s."""
    try:
        if len(file_ids) == 1:
            fid = file_ids[0]
            kwargs = {
                "chat_id": chat_id,
                "caption": caption,
                "parse_mode": "HTML",
                "reply_to_message_id": original_msg_id,
            }
            if media_type == "video":
                await context.bot.send_video(video=fid, **kwargs)
            elif media_type == "document":
                await context.bot.send_document(document=fid, **kwargs)
            else:
                await context.bot.send_photo(photo=fid, **kwargs)
        else:
            media_group = []
            for i, fid in enumerate(file_ids):
                item_caption = caption if i == 0 else None
                media_cls = InputMediaVideo if media_type == "video" else InputMediaPhoto
                media_group.append(media_cls(media=fid, caption=item_caption, parse_mode='HTML'))
            await context.bot.send_media_group(chat_id=chat_id, media=media_group, reply_to_message_id=original_msg_id)
        return True
    except Exception as e:
        logger.warning(f"Erro ao enviar mídia em cache: {e}")
        return False
