"""Garante que o pacote do bot esteja importável nos testes."""
import os
import sys
import tempfile
from pathlib import Path

_TEST_RUNTIME_DIR = tempfile.mkdtemp(prefix="mediaraven-tests-")
os.environ["METRICS_DB_PATH"] = os.path.join(_TEST_RUNTIME_DIR, "metrics.sqlite3")
os.environ["BASE_DOWNLOAD_DIR"] = "/tmp"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
