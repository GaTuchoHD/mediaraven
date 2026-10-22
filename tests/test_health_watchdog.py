import unittest
from unittest.mock import patch
from io import StringIO
from lifecycle.health_watchdog import _check_system_resources


class TestHealthWatchdog(unittest.TestCase):
    @patch("shutil.disk_usage", return_value=(100 * (1024**3), 10 * (1024**3), 90 * (1024**3)))
    def test_check_system_resources_success(self, mock_disk):
        def fake_cgroup(path, *_args, **_kwargs):
            if str(path).endswith("memory.current"):
                return StringIO(str(250 * 1024**2))
            if str(path).endswith("memory.max"):
                return StringIO(str(1000 * 1024**2))
            raise FileNotFoundError(path)

        with patch("builtins.open", side_effect=fake_cgroup):
            metrics = _check_system_resources()

        self.assertEqual(metrics["disk_pct"], 10.0)
        self.assertEqual(metrics["disk_free_gb"], 90.0)
        self.assertEqual(metrics["ram_pct"], 25.0)


if __name__ == "__main__":
    unittest.main()
