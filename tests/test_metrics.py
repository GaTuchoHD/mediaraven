"""Testes do módulo de métricas."""
import sqlite3

import metrics


def setup_function():
    # Reset do estado global entre testes.
    metrics._metrics.platforms.clear()
    metrics._metrics.last_success_at = 0.0
    metrics._metrics.last_failure_at = 0.0
    metrics._metrics.last_success_platform = ""
    metrics._metrics.last_failure_platform = ""
    if metrics._persistence_ready:
        with sqlite3.connect(metrics._METRICS_DB_PATH) as conn:
            conn.execute("DELETE FROM download_metrics")


def test_record_success_increments():
    metrics.record_success('youtube', 1.5)
    metrics.record_success('youtube', 2.5)
    snap = metrics.snapshot()
    assert snap['platforms']['youtube']['success'] == 2
    assert snap['platforms']['youtube']['failure'] == 0
    assert snap['platforms']['youtube']['avg_duration_s'] == 2.0


def test_record_failure_and_multilang_coexist():
    metrics.record_failure('reddit', 3.0)
    metrics.record_multilang('youtube')
    snap = metrics.snapshot()
    assert snap['platforms']['reddit']['failure'] == 1
    assert snap['platforms']['youtube']['multilang'] == 1
    assert snap['platforms']['youtube']['total'] == 1


def test_snapshot_reports_last_success_and_failure(monkeypatch):
    timestamps = iter((1000.0, 1100.0))
    monkeypatch.setattr(metrics.time, "time", lambda: next(timestamps))
    metrics.record_success('instagram', 1.0)
    metrics.record_failure('reddit', 2.0)

    snap = metrics.snapshot()
    assert snap['last_success_at'] == 1000.0
    assert snap['last_failure_at'] == 1100.0
    assert snap['last_success_platform'] == 'instagram'
    assert snap['last_failure_platform'] == 'reddit'


def test_avg_duration_ignores_multilang():
    """multilang não entra no cálculo de duração média (não tem sentido)."""
    metrics.record_success('x', 4.0)
    metrics.record_multilang('x')
    snap = metrics.snapshot()
    assert snap['platforms']['x']['avg_duration_s'] == 4.0


def test_format_summary_empty():
    out = metrics.format_summary()
    assert "sem downloads" in out.lower()


def test_format_summary_with_data():
    metrics.record_success('youtube', 10.0)
    metrics.record_failure('reddit', 5.0)
    out = metrics.format_summary()
    assert "youtube" in out
    assert "reddit" in out
    assert "ok=1" in out


def test_snapshot_is_copy_not_reference():
    """snapshot deve isolar o estado interno — mutar o snapshot não afeta o real."""
    metrics.record_success('y', 1.0)
    snap = metrics.snapshot()
    snap['platforms']['y']['success'] = 999
    assert metrics.snapshot()['platforms']['y']['success'] == 1


def test_metrics_survive_in_memory_reset():
    metrics.record_success('reddit', 1.5)
    metrics.record_failure('instagram', 2.5)

    metrics._metrics.platforms.clear()
    metrics._metrics.last_success_at = 0.0
    metrics._metrics.last_failure_at = 0.0
    metrics._metrics.last_success_platform = ""
    metrics._metrics.last_failure_platform = ""
    metrics._load_persisted()

    snap = metrics.snapshot()
    assert snap['persistent'] is True
    assert snap['platforms']['reddit']['success'] == 1
    assert snap['platforms']['instagram']['failure'] == 1
    assert snap['last_success_platform'] == 'reddit'
    assert snap['last_failure_platform'] == 'instagram'
