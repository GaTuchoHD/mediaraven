import asyncio
from datetime import datetime
import html
import logging
import os
import random
import time
import uuid
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

import state
from config import (
    cfg,
    ALLOW_ALL,
    ALLOWED_CHAT_ID,
    ALLOWED_USER_ID,
    ADMIN_USER_ID,
    MAX_QUEUE_WAIT_SECONDS,
    ALLOWED_USERNAMES,
    BASE_DOWNLOAD_DIR,
    request_context,
    should_show_prompt,
)
from downloaders.dispatcher import download_media
from downloaders.fallback import take_page_screenshot
from lifecycle import get_chat_lock
from messages import lmsg, msg, msg_list
from media_cache import delete_cached_media, get_cached_media, save_cached_media
from url_safety import UnsafeURLError, validate_url
from downloaders._caption import CAPTION_MAX, TEXT_MAX
from telegram_io import (
    UploadSizeLimitExceeded,
    infer_media_type,
    is_permanent_telegram_error,
    send_cached_media_group,
    send_downloaded_media,
)
from utils import chunk_html_text, cycle_status_message, is_ignored_domain, safe_cleanup, safe_url

logger = logging.getLogger(__name__)


def _cache_key_for_language(url: str, target_lang: Optional[str]) -> str:
    """Keep audio-language variants separate in the Telegram file-id cache."""
    if not target_lang:
        return url
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("__mediaraven_language", target_lang))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _cached_caption_payload(cached: dict[str, Any]) -> tuple[Optional[str], str]:
    """Retorna (caption_da_midia, texto_separado) para uma entrada do cache."""
    short = str(cached.get("caption_short") or "")
    full = str(cached.get("caption_full") or "")
    if full and len(full) > CAPTION_MAX:
        return None, full
    return (short or full or None), ""


async def _resolve_future_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    store_key: str,
    expired_msg: str,
    wrong_user_msg: str,
) -> None:
    query = update.callback_query
    _, req_key, value = query.data.split('|')

    data = context.bot_data.get(store_key, {}).get(req_key)
    if not data:
        await query.answer(expired_msg, show_alert=True)
        return

    if query.from_user.id != data['user_id']:
        await query.answer(wrong_user_msg, show_alert=True)
        return

    future = data['future']
    if not future.done():
        future.set_result(value)
    await query.answer()


async def download_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _resolve_future_callback(
        update, context, 'dl_futures',
        msg("callback_alerts.dl_expired"),
        msg("callback_alerts.dl_wrong_user"),
    )


async def lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _resolve_future_callback(
        update, context, 'lang_futures',
        msg("callback_alerts.lang_expired"),
        msg("callback_alerts.lang_wrong_user"),
    )


async def caption_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _resolve_future_callback(
        update, context, 'caption_futures',
        msg("callback_alerts.caption_expired"),
        msg("callback_alerts.caption_wrong_user"),
    )


async def screenshot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _resolve_future_callback(
        update, context, 'screenshot_futures',
        msg("callback_alerts.screenshot_expired"),
        msg("callback_alerts.screenshot_wrong_user"),
    )


async def retry_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    retry_id = query.data

    if retry_id not in context.bot_data.get('retries', {}):
        await query.answer(msg("callback_alerts.retry_expired"), show_alert=True)
        await query.edit_message_text(msg("callback_alerts.retry_expired"))
        return

    retry_data = context.bot_data['retries'][retry_id]

    if query.from_user.id != retry_data.get('user_id'):
        await query.answer(msg("callback_alerts.retry_wrong_user"), show_alert=True)
        return

    context.bot_data['retries'].pop(retry_id)
    await query.answer()
    await query.message.delete()
    request_context.set((query.message.chat_id, retry_data['user_id']))
    await _bounded_media_request(
        context, query.message.chat_id, retry_data['msg_id'], retry_data['url'],
        target_lang=retry_data.get('target_lang'),
        user_id=retry_data['user_id'], is_retry=True,
    )


async def _ask_via_future(
    context: ContextTypes.DEFAULT_TYPE,
    store_key: str,
    req_key: str,
    markup: InlineKeyboardMarkup,
    user_id: Optional[int],
    timeout: float,
    default: str,
) -> str:
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    context.bot_data.setdefault(store_key, {})[req_key] = {'future': future, 'user_id': user_id}
    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        return default
    finally:
        context.bot_data[store_key].pop(req_key, None)


def _yes_no_markup(prefix: str, req_key: str, yes_label: str, no_label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(yes_label, callback_data=f"{prefix}|{req_key}|yes"),
        InlineKeyboardButton(no_label, callback_data=f"{prefix}|{req_key}|no"),
    ]])


async def _ask_download_confirmation(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    suffix: str,
    user_id: Optional[int],
    idx: int,
) -> tuple[str, Any]:
    req_key = f"dl_{message_id}_{idx}"
    markup = _yes_no_markup(
        "dl", req_key,
        msg("buttons.download_yes"), msg("buttons.download_no"),
    )
    status_msg = await context.bot.send_message(
        chat_id,
        msg("prompts.link_detected", suffix=suffix),
        parse_mode='HTML',
        reply_markup=markup,
        reply_to_message_id=message_id,
        disable_web_page_preview=True,
    )
    choice = await _ask_via_future(
        context, 'dl_futures', req_key, markup, user_id,
        timeout=cfg("ASK_DL_TIMEOUT"), default=cfg("ASK_DL_DEFAULT"),
    )
    return choice, status_msg


async def _ask_language_choice(
    context: ContextTypes.DEFAULT_TYPE,
    status_msg: Any,
    message_id: int,
    lang_buttons: list,
    suffix: str,
    user_id: Optional[int],
    idx: int,
) -> str:
    req_key = f"{message_id}_{idx}"
    buttons = [
        InlineKeyboardButton(label, callback_data=f"lang|{req_key}|{code}")
        for code, label in lang_buttons
    ]
    keyboard = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    markup = InlineKeyboardMarkup(keyboard)
    await status_msg.edit_text(
        msg("prompts.language_detected", suffix=suffix),
        parse_mode='HTML',
        reply_markup=markup,
    )
    return await _ask_via_future(
        context, 'lang_futures', req_key, markup, user_id,
        timeout=cfg("ASK_LANG_TIMEOUT"), default='original',
    )


async def _ask_caption_inclusion(
    context: ContextTypes.DEFAULT_TYPE,
    status_msg: Any,
    message_id: int,
    suffix: str,
    user_id: Optional[int],
    idx: int,
    timeout: Optional[float] = None,
    default: Optional[str] = None,
) -> bool:
    if timeout is None:
        timeout = cfg("ASK_CAPTION_TIMEOUT")
    if default is None:
        default = cfg("ASK_CAPTION_DEFAULT")
    req_key = f"cap_{message_id}_{idx}"
    markup = _yes_no_markup(
        "cap", req_key,
        msg("buttons.caption_yes"), msg("buttons.caption_no"),
    )
    await status_msg.edit_text(
        msg("prompts.caption_found", suffix=suffix),
        parse_mode='HTML',
        reply_markup=markup,
    )
    choice = await _ask_via_future(
        context, 'caption_futures', req_key, markup, user_id,
        timeout=timeout, default=default,
    )
    return choice == 'yes'


async def _ask_screenshot_offer(
    context: ContextTypes.DEFAULT_TYPE,
    status_msg: Any,
    message_id: int,
    suffix: str,
    user_id: Optional[int],
    idx: int,
) -> bool:
    req_key = f"scrn_{message_id}_{idx}"
    markup = _yes_no_markup(
        "scrn", req_key,
        msg("buttons.screenshot_yes"), msg("buttons.screenshot_no"),
    )
    await status_msg.edit_text(
        msg("prompts.screenshot_offer", suffix=suffix),
        parse_mode='HTML',
        reply_markup=markup,
    )
    choice = await _ask_via_future(
        context, 'screenshot_futures', req_key, markup, user_id,
        timeout=cfg("ASK_SCREENSHOT_TIMEOUT"), default=cfg("ASK_SCREENSHOT_DEFAULT"),
    )
    return choice == 'yes'


async def _try_screenshot_offer(
    context: ContextTypes.DEFAULT_TYPE,
    status_msg: Any,
    message_id: int,
    suffix: str,
    user_id: Optional[int],
    idx: int,
    unique_folder: Optional[str],
    url: str,
    is_retry: bool,
) -> list:
    if is_retry or cfg("SCRAPE_SCREENSHOT_FALLBACK") != "yes" or unique_folder is None:
        return []
    try:
        wants = await _ask_screenshot_offer(context, status_msg, message_id, suffix, user_id, idx)
    except Exception as e:
        logger.debug(lmsg("handlers.falha_no_prompt", e=e))
        return []
    if not wants:
        return []
    await _safe_edit(status_msg, msg("status.screenshot_taking", suffix=suffix), parse_mode='HTML')
    shot = await take_page_screenshot(os.path.join(unique_folder, "content"), url)
    if shot:
        return [shot]
    logger.warning(lmsg("handlers.screenshot_falhou_para", url=url))
    return []


async def _acquire_chat_lock(status_msg: Any, chat_id: int, suffix: str) -> asyncio.Lock:
    lock = get_chat_lock(chat_id)
    if lock.locked():
        try:
            await status_msg.edit_text(msg("status.queue_busy", suffix=suffix))
        except Exception as e:
            logger.debug(lmsg("handlers.falha_ao_atualizar", e=e))
    await lock.acquire()
    return lock


async def _safe_edit(status_msg: Any, text: str, **kwargs) -> None:
    try:
        await status_msg.edit_text(text, **kwargs)
    except Exception as e:
        logger.debug(lmsg("handlers.falha_silenciosa_em", e=e))


async def _safe_delete(status_msg: Any) -> None:
    try:
        await status_msg.delete()
    except Exception as e:
        logger.debug(lmsg("handlers.falha_silenciosa_em_2", e=e))


def _build_suffix(idx: int, total: int, target_lang: Optional[str]) -> str:
    suffix = f" (Link {idx}/{total})" if total > 1 else ""
    if target_lang:
        suffix += f" [Idioma: {target_lang.upper()}]"
    return suffix


async def _initial_status_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    suffix: str,
    user_id: Optional[int],
    idx: int,
    skip_confirm: bool,
) -> Optional[Any]:
    prompt_enabled = should_show_prompt("download", chat_id, user_id or 0)
    if skip_confirm or not prompt_enabled:
        try:
            return await context.bot.send_message(
                chat_id,
                msg("status.queue_busy", suffix=suffix),
                reply_to_message_id=message_id,
            )
        except BadRequest as exc:
            if "message to be replied not found" not in str(exc).casefold():
                raise
            logger.info("Mensagem original ausente; enviando status sem reply.")
            return await context.bot.send_message(
                chat_id,
                msg("status.queue_busy", suffix=suffix),
            )

    choice, status_msg = await _ask_download_confirmation(
        context, chat_id, message_id, suffix, user_id, idx
    )
    if choice == 'no':
        await _safe_edit(status_msg, msg("status.download_ignored", suffix=suffix))
        return None
    await _safe_edit(status_msg, msg("status.queue_busy", suffix=suffix))
    return status_msg


async def _register_retry_and_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    status_msg: Any,
    message_id: int,
    idx: int,
    url: str,
    user_id: Optional[int],
    target_lang: Optional[str],
    suffix: str,
) -> None:
    retry_id = f"retry_{message_id}_{idx}"
    context.bot_data['retries'][retry_id] = {
        'url': url, 'msg_id': message_id, 'user_id': user_id,
        'target_lang': target_lang,
    }
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton(msg("buttons.retry"), callback_data=retry_id)]]
    )
    text = f"{msg('downloader_status.generic_fail')}{suffix}"
    await _safe_edit(status_msg, text, parse_mode='HTML', reply_markup=markup)


async def _send_text_in_chunks(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    full_text: str,
    *,
    show_preview: bool,
    reply_to_first: bool = True,
) -> None:
    chunks = chunk_html_text(full_text, TEXT_MAX)
    if not chunks:
        return
    delay = cfg("MEDIA_GROUP_DELAY")
    last_idx = len(chunks) - 1
    for i, chunk in enumerate(chunks):
        await context.bot.send_message(
            chat_id=chat_id,
            text=chunk,
            parse_mode='HTML',
            reply_to_message_id=message_id if (reply_to_first and i == 0) else None,
            disable_web_page_preview=not (show_preview and i == last_idx),
        )
        if i < last_idx:
            await asyncio.sleep(delay)


async def _send_files_and_cleanup_status(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    status_msg: Any,
    files: list,
    desc_short: str,
    desc_full: str,
    suffix: str,
    user_id: Optional[int],
    idx: int,
    is_article: bool = False,
    cache_url: Optional[str] = None,
    request_id: Optional[str] = None,
) -> list[str]:
    include_text = False
    if desc_short:
        timeout = cfg("ASK_ARTICLE_TIMEOUT") if is_article else cfg("ASK_CAPTION_TIMEOUT")
        default = cfg("ASK_ARTICLE_DEFAULT") if is_article else cfg("ASK_CAPTION_DEFAULT")
        if should_show_prompt("caption", chat_id, user_id or 0):
            include_text = await _ask_caption_inclusion(
                context, status_msg, message_id, suffix, user_id, idx,
                timeout=timeout, default=default,
            )
        elif default == 'yes':
            include_text = True

    await _safe_edit(status_msg, msg("status.sending", count=len(files), suffix=suffix))

    upload_kwargs = {
        'read_timeout': cfg("TELEGRAM_UPLOAD_TIMEOUT"),
        'write_timeout': cfg("TELEGRAM_UPLOAD_TIMEOUT"),
        'parse_mode': 'HTML',
        'reply_to_message_id': message_id,
    }

    text_in_chunks = include_text and desc_full and len(desc_full) > CAPTION_MAX
    caption_on_media = desc_short if (include_text and not text_in_chunks) else None

    send_started = time.monotonic()
    try:
        sent_fids = await send_downloaded_media(
            context, chat_id, files, message_id, upload_kwargs,
            caption=caption_on_media, request_id=request_id,
        )
    except UploadSizeLimitExceeded as exc:
        logger.info(
            "pipeline request_id=%s stage=delivery outcome=%s elapsed_ms=%d files=%d sent=%d skipped=%d max_upload_mb=%d",
            request_id or "unknown",
            "partial" if exc.sent_count else "too_large",
            round((time.monotonic() - send_started) * 1000),
            len(files),
            exc.sent_count,
            exc.skipped_count,
            exc.max_mb,
        )
        raise
    except Exception as exc:
        logger.warning(
            "pipeline request_id=%s stage=delivery outcome=error elapsed_ms=%d files=%d sent=0 error_type=%s",
            request_id or "unknown",
            round((time.monotonic() - send_started) * 1000),
            len(files),
            type(exc).__name__,
        )
        raise
    logger.info(
        "pipeline request_id=%s stage=delivery outcome=ok elapsed_ms=%d files=%d sent=%d",
        request_id or "unknown",
        round((time.monotonic() - send_started) * 1000),
        len(files),
        len(sent_fids),
    )

    # Cache only a single, typed file. Mixed Telegram albums need a per-item
    # type schema and are deliberately not cached to avoid replaying as photos.
    if cache_url and len(files) == 1 and len(sent_fids) == 1:
        await asyncio.to_thread(
            save_cached_media,
            cache_url,
            infer_media_type(files[0]),
            sent_fids,
            desc_short if include_text else "",
            desc_full if include_text else "",
        )

    if text_in_chunks:
        await _send_text_in_chunks(
            context, chat_id, message_id, desc_full,
            show_preview=False, reply_to_first=False,
        )

    await _safe_delete(status_msg)
    return sent_fids


async def process_media_request(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    url: str,
    target_lang: Optional[str] = None,
    request_id: Optional[str] = None,
    idx: int = 1,
    total: int = 1,
    user_id: Optional[int] = None,
    is_retry: bool = False,
) -> None:
    request_id = request_id or uuid.uuid4().hex[:12]
    request_started = time.monotonic()
    request_context.set((chat_id, user_id))
    suffix = _build_suffix(idx, total, target_lang)
    status_msg: Optional[Any] = None
    status_task: Optional[asyncio.Task] = None
    unique_folder: Optional[str] = None
    lock: Optional[asyncio.Lock] = None
    lock_acquired = False

    try:
        skip_confirm = is_retry or bool(target_lang)
        status_msg = await _initial_status_message(
            context, chat_id, message_id, suffix, user_id, idx, skip_confirm,
        )
        if status_msg is None:
            return

        lock = await _acquire_chat_lock(status_msg, chat_id, suffix)
        lock_acquired = True

        cache_key = _cache_key_for_language(url, target_lang)
        cached = await asyncio.to_thread(get_cached_media, cache_key)
        if cached and len(cached.get("file_ids") or []) == 1:
            await _safe_edit(status_msg, f"⚡ Enviando do cache...{suffix}")
            cached_caption, cached_text = _cached_caption_payload(cached)
            cache_sent = await send_cached_media_group(
                context,
                chat_id,
                cached["file_ids"],
                message_id,
                caption=cached_caption,
                media_type=cached.get("media_type") or "photo",
            )
            if cache_sent:
                if cached_text:
                    await _send_text_in_chunks(
                        context, chat_id, message_id, cached_text,
                        show_preview=False, reply_to_first=False,
                    )
                logger.info("⚡ Mídia entregue pelo cache Telegram.")
                await _safe_delete(status_msg)
                return
            await asyncio.to_thread(delete_cached_media, cache_key)

        await _safe_edit(status_msg, msg("status.downloading", suffix=suffix), parse_mode='HTML')
        status_task = asyncio.create_task(cycle_status_message(status_msg, suffix))

        unique_folder = os.path.join(BASE_DOWNLOAD_DIR, f"task_{chat_id}_{message_id}_{idx}")
        link_folder = os.path.join(unique_folder, "content")
        state.active_download_folders.add(unique_folder)

        detect_lang = should_show_prompt("lang", chat_id, user_id or 0)
        download_started = time.monotonic()
        try:
            files, status_or_error, desc_short, desc_full, is_article = await download_media(
                url, link_folder, target_lang, detect_languages=detect_lang,
            )
        except asyncio.CancelledError:
            logger.info(
                "pipeline request_id=%s stage=download outcome=cancelled elapsed_ms=%d files=na",
                request_id,
                round((time.monotonic() - download_started) * 1000),
            )
            raise
        except Exception as exc:
            logger.warning(
                "pipeline request_id=%s stage=download outcome=error elapsed_ms=%d files=na error_type=%s",
                request_id,
                round((time.monotonic() - download_started) * 1000),
                type(exc).__name__,
            )
            raise
        logger.info(
            "pipeline request_id=%s stage=download outcome=%s elapsed_ms=%d files=%d",
            request_id,
            "ok" if files else "text_only" if (desc_full or desc_short) else "no_files",
            round((time.monotonic() - download_started) * 1000),
            len(files),
        )

        if status_task is not None:
            status_task.cancel()
            await asyncio.gather(status_task, return_exceptions=True)
            status_task = None

        if status_or_error == "MULTILANG":
            lock.release()
            lock_acquired = False

            chosen_lang = await _ask_language_choice(
                context, status_msg, message_id, files, suffix, user_id, idx
            )
            await _safe_delete(status_msg)

            return await process_media_request(
                context, chat_id, message_id, url,
                target_lang=chosen_lang,
                request_id=request_id, idx=idx, total=total,
                user_id=user_id, is_retry=is_retry,
            )

        if status_or_error:
            outcome = "✅" if (files or desc_full or desc_short) else "❌"
            logger.info(lmsg("handlers.x_x_x", outcome=outcome, status_or_error=status_or_error, arg0=safe_url(url)))

        if not files and (desc_full or desc_short):
            await _send_text_in_chunks(
                context, chat_id, message_id, desc_full or desc_short,
                show_preview=True, reply_to_first=True,
            )
            await _safe_delete(status_msg)
            return

        if not files:
            shot_files = await _try_screenshot_offer(
                context, status_msg, message_id, suffix, user_id, idx,
                unique_folder, url, is_retry,
            )
            if shot_files:
                files = shot_files
            else:
                if is_retry:
                    await _safe_edit(
                        status_msg,
                        msg("status.retry_failed", suffix=suffix),
                        parse_mode='HTML',
                    )
                else:
                    await _register_retry_and_prompt(
                        context, status_msg, message_id, idx, url, user_id, target_lang,
                        suffix,
                    )
                return

        await _send_files_and_cleanup_status(
            context, chat_id, message_id, status_msg, files, desc_short, desc_full,
            suffix, user_id, idx, is_article=is_article, cache_url=cache_key,
            request_id=request_id,
        )

    except UploadSizeLimitExceeded as exc:
        request_outcome = "partial_upload" if exc.sent_count else "upload_too_large"
        request_error_type = type(exc).__name__
        if status_msg is not None:
            key = (
                "status.upload_too_large_partial"
                if exc.sent_count
                else "status.upload_too_large"
            )
            await _safe_edit(
                status_msg,
                msg(
                    key,
                    count_label=(
                        f"{exc.skipped_count} arquivo"
                        if exc.skipped_count == 1
                        else f"{exc.skipped_count} arquivos"
                    ),
                    max_mb=exc.max_mb,
                    suffix=suffix,
                ),
            )
    except (BadRequest, Forbidden) as exc:
        if is_permanent_telegram_error(exc):
            logger.warning(
                "Telegram recusou permanentemente a entrega (%s); tarefa encerrada sem retry.",
                type(exc).__name__,
            )
        else:
            logger.warning("Telegram rejeitou a operação: %s", type(exc).__name__)
    except Exception:
        logger.exception("⚠️ Erro ao processar link")
        if status_msg is not None:
            await _safe_edit(status_msg, msg("status.internal_error", suffix=suffix))

    finally:
        logger.info(
            "pipeline request_id=%s stage=request_total outcome=finished elapsed_ms=%d",
            request_id,
            round((time.monotonic() - request_started) * 1000),
        )
        if status_task is not None:
            status_task.cancel()
            await asyncio.gather(status_task, return_exceptions=True)
        if unique_folder is not None:
            state.active_download_folders.discard(unique_folder)
            await safe_cleanup(unique_folder)
        if lock_acquired and lock is not None:
            lock.release()


async def _bounded_media_request(*args, _reserved: bool = False, **kwargs) -> None:
    """Bound active downloads, pending work, and time spent waiting for capacity."""
    started = False
    acquired = False
    if not _reserved and not state.try_request_queued():
        logger.warning("Solicitação recusada: fila de downloads esgotada")
        if len(args) >= 2:
            await args[0].bot.send_message(args[1], "⚠️ Fila de downloads cheia. Tente novamente.")
        return
    try:
        await asyncio.wait_for(state.REQUEST_SEMAPHORE.acquire(), timeout=MAX_QUEUE_WAIT_SECONDS)
        acquired = True
        state.request_started()
        started = True
        await process_media_request(*args, **kwargs)
    except asyncio.TimeoutError:
        logger.warning("Solicitação expirou aguardando vaga na fila de downloads")
    except Exception:
        # This is a last-resort supervisor. process_media_request normally
        # handles delivery errors itself, but no task exception may go unseen.
        logger.exception("Falha não tratada no supervisor de download")
    finally:
        if acquired:
            state.REQUEST_SEMAPHORE.release()
        if started:
            state.request_finished()
        else:
            state.request_cancelled_while_pending()


def _media_task_done(task: asyncio.Task) -> None:
    state.background_tasks.discard(task)
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.error(
            "Tarefa de mídia terminou com exceção não recuperada: %s",
            type(exc).__name__,
            exc_info=(type(exc), exc, exc.__traceback__),
        )


def _extract_urls_from_update(update: Update) -> list[str]:
    if not update.message:
        return []

    if update.message.text:
        entities = update.message.parse_entities(types=['url', 'text_link'])
    elif update.message.caption:
        entities = update.message.parse_caption_entities(types=['url', 'text_link'])
    else:
        entities = {}

    urls: list[str] = []
    for entity, extracted_text in entities.items():
        if entity.type == 'text_link':
            candidate = entity.url
        elif entity.type == 'url':
            if extracted_text.startswith('@'):
                continue
            candidate = extracted_text
            if not candidate.startswith(('http://', 'https://')):
                candidate = f"https://{candidate}"
        else:
            continue

        if not candidate or not candidate.lower().startswith(('http://', 'https://')):
            continue
        urls.append(candidate)

    deduped = list(dict.fromkeys(urls))

    ignored = cfg("IGNORED_DOMAINS")
    if ignored:
        kept: list[str] = []
        for candidate in deduped:
            if is_ignored_domain(candidate, ignored):
                logger.info(lmsg("handlers.dominio_ignorado", url=safe_url(candidate)))
            else:
                kept.append(candidate)
        deduped = kept

    if len(deduped) > cfg("MAX_URLS_PER_MESSAGE"):
        logger.warning(lmsg(
            "handlers.urls_exceed_limit",
            n=len(deduped), max=cfg("MAX_URLS_PER_MESSAGE"),
        ))
        return deduped[:cfg("MAX_URLS_PER_MESSAGE")]
    return deduped


def _is_allowed_user(update: Update) -> bool:
    user = getattr(update, "effective_user", None)
    if not user:
        return False

    username = (getattr(user, "username", None) or "").lstrip("@").casefold()
    return getattr(user, "id", 0) in ALLOWED_USER_ID or bool(
        username and username in ALLOWED_USERNAMES
    )


def _is_admin_update(update: Update) -> bool:
    """Administrative privileges are never inherited from a group allowlist."""
    user = getattr(update, "effective_user", None)
    return bool(user and user.id in ADMIN_USER_ID)


def _is_authorized_update(update: Update) -> bool:
    effective_chat = getattr(update, "effective_chat", None)
    chat_id = effective_chat.id if effective_chat else 0
    return (
        ALLOW_ALL is True
        or str(ALLOW_ALL).casefold() == "yes"
        or _is_allowed_user(update)
        or chat_id in ALLOWED_CHAT_ID
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0
    request_context.set((chat_id, user_id))

    is_allowed_user = _is_allowed_user(update)
    is_whitelisted_chat = chat_id in ALLOWED_CHAT_ID
    if not _is_authorized_update(update):
        return

    urls = _extract_urls_from_update(update)
    if not urls:
        return

    if is_allowed_user and not is_whitelisted_chat:
        logger.info(lmsg(
            "handlers.allowed_user_outside_whitelist",
            user_id=user_id, chat_id=chat_id, n=len(urls),
        ))

    try:
        reactions = msg_list("reactions")
        if reactions:
            await update.message.set_reaction(reaction=random.choice(reactions))
    except Exception as e:
        logger.debug(lmsg("handlers.falha_ao_setar", e=e))

    safe_urls = []
    for url in urls:
        try:
            safe_urls.append(await validate_url(url))
        except UnsafeURLError as exc:
            logger.warning("URL bloqueada pela política de rede (%s): %s", type(exc).__name__, safe_url(url))

    total_urls = len(safe_urls)
    for idx, url in enumerate(safe_urls, start=1):
        if not state.try_request_queued():
            await update.message.reply_text("⚠️ Fila de downloads cheia. Tente novamente.")
            break
        try:
            task = asyncio.create_task(
                _bounded_media_request(
                    context, chat_id, update.message.id, url,
                    user_id=user_id, idx=idx, total=total_urls, _reserved=True,
                )
            )
        except BaseException:
            state.request_cancelled_while_pending()
            raise
        state.background_tasks.add(task)
        task.add_done_callback(_media_task_done)


async def status_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Telegram Admin Diagnostic Command (/status or /health)."""
    user_id = update.effective_user.id if update.effective_user else 0

    # Check permissions
    if not _is_admin_update(update):
        await update.message.reply_text("⛔ Comando restrito ao Administrador.")
        return

    from metrics_server import _get_system_stats
    stats = _get_system_stats()

    uptime_s = stats.get("uptime_seconds", 0)
    hours = uptime_s // 3600
    mins = (uptime_s % 3600) // 60

    disk = stats.get("disk", {})
    ram = stats.get("ram", {})

    status_msg = (
        "🛠️ <b>Diagnóstico do MediaRaven</b>\n\n"
        f"⏱ <b>Uptime:</b> {hours}h {mins}m\n"
        f"💾 <b>Disco:</b> {disk.get('pct_used', '?')}% usado ({disk.get('free_gb', '?')} GB livre de {disk.get('total_gb', '?')} GB)\n"
        f"🧠 <b>Memória RAM:</b> {ram.get('pct_used', '?')}% usada ({ram.get('avail_mb', '?')} MB livre)\n"
        f"⚡ <b>Cache SQLite:</b> {stats.get('cache_entries', 0)} itens ativos\n"
        f"📦 <b>yt-dlp:</b> v{stats.get('ytdlp_version', 'N/A')}\n\n"
        "⚙️ <b>Componentes:</b>\n"
        f"  • FFmpeg: {'✅ Sim' if stats.get('ffmpeg') else '❌ Não'}\n"
        f"  • JS Engine (Node/Deno): {'✅ Sim' if stats.get('deno') else '❌ Não'}\n"
        f"  • Playwright Chromium: {'✅ Sim' if stats.get('playwright') else '❌ Não'}\n\n"
        "📊 <i>Métricas e logs: endpoint interno do container (não exposto na LAN)</i>"
    )
    await update.message.reply_text(status_msg, parse_mode="HTML")


async def cookies_status_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Report Firefox cookie health without exposing names, values or tokens."""
    user_id = update.effective_user.id if update.effective_user else 0

    if not _is_admin_update(update):
        await update.message.reply_text("⛔ Comando restrito ao Administrador.")
        return

    from cookies import get_firefox_cookie_status, get_reddit_cookie_status

    status = get_firefox_cookie_status()
    reddit_status = get_reddit_cookie_status()
    cache_count = len(state.FIREFOX_COOKIES_CACHE)
    safe_path = html.escape(status['path'])

    if not status['exists']:
        text = (
            "🍪 <b>Status dos cookies</b>\n\n"
            "❌ <b>Banco Firefox:</b> não encontrado\n"
            f"📂 <b>Caminho esperado:</b> <code>{safe_path}</code>\n"
            f"⚡ <b>Cache em memória:</b> {cache_count} cookies\n\n"
            "O cadastro do comando no BotFather apenas cria o atalho do menu; "
            "os cookies ainda precisam ser colocados no caminho acima."
        )
    elif not status['readable']:
        error = html.escape(status.get('error') or 'erro desconhecido')
        text = (
            "🍪 <b>Status dos cookies</b>\n\n"
            "⚠️ <b>Banco Firefox:</b> encontrado, mas não pôde ser lido\n"
            f"📂 <b>Caminho:</b> <code>{safe_path}</code>\n"
            f"🧩 <b>Erro:</b> {error}\n"
            f"⚡ <b>Cache em memória:</b> {cache_count} cookies"
        )
    else:
        try:
            modified = datetime.fromtimestamp(status['modified_at']).astimezone().strftime('%d/%m/%Y %H:%M:%S %Z')
        except (OSError, OverflowError, ValueError):
            modified = "data indisponível"
        platform_lines = [
            f"  • {name}: {count}"
            for name, count in status['platforms'].items()
            if count
        ]
        platforms = "\n".join(platform_lines) if platform_lines else "  • Nenhuma plataforma conhecida"
        if reddit_status["readable"]:
            platforms += (
                "\n  • Reddit (arquivo dedicado): "
                f"{reddit_status['active']} ativos / {reddit_status['expired']} expirados"
            )
        reddit_ready = reddit_status["readable"] and reddit_status["active"] > 0
        readiness = (
            "✅ Prontos para uso"
            if status['active'] or reddit_ready
            else "⚠️ Nenhum cookie ativo"
        )
        platform_readiness = dict(status.get('readiness', {}))
        platform_readiness["Reddit"] = reddit_ready
        readiness_lines = [
            f"  • {name}: {'✅ pronto' if ready else '⚠️ incompleto'}"
            for name, ready in platform_readiness.items()
        ]
        readiness_detail = "\n".join(readiness_lines)
        expiring_soon = status.get('expiring_soon', 0)
        text = (
            "🍪 <b>Status dos cookies</b>\n\n"
            f"<b>{readiness}</b>\n"
            "✅ <b>Banco Firefox:</b> legível\n"
            f"📦 <b>Total:</b> {status['total']}\n"
            f"🟢 <b>Ativos:</b> {status['active']}\n"
            f"🔴 <b>Expirados:</b> {status['expired']}\n"
            f"🕐 <b>De sessão:</b> {status['session']}\n"
            f"⏳ <b>Expiram em até 7 dias:</b> {expiring_soon}\n"
            f"🌐 <b>Domínios:</b> {status['domains']}\n"
            f"⚡ <b>Cache em memória:</b> {cache_count}\n"
            f"📝 <b>Última alteração:</b> {modified}\n\n"
            "<b>Cookies por plataforma:</b>\n"
            f"{platforms}"
            + (f"\n\n<b>Prontidão de autenticação:</b>\n{readiness_detail}" if readiness_detail else "")
        )

    logger.info("Comando /cookies_status consultado pelo usuário %s", user_id)
    await update.message.reply_text(text, parse_mode="HTML")


async def whoami_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Return the caller's numeric Telegram ID for allowlist enrollment."""
    if not update.effective_user or not update.effective_chat:
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("Use /whoami em conversa privada com o bot.")
        return
    username = html.escape(update.effective_user.username or "sem username")
    await update.message.reply_text(
        "🪪 <b>Identidade Telegram</b>\n\n"
        f"• User ID: <code>{update.effective_user.id}</code>\n"
        f"• Username: <code>@{username}</code>",
        parse_mode="HTML",
    )


async def cookies_add_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Install platform cookies from an authorized Telegram command."""
    if not _is_admin_update(update):
        await update.message.reply_text("⛔ Comando restrito ao Administrador.")
        return

    if not update.effective_chat or update.effective_chat.type != 'private':
        await update.message.reply_text(
            "⛔ Por segurança, /cookies_add funciona somente em conversa privada."
        )
        return

    args = context.args or []
    platform = args[0] if args else ''
    raw_cookie = ' '.join(args[1:]).strip() if len(args) > 1 else ''
    secret_message = update.effective_message

    if not raw_cookie and update.effective_message.reply_to_message:
        replied = update.effective_message.reply_to_message
        raw_cookie = (replied.text or replied.caption or '').strip()
        secret_message = replied

    if not platform or not raw_cookie:
        await update.message.reply_text(
            "🍪 <b>Adicionar cookies</b>\n\n"
            "Responda à mensagem privada que contém o cookie usando:\n"
            "<code>/cookies_add x</code> ou <code>/cookies_add instagram</code>\n\n"
            "Também é possível usar:\n"
            "<code>/cookies_add instagram nome=valor; nome2=valor2</code>\n\n"
            "Também é possível responder à mensagem que contém o cookie usando "
            "somente o comando da plataforma.",
            parse_mode="HTML",
        )
        return

    from cookies import extract_firefox_cookies, install_raw_cookies

    instagram_reloaded = None
    try:
        result = install_raw_cookies(platform, raw_cookie)
        state.FIREFOX_COOKIES_CACHE = extract_firefox_cookies()
        if state.PW_CONTEXT:
            if result['platform'] == 'Instagram':
                accepted_domains = {'instagram.com'}
            else:
                accepted_domains = {'x.com', 'twitter.com'}
            platform_cookies = [
                cookie for cookie in state.FIREFOX_COOKIES_CACHE
                if cookie['domain'].lstrip('.').lower() in accepted_domains
            ]
            if platform_cookies:
                await state.PW_CONTEXT.add_cookies(platform_cookies)
        if result['platform'] == 'Instagram':
            from lifecycle.instagram_login import init_instagrapi_async
            instagram_reloaded = await init_instagrapi_async()
    except ValueError as exc:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ {html.escape(str(exc))}",
            parse_mode="HTML",
        )
        return
    except Exception as exc:
        logger.error(
            "Falha segura no comando /cookies_add: %s", type(exc).__name__
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Não foi possível instalar os cookies. Nenhum valor foi registrado no log.",
        )
        return

    instagram_status = ""
    if instagram_reloaded is True:
        instagram_status = "\n🔐 <b>Instagrapi:</b> reautenticado com o novo cookie"
    elif instagram_reloaded is False:
        instagram_status = (
            "\n⚠️ <b>Instagrapi:</b> o novo cookie não foi aceito; "
            "o cliente anterior foi preservado"
        )

    confirmation = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "✅ <b>Cookies instalados</b>\n\n"
            f"🌐 <b>Plataforma:</b> {html.escape(str(result['platform']))}\n"
            f"📦 <b>Quantidade:</b> {result['installed']}\n"
            "⚡ <b>Cache:</b> recarregado sem reiniciar o MediaRaven"
            f"{instagram_status}"
        ),
        parse_mode="HTML",
    )

    deleted = True
    messages = [secret_message]
    if update.effective_message is not secret_message:
        messages.append(update.effective_message)
    for message in messages:
        try:
            await message.delete()
        except Exception:
            deleted = False

    if not deleted:
        await confirmation.edit_text(
            confirmation.text_html
            + "\n\n⚠️ O Telegram não permitiu apagar a mensagem recebida; apague-a manualmente para ambos.",
            parse_mode="HTML",
        )

    logger.info(
        "Comando /cookies_add concluiu a instalação de %d cookies para %s pelo usuário %s",
        result['installed'],
        result['platform'],
        update.effective_user.id if update.effective_user else 0,
    )


async def reset_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Telegram Admin Restart Command (/reset or /restart)."""
    user_id = update.effective_user.id if update.effective_user else 0

    if not _is_admin_update(update):
        await update.message.reply_text("⛔ Comando restrito ao Administrador.")
        return

    await update.message.reply_text("🔄 <b>Reiniciando o MediaRaven...</b>\nO bot estará online novamente em 3 segundos.", parse_mode="HTML")
    logger.info(f"🔄 Comando /reset acionado pelo Admin (User ID: {user_id}). Reiniciando processo...")

    # Stop polling gracefully. The Docker restart policy handles process recreation.
    async def _do_restart():
        await asyncio.sleep(1)
        context.application.stop_running()

    asyncio.create_task(_do_restart())
