import system_resources


def test_cgroup_memory_is_preferred(tmp_path, monkeypatch):
    current = tmp_path / "memory.current"
    maximum = tmp_path / "memory.max"
    current.write_text(str(2 * 1024**3), encoding="ascii")
    maximum.write_text(str(8 * 1024**3), encoding="ascii")
    monkeypatch.setattr(system_resources, "CGROUP_MEMORY_CURRENT", str(current))
    monkeypatch.setattr(system_resources, "CGROUP_MEMORY_MAX", str(maximum))

    stats = system_resources.get_memory_stats()

    assert stats["total_mb"] == 8192.0
    assert stats["used_mb"] == 2048.0
    assert stats["pct_used"] == 25.0
