import logging
import sys

from telegram.error import BadRequest, Forbidden
from telegram.ext import AIORateLimiter, ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from config import (
    LOCAL_API_URL,
    LOCAL_FILE_URL,
    TOKEN,
    setup_logging,
    validate_runtime_config,
)
from handlers import (
    cookies_add_command_handler,
    cookies_status_command_handler,
    status_command_handler,
    whoami_command_handler,
    reset_command_handler,
    caption_callback,
    download_confirm_callback,
    handle_message,
    lang_callback,
    retry_callback,
    screenshot_callback,
)
from lifecycle import init_deno, init_ffmpeg, init_globals, stop_globals
from messages import msg
from version import __build__, __version__

logger = logging.getLogger(__name__)


async def telegram_error_handler(update, context) -> None:
    """Retrieve every PTB exception and keep permanent chat errors concise."""
    error = context.error
    if isinstance(error, (BadRequest, Forbidden)):
        logger.warning("Erro permanente do Telegram tratado: %s", type(error).__name__)
        return
    logger.error(
        "Erro global não tratado do Telegram: %s",
        type(error).__name__,
        exc_info=(type(error), error, error.__traceback__),
    )


if __name__ == '__main__':
    setup_logging()

    if not TOKEN:
        print(msg("startup.token_missing"))
        sys.exit(1)

    config_errors = validate_runtime_config()
    if config_errors:
        for err in config_errors:
            print(f"❌ Config inválida: {err}")
        sys.exit(1)

    init_deno()
    init_ffmpeg()

    logger.info(
        "%s | build=%s",
        msg("startup.connecting", api_url=LOCAL_API_URL, version=__version__),
        __build__,
    )

    builder = ApplicationBuilder().token(TOKEN)
    builder.base_url(LOCAL_API_URL)
    builder.base_file_url(LOCAL_FILE_URL)
    builder.get_updates_connect_timeout(10)
    builder.get_updates_read_timeout(30)
    builder.get_updates_write_timeout(10)
    builder.get_updates_pool_timeout(10)
    builder.local_mode(True)
    builder.rate_limiter(AIORateLimiter(max_retries=3))
    builder.post_init(init_globals)
    builder.post_shutdown(stop_globals)
    application = builder.concurrent_updates(True).build()
    application.add_handler(CommandHandler(['status', 'health'], status_command_handler))
    application.add_handler(CommandHandler(['whoami', 'meuid'], whoami_command_handler))
    application.add_handler(CommandHandler(['cookies_status', 'cookie_status'], cookies_status_command_handler))
    application.add_handler(CommandHandler(['cookies_add', 'cookie_add'], cookies_add_command_handler))
    application.add_handler(CommandHandler(['reset', 'restart'], reset_command_handler))
    application.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & (~filters.COMMAND), handle_message))
    application.add_handler(CallbackQueryHandler(retry_callback, pattern=r"^retry_"))
    application.add_handler(CallbackQueryHandler(lang_callback, pattern=r"^lang\|"))
    application.add_handler(CallbackQueryHandler(caption_callback, pattern=r"^cap\|"))
    application.add_handler(CallbackQueryHandler(screenshot_callback, pattern=r"^scrn\|"))
    application.add_handler(CallbackQueryHandler(download_confirm_callback, pattern=r"^dl\|"))
    application.add_error_handler(telegram_error_handler)

    print(msg("startup.ready"))
    application.run_polling(bootstrap_retries=5)
