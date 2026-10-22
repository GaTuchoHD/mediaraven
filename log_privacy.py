"""Redação defensiva de dados sensíveis antes de gravá-los nos logs."""
from __future__ import annotations

import hashlib
import logging
import re


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_IDENTIFIER_RE = re.compile(
    r"(?P<label>\b(?:"
    r"usu[aá]rio(?:\s+permitido)?(?:\s+telegram)?|"
    r"user(?:\s+id)?|user_id|"
    r"chat(?:\s+id)?|chat_id"
    r")\b)(?P<separator>\s*(?::|=|\bis\b|\bem\b)?\s*)"
    r"(?P<value>-?\d{5,})",
    re.IGNORECASE,
)


def _fingerprint(kind: str, value: str) -> str:
    digest = hashlib.sha256(f"mediaraven:{kind}:{value}".encode()).hexdigest()
    return digest[:10]


def redact_log_message(message: str) -> str:
    """Remove URLs completas e pseudonimiza IDs de usuário/chat."""

    def replace_url(match: re.Match[str]) -> str:
        value = match.group(0)
        trailing = ""
        while value and value[-1] in ".,;!?":
            trailing = value[-1] + trailing
            value = value[:-1]
        return f"[url#{_fingerprint('url', value)}]{trailing}"

    def replace_identifier(match: re.Match[str]) -> str:
        value = match.group("value")
        return (
            f"{match.group('label')}{match.group('separator')}"
            f"id#{_fingerprint('id', value)}"
        )

    redacted = _URL_RE.sub(replace_url, str(message))
    return _IDENTIFIER_RE.sub(replace_identifier, redacted)


class PrivacyFilter(logging.Filter):
    """Aplica a redação depois da interpolação e antes dos handlers."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_log_message(record.getMessage())
            record.args = ()
        except Exception:
            # Falha aberta somente para logging: nunca derruba o bot.
            pass
        return True


class PrivacyFormatter(logging.Formatter):
    """Redige a saída final, incluindo mensagens de exceção e tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            return redact_log_message(super().format(record))
        except Exception:
            return super().format(record)
