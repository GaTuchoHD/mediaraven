"""Estado global mutável compartilhado entre módulos.

Use `import state` + `state.NOME`. NÃO use `from state import NOME`:
variáveis reatribuídas em init_globals/periodic_playwright_refresh ficam
dessincronizadas no namespace do importador.
"""
import asyncio
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor

from config import (
    IG_WORKERS,
    IO_WORKERS,
    MAX_CONCURRENT_REQUESTS,
    PW_CONCURRENCY,
    YTDLP_WORKERS,
)

FIREFOX_COOKIES_CACHE = []

chat_locks: "weakref.WeakValueDictionary[int, asyncio.Lock]" = weakref.WeakValueDictionary()

YTDLP_POOL = ThreadPoolExecutor(max_workers=YTDLP_WORKERS, thread_name_prefix='ytdlp_pool')
IG_POOL = ThreadPoolExecutor(max_workers=IG_WORKERS, thread_name_prefix='ig_pool')
IO_POOL = ThreadPoolExecutor(max_workers=IO_WORKERS, thread_name_prefix='io_pool')

ig_pending_lock = threading.Lock()
ig_pending_count = 0


def ig_pending_inc() -> int:
    global ig_pending_count
    with ig_pending_lock:
        ig_pending_count += 1
        return ig_pending_count


def ig_pending_dec() -> int:
    global ig_pending_count
    with ig_pending_lock:
        ig_pending_count = max(0, ig_pending_count - 1)
        return ig_pending_count


def ig_pending_size() -> int:
    with ig_pending_lock:
        return ig_pending_count


background_tasks = set()
service_tasks: set[asyncio.Task] = set()
active_download_folders: set[str] = set()

REQUEST_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
_request_stats_lock = threading.Lock()
_active_requests = 0
_pending_requests = 0
_last_request_at = 0.0


def request_queued() -> None:
    global _pending_requests, _last_request_at
    with _request_stats_lock:
        _pending_requests += 1
        _last_request_at = time.time()


def request_started() -> None:
    global _active_requests, _pending_requests, _last_request_at
    with _request_stats_lock:
        _pending_requests = max(0, _pending_requests - 1)
        _active_requests += 1
        _last_request_at = time.time()


def request_finished() -> None:
    global _active_requests
    with _request_stats_lock:
        _active_requests = max(0, _active_requests - 1)


def request_cancelled_while_pending() -> None:
    global _pending_requests
    with _request_stats_lock:
        _pending_requests = max(0, _pending_requests - 1)


def request_stats() -> dict[str, float | int]:
    with _request_stats_lock:
        return {
            "active": _active_requests,
            "pending": _pending_requests,
            "last_request_at": _last_request_at,
        }

DENO_PATH = None
FFMPEG_PATH = None
FFPROBE_PATH = None
IG_CLIENT = None

PW_MANAGER = None
PW_BROWSER = None
PW_CONTEXT = None

AIOHTTP_SESSION = None
PW_SEMAPHORE = asyncio.Semaphore(PW_CONCURRENCY)

CFFI_SESSION = None
METRICS_RUNNER = None
METRICS_SITE = None
