import pytest
import json

import metrics_server
import state


class _FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self, **_kwargs):
        return {"ok": True, "result": {"username": "test_bot"}}


class _FakeSession:
    closed = False

    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse()


@pytest.mark.asyncio
async def test_telegram_auth_uses_getme_and_cache(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(state, "AIOHTTP_SESSION", session)
    monkeypatch.setattr(metrics_server, "TOKEN", "test-token")
    monkeypatch.setattr(metrics_server, "LOCAL_API_HOST", "telegram-api:8081")
    monkeypatch.setattr(metrics_server, "LOCAL_API_URL", "http://telegram-api:8081/bot")
    metrics_server._telegram_auth_cache.update({"checked_at": 0.0, "ok": False})

    assert await metrics_server._telegram_api_authenticated(force=True) is True
    assert await metrics_server._telegram_api_authenticated() is True
    assert len(session.calls) == 1
    assert session.calls[0][0].endswith("/bottest-token/getMe")


@pytest.mark.asyncio
async def test_failed_auth_cache_retries_after_short_ttl(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(state, "AIOHTTP_SESSION", session)
    monkeypatch.setattr(metrics_server, "TOKEN", "test-token")
    monkeypatch.setattr(metrics_server, "LOCAL_API_HOST", "telegram-api:8081")
    monkeypatch.setattr(metrics_server, "LOCAL_API_URL", "http://telegram-api:8081/bot")
    now = metrics_server.time.monotonic()
    metrics_server._telegram_auth_cache.update({"checked_at": now, "ok": False})

    assert await metrics_server._telegram_api_authenticated() is False
    assert session.calls == []

    metrics_server._telegram_auth_cache["checked_at"] = (
        now - metrics_server._TELEGRAM_AUTH_FAILURE_TTL_SECONDS - 1
    )
    assert await metrics_server._telegram_api_authenticated() is True
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_liveness_does_not_depend_on_telegram(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(state, "AIOHTTP_SESSION", session)
    monkeypatch.setattr(state, "PW_BROWSER", object())

    async def must_not_run():
        raise AssertionError("liveness não deve consultar Telegram")

    monkeypatch.setattr(metrics_server, "_telegram_api_online", must_not_run)
    monkeypatch.setattr(metrics_server, "_telegram_api_authenticated", must_not_run)

    response = await metrics_server.handle_live(None)
    assert response.status == 200
    assert json.loads(response.text)["status"] == "ok"


def test_system_stats_include_last_download_timestamps(monkeypatch):
    monkeypatch.setattr(
        metrics_server,
        "download_metrics_snapshot",
        lambda: {
            "last_success_at": 1000.0,
            "last_failure_at": 1100.0,
            "last_success_platform": "instagram",
            "last_failure_platform": "reddit",
            "persistent": True,
            "platforms": {
                "instagram": {"success": 2, "failure": 0},
                "reddit": {"success": 0, "failure": 1},
            },
        },
    )
    monkeypatch.setattr(metrics_server.time, "time", lambda: 1200.0)
    monkeypatch.setattr(state, "request_stats", lambda: {
        "active": 0, "pending": 0, "last_request_at": 0,
    })

    downloads = metrics_server._get_system_stats()["downloads"]
    assert downloads["success"] == 2
    assert downloads["failure"] == 1
    assert downloads["last_success_age_seconds"] == 200
    assert downloads["last_failure_age_seconds"] == 100
    assert downloads["last_success_platform"] == "instagram"
    assert downloads["persistent"] is True
