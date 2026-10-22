import unittest
from unittest.mock import mock_open, patch
from lifecycle.health_watchdog import _check_system_resources


class TestHealthWatchdog(unittest.TestCase):
    @patch("shutil.disk_usage", return_value=(100 * (1024**3), 10 * (1024**3), 90 * (1024**3)))
    def test_check_system_resources_success(self, mock_disk):
        meminfo_data = "MemTotal:       8192000 kB\nMemAvailable:   6144000 kB\n"
        with patch("builtins.open", mock_open(read_data=meminfo_data)):
            metrics = _check_system_resources()

        self.assertEqual(metrics["disk_pct"], 10.0)
        self.assertEqual(metrics["disk_free_gb"], 90.0)
        self.assertEqual(metrics["ram_pct"], 25.0)


if __name__ == "__main__":
    unittest.main()
