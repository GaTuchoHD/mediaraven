import tempfile
from media_cache import cleanup_and_vacuum_cache
import asyncio
import logging
import os
import shutil
import time

import state
from config import BASE_DOWNLOAD_DIR

logger = logging.getLogger(__name__)

# Max age for temporary download folders (30 minutes)
FOLDER_MAX_AGE_SECONDS = 1800


def _clean_old_temp_folders():
    now = time.time()
    cleaned_count = 0
    total_freed_bytes = 0

    # 1. Clean BASE_DOWNLOAD_DIR
    if os.path.exists(BASE_DOWNLOAD_DIR):
        try:
            active_paths = {os.path.realpath(p) for p in state.active_download_folders}
            for item in os.listdir(BASE_DOWNLOAD_DIR):
                item_path = os.path.join(BASE_DOWNLOAD_DIR, item)
                if os.path.isdir(item_path) and item.startswith("task_"):
                    if os.path.realpath(item_path) in active_paths:
                        continue
                    mtime = os.path.getmtime(item_path)
                    if (now - mtime) > FOLDER_MAX_AGE_SECONDS:
                        try:
                            # Calculate size before deleting
                            for root, _, files in os.walk(item_path):
                                for f in files:
                                    total_freed_bytes += os.path.getsize(os.path.join(root, f))
                            shutil.rmtree(item_path, ignore_errors=True)
                            cleaned_count += 1
                        except Exception as e_del:
                            logger.warning(f"Erro ao remover pasta antiga {item_path}: {e_del}")
        except Exception as e:
            logger.error(f"Erro na limpeza automatica de BASE_DOWNLOAD_DIR: {e}")

    # 2. Clean /tmp temporary test files older than 1 hour
    tmp_dir = tempfile.gettempdir()
    if os.path.exists(tmp_dir):
        try:
            for item in os.listdir(tmp_dir):
                if item.startswith(("test_", "frieren_", "debug_")):
                    item_path = os.path.join(tmp_dir, item)
                    mtime = os.path.getmtime(item_path)
                    if (now - mtime) > 3600:
                        try:
                            if os.path.isdir(item_path):
                                shutil.rmtree(item_path, ignore_errors=True)
                            else:
                                os.remove(item_path)
                            cleaned_count += 1
                        except Exception:
                            pass
        except Exception:
            pass

    cleanup_and_vacuum_cache()
    if cleaned_count > 0:
        mb_freed = total_freed_bytes / (1024 * 1024)
        logger.info(f"🧹 Limpeza periódica: {cleaned_count} pastas/arquivos removidos ({mb_freed:.2f} MB liberados)")


async def periodic_temp_cleanup():
    """Background loop running every 15 minutes to keep disk clean."""
    while True:
        try:
            await asyncio.sleep(900)  # Every 15 minutes
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(state.IO_POOL, _clean_old_temp_folders)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Erro no loop de limpeza periódica: {e}")
