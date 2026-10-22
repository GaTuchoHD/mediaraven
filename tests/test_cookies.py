import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest

import cookies
import state


def _make_firefox_db(path: str, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE moz_cookies (
            name TEXT, value TEXT, host TEXT, path TEXT,
            expiry INTEGER, isSecure INTEGER, isHttpOnly INTEGER
        )
    """)
    conn.executemany(
        "INSERT INTO moz_cookies VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_extract_firefox_cookies_missing_file(tmp_path):
    fake_profile = tmp_path / "does-not-exist"
    with patch.object(cookies, "FIREFOX_PROFILE_PATH", str(fake_profile)):
        assert cookies.extract_firefox_cookies() == []


def test_extract_firefox_cookies_reads_rows(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    db_path = profile / "cookies.sqlite"
    _make_firefox_db(
        str(db_path),
        rows=[
            ("session", "abc", ".reddit.com", "/", 9999999999, 1, 1),
            ("pref", "xyz", ".example.com", "/", 0, 0, 0),
        ],
    )

    with patch.object(cookies, "FIREFOX_PROFILE_PATH", str(profile)):
        result = cookies.extract_firefox_cookies()

    by_name = {c['name']: c for c in result}
    assert by_name['session']['domain'] == ".reddit.com"
    assert by_name['session']['secure'] is True
    assert by_name['session']['httpOnly'] is True
    assert by_name['pref']['expires'] == -1


def test_extract_firefox_cookies_clamps_future_date(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    db_path = profile / "cookies.sqlite"
    bogus_future = 9999999999999
    _make_firefox_db(str(db_path), rows=[("bogus", "v", "x.com", "/", bogus_future, 0, 0)])

    with patch.object(cookies, "FIREFOX_PROFILE_PATH", str(profile)):
        result = cookies.extract_firefox_cookies()

    assert result[0]['expires'] == 253402300799


def test_get_aiohttp_cookies_filters_by_domain():
    state.FIREFOX_COOKIES_CACHE = [
        {'name': 'a', 'value': '1', 'domain': '.reddit.com'},
        {'name': 'b', 'value': '2', 'domain': '.example.com'},
        {'name': 'c', 'value': '3', 'domain': 'reddit.com'},
    ]
    try:
        got = cookies.get_aiohttp_cookies_for_url("https://www.reddit.com/r/pics/")
        assert got == {'a': '1', 'c': '3'}
    finally:
        state.FIREFOX_COOKIES_CACHE = []


def test_get_aiohttp_cookies_empty_when_no_match():
    state.FIREFOX_COOKIES_CACHE = [
        {'name': 'a', 'value': '1', 'domain': '.reddit.com'},
    ]
    try:
        got = cookies.get_aiohttp_cookies_for_url("https://instagram.com/foo")
        assert got == {}
    finally:
        state.FIREFOX_COOKIES_CACHE = []


def test_get_firefox_cookie_status_missing_file(tmp_path):
    fake_profile = tmp_path / "missing"
    with patch.object(cookies, "FIREFOX_PROFILE_PATH", str(fake_profile)):
        status = cookies.get_firefox_cookie_status(now=1000)

    assert status['exists'] is False
    assert status['readable'] is False
    assert status['total'] == 0


def test_get_firefox_cookie_status_counts_without_exposing_values(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    db_path = profile / "cookies.sqlite"
    _make_firefox_db(
        str(db_path),
        rows=[
            ("session", "secret-session", ".youtube.com", "/", 0, 1, 1),
            ("future", "secret-future", ".google.com", "/", 2000, 1, 1),
            ("expired", "secret-expired", ".instagram.com", "/", 500, 1, 1),
            ("other", "secret-other", ".example.com", "/", 3000, 0, 0),
        ],
    )

    with patch.object(cookies, "FIREFOX_PROFILE_PATH", str(profile)):
        status = cookies.get_firefox_cookie_status(now=1000)

    assert status['exists'] is True
    assert status['readable'] is True
    assert status['total'] == 4
    assert status['active'] == 3
    assert status['expired'] == 1
    assert status['session'] == 1
    assert status['domains'] == 4
    assert status['platforms']['YouTube/Google'] == 2
    assert status['platforms']['Instagram'] == 1
    assert "secret-" not in repr(status)


def test_get_firefox_cookie_status_reports_platform_readiness(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    _make_firefox_db(
        str(profile / "cookies.sqlite"),
        rows=[
            ("sessionid", "hidden", ".instagram.com", "/", 0, 1, 1),
            ("csrftoken", "hidden", ".instagram.com", "/", 0, 1, 1),
            ("ds_user_id", "hidden", ".instagram.com", "/", 0, 1, 1),
            ("auth_token", "hidden", ".x.com", "/", 1500, 1, 1),
            ("ct0", "hidden", ".x.com", "/", 1500, 1, 1),
        ],
    )

    with patch.object(cookies, "FIREFOX_PROFILE_PATH", str(profile)):
        status = cookies.get_firefox_cookie_status(now=1000)

    assert status["readiness"]["Instagram"] is True
    assert status["readiness"]["X/Twitter"] is True
    assert status["readiness"]["YouTube/Google"] is False
    assert status["expiring_soon"] == 2


def test_install_raw_x_cookies_replaces_only_x_domain(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    db_path = profile / "cookies.sqlite"
    _make_firefox_db(
        str(db_path),
        rows=[
            ("old", "old-value", ".x.com", "/", 0, 1, 0),
            ("youtube", "keep-me", ".youtube.com", "/", 0, 1, 1),
        ],
    )

    with patch.object(cookies, "FIREFOX_PROFILE_PATH", str(profile)):
        result = cookies.install_raw_cookies(
            "x",
            'auth_token=auth-secret; ct0=csrf-secret; lang="pt"',
        )

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name, value, host FROM moz_cookies ORDER BY name"
    ).fetchall()
    conn.close()
    assert result == {'platform': 'X/Twitter', 'installed': 3}
    assert ("old", "old-value", ".x.com") not in rows
    assert ("youtube", "keep-me", ".youtube.com") in rows
    assert ("auth_token", "auth-secret", ".x.com") in rows
    assert ("ct0", "csrf-secret", ".x.com") in rows
    assert ("lang", "pt", ".x.com") in rows


def test_install_raw_x_cookies_requires_auth_pair(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    _make_firefox_db(str(profile / "cookies.sqlite"), rows=[])

    with patch.object(cookies, "FIREFOX_PROFILE_PATH", str(profile)), \
         pytest.raises(ValueError, match="auth_token e ct0"):
        cookies.install_raw_cookies("x", "auth_token=only-one")


def test_install_raw_instagram_cookies_preserves_other_platforms(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    db_path = profile / "cookies.sqlite"
    _make_firefox_db(
        str(db_path),
        rows=[
            ("old", "old-value", ".instagram.com", "/", 0, 1, 0),
            ("youtube", "keep-me", ".youtube.com", "/", 0, 1, 1),
        ],
    )

    raw = (
        "sessionid=session-secret; csrftoken=csrf-secret; "
        "ds_user_id=123; mid=mid-value; tracking_cookie=drop-me"
    )
    with patch.object(cookies, "FIREFOX_PROFILE_PATH", str(profile)):
        result = cookies.install_raw_cookies("instagram", raw)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name, value, host FROM moz_cookies ORDER BY name"
    ).fetchall()
    conn.close()
    assert result == {'platform': 'Instagram', 'installed': 4}
    assert ("old", "old-value", ".instagram.com") not in rows
    assert ("youtube", "keep-me", ".youtube.com") in rows
    assert ("sessionid", "session-secret", ".instagram.com") in rows
    assert ("csrftoken", "csrf-secret", ".instagram.com") in rows
    assert not any(row[0] == "tracking_cookie" for row in rows)


def test_install_raw_instagram_cookies_requires_auth_fields(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    _make_firefox_db(str(profile / "cookies.sqlite"), rows=[])

    with patch.object(cookies, "FIREFOX_PROFILE_PATH", str(profile)), \
         pytest.raises(ValueError, match="sessionid, csrftoken e ds_user_id"):
        cookies.install_raw_cookies("instagram", "sessionid=only-one")


def test_get_firefox_cookie_value_filters_domain(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    _make_firefox_db(
        str(profile / "cookies.sqlite"),
        rows=[
            ("sessionid", "ig-secret", ".instagram.com", "/", 0, 1, 1),
            ("sessionid", "other-secret", ".example.com", "/", 0, 1, 1),
        ],
    )

    with patch.object(cookies, "FIREFOX_PROFILE_PATH", str(profile)):
        value = cookies.get_firefox_cookie_value(
            "sessionid", ("instagram.com",)
        )
    assert value == "ig-secret"


def test_install_raw_cookies_rejects_unsupported_platform():
    with pytest.raises(ValueError, match="Plataforma não suportada"):
        cookies.install_raw_cookies("example", "a=b")
