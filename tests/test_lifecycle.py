"""Testes dos pedaços puros de lifecycle (sem subir bot)."""
import asyncio

import pytest

import state
from lifecycle.chat_lock import get_chat_lock
from lifecycle.playwright_refresh import _get_process_rss_mb


@pytest.fixture(autouse=True)
def _reset_chat_locks():
    import weakref
    state.chat_locks = weakref.WeakValueDictionary()
    yield
    state.chat_locks = weakref.WeakValueDictionary()


def test_get_chat_lock_returns_same_lock_for_same_id():
    async def run():
        lock_a = get_chat_lock(42)
        lock_b = get_chat_lock(42)
        return lock_a, lock_b
    a, b = asyncio.run(run())
    assert a is b


def test_get_chat_lock_returns_different_lock_for_different_id():
    async def run():
        return get_chat_lock(1), get_chat_lock(2)
    a, b = asyncio.run(run())
    assert a is not b


def test_get_chat_lock_gc_when_no_reference():
    """Quando nenhum caller referencia o lock, ele some da WeakValueDict."""
    import gc
    async def run():
        get_chat_lock(99)  # cria + deixa escapar; temp fica só no weakref
        gc.collect()
        return 99 in state.chat_locks
    assert asyncio.run(run()) is False


def test_get_process_rss_mb_returns_positive_or_none():
    val = _get_process_rss_mb()
    # Em Linux, deve retornar float > 0. Em outros SOs, pode ser None.
    assert val is None or val > 0
