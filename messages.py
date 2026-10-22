"""Mensagens user-facing do bot, carregadas de messages.json (fallback: messages.example.json)."""
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_USER_FILE = os.path.join(_BASE_DIR, "messages.json")
_EXAMPLE_FILE = os.path.join(_BASE_DIR, "messages.example.json")
_LOG_USER_FILE = os.path.join(_BASE_DIR, "log_messages.json")
_LOG_EXAMPLE_FILE = os.path.join(_BASE_DIR, "log_messages.example.json")


def _load() -> dict[str, Any]:
    path = _USER_FILE if os.path.exists(_USER_FILE) else _EXAMPLE_FILE
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _flatten_keys(node: Any, prefix: str = "") -> list[str]:
    keys: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else k
            keys.extend(_flatten_keys(v, path))
    else:
        keys.append(prefix)
    return keys


def _validate_against_example(user: dict[str, Any]) -> list[str]:
    if not os.path.exists(_EXAMPLE_FILE) or not os.path.exists(_USER_FILE):
        return []
    try:
        with open(_EXAMPLE_FILE, "r", encoding="utf-8") as f:
            example = json.load(f)
    except Exception as e:
        logger.warning(f"Falha ao ler {_EXAMPLE_FILE} para validação: {e}")
        return []

    example_keys = set(_flatten_keys(example))
    user_keys = set(_flatten_keys(user))
    return sorted(example_keys - user_keys)


_MESSAGES: dict[str, Any] = _load()

_missing = _validate_against_example(_MESSAGES)
if _missing:
    logger.warning(
        f"⚠️ messages.json está faltando {len(_missing)} chave(s) em relação ao example: "
        f"{', '.join(_missing[:10])}" + ("..." if len(_missing) > 10 else "")
    )


def _resolve(key: str) -> Any:
    parts = key.split(".")
    node: Any = _MESSAGES
    for idx, p in enumerate(parts):
        try:
            node = node[p]
        except (KeyError, TypeError) as e:
            path_so_far = ".".join(parts[:idx + 1])
            raise KeyError(
                f"messages key not found: {path_so_far!r} (full key: {key!r})"
            ) from e
    return node


def msg(key: str, **kwargs) -> str:
    node = _resolve(key)
    if kwargs:
        return node.format(**kwargs)
    return node


def msg_list(key: str) -> list:
    return list(_resolve(key))


def _load_log() -> dict[str, Any]:
    path = _LOG_USER_FILE if os.path.exists(_LOG_USER_FILE) else _LOG_EXAMPLE_FILE
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_LOG_MESSAGES: dict[str, Any] = _load_log()


def lmsg(key: str, **kwargs) -> str:
    parts = key.split(".")
    node: Any = _LOG_MESSAGES
    for p in parts:
        try:
            node = node[p]
        except (KeyError, TypeError):
            return f"<<missing log key: {key}>>"
    if not isinstance(node, str):
        return f"<<invalid log key: {key}>>"
    if kwargs:
        try:
            return node.format(**kwargs)
        except (KeyError, IndexError) as e:
            return f"{node} <<format error: {e}>>"
    return node
