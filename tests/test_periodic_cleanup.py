import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

import state
from lifecycle.periodic_cleanup import _clean_old_temp_folders


class TestPeriodicCleanup(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.download_dir = os.path.join(self.test_dir, "downloads")
        os.makedirs(self.download_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_clean_old_temp_folders_removes_old_unlocked_folders(self):
        # 1. Old task folder (> 30 min) not active
        old_task = os.path.join(self.download_dir, "task_old_123")
        os.makedirs(old_task, exist_ok=True)
        file1 = os.path.join(old_task, "video.mp4")
        with open(file1, "wb") as f:
            f.write(b"12345")
        old_time = time.time() - 3600
        os.utime(old_task, (old_time, old_time))

        # 2. Active task folder (registered in state)
        active_task = os.path.join(self.download_dir, "task_active_456")
        os.makedirs(active_task, exist_ok=True)
        file2 = os.path.join(active_task, "image.jpg")
        with open(file2, "wb") as f:
            f.write(b"67890")
        os.utime(active_task, (old_time, old_time))

        # 3. Fresh task folder (< 30 min)
        fresh_task = os.path.join(self.download_dir, "task_fresh_789")
        os.makedirs(fresh_task, exist_ok=True)

        with patch("lifecycle.periodic_cleanup.BASE_DOWNLOAD_DIR", self.download_dir), \
             patch.object(state, "active_download_folders", {active_task}), \
             patch("lifecycle.periodic_cleanup.cleanup_and_vacuum_cache"):
            _clean_old_temp_folders()

        self.assertFalse(os.path.exists(old_task))
        self.assertTrue(os.path.exists(active_task))
        self.assertTrue(os.path.exists(fresh_task))


if __name__ == "__main__":
    unittest.main()
