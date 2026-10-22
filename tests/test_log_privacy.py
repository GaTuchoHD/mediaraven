import logging
import sys

from log_privacy import PrivacyFilter, PrivacyFormatter, redact_log_message


def test_redacts_complete_url_and_keeps_only_stable_fingerprint():
    source = "Falha em https://example.com/private/path?token=secret."
    redacted = redact_log_message(source)

    assert "https://" not in redacted
    assert "private" not in redacted
    assert "secret" not in redacted
    assert redacted.startswith("Falha em [url#")
    assert redacted.endswith("].")
    assert redacted == redact_log_message(source)


def test_redacts_contextual_user_and_chat_ids_only():
    redacted = redact_log_message(
        "Usuário permitido 394625529 usou o bot em chat -1001234567890; vídeo 720p"
    )

    assert "394625529" not in redacted
    assert "1001234567890" not in redacted
    assert redacted.count("id#") == 2
    assert "720p" in redacted


def test_logging_filter_redacts_interpolated_arguments():
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1,
        "URL %s para User ID: %s", ("https://x.test/a?b=c", 394625529), None,
    )

    assert PrivacyFilter().filter(record) is True
    assert record.args == ()
    assert "https://" not in record.getMessage()
    assert "394625529" not in record.getMessage()
    assert "User ID: id#" in record.getMessage()
    assert ": :" not in record.getMessage()


def test_privacy_formatter_redacts_exception_traceback():
    try:
        raise RuntimeError(
            "falha em https://example.test/private?token=secret para User ID: 394625529"
        )
    except RuntimeError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1, "download falhou", (), exc_info,
    )
    rendered = PrivacyFormatter("%(levelname)s %(message)s").format(record)

    assert "Traceback" in rendered
    assert "RuntimeError" in rendered
    assert "https://" not in rendered
    assert "private" not in rendered
    assert "secret" not in rendered
    assert "394625529" not in rendered
    assert "[url#" in rendered
    assert "User ID: id#" in rendered
