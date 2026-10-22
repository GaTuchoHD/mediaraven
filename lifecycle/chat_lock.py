import asyncio
from contextlib import asynccontextmanager

import state


def get_chat_lock(chat_id: int) -> asyncio.Lock:
    """Retorna o Lock do chat, criando dentro do event loop ativo.

    Armazenado em state.chat_locks (WeakValueDictionary) para que seja coletado
    pelo GC quando nenhum download estiver segurando o lock.
    """
    lock = state.chat_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        state.chat_locks[chat_id] = lock
    return lock


@asynccontextmanager
async def safe_chat_lock(chat_id: int):
    """Context manager que garante a liberação do lock do chat em qualquer cenário."""
    lock = get_chat_lock(chat_id)
    await lock.acquire()
    try:
        yield lock
    finally:
        if lock.locked():
            lock.release()
