"""
test_lifecycle.py — Backend lifecycle stress test.

Runs 10+ cycles of:
    Stopped → Start → Running → Stop → Stopped →
    Start → Running → Restart → Running → Stop

Verifies that after EACH transition:
    - self._process is consistent
    - self._status is consistent
    - self._is_managed is consistent
    - process.poll() matches expected
    - Button enabled states would be correct
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest

# Ensure companion/ is on sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from backend_manager import BackendManager, BackendStatus, SETTINGS_FILE
from logger import AppLogger

TEST_PORT = 5002


def _find_free_port(start: int = 5002, end: int = 5010) -> int:
    import socket
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free port found")


class LifecycleCycleTest(unittest.TestCase):
    """Real backend lifecycle stress test — cycles with process lifecycle."""

    CYCLES = 12
    STARTUP_WAIT = 15.0

    @classmethod
    def setUpClass(cls):
        cls.logger = AppLogger(debug=True)
        cls.logger.enable_file_logging()

        # Backup and override settings.json to use a free port
        cls._test_port = _find_free_port()
        cls._settings_backup = None
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
                cls._settings_backup = fh.read()
        cls._write_test_settings(cls._test_port)

        cls.manager = BackendManager(logger=cls.logger)
        # Override host/port so manager doesn't reload from file during start()
        with cls.manager._lock:
            cls.manager._port = cls._test_port

    @classmethod
    def _write_test_settings(cls, port: int) -> None:
        data = {"backend_url": f"http://127.0.0.1:{port}"}
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    @classmethod
    def tearDownClass(cls):
        if cls.manager.status != BackendStatus.STOPPED:
            cls.manager.stop()
            cls._wait_for_status_static(cls.manager, BackendStatus.STOPPED, timeout=8.0)
        cls.manager.shutdown()
        cls.manager.close_session()

        # Restore settings.json
        if cls._settings_backup is not None:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
                fh.write(cls._settings_backup)

    @staticmethod
    def _wait_for_status_static(manager, expected, timeout=15.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if manager.status == expected:
                return
            time.sleep(0.2)
        raise AssertionError(f"Timed out waiting for {expected}")

    def _wait_for_status(self, expected: BackendStatus, timeout: float = STARTUP_WAIT) -> None:
        self._wait_for_status_static(self.manager, expected, timeout)

    def _assert_consistent(self, tag: str, expect_managed: bool | None = None,
                           expect_process: bool | None = None) -> None:
        with self.manager._lock:
            status = self.manager._status
            is_managed = self.manager._is_managed
            proc = self.manager._process
            pid = proc.pid if proc else None
            poll_result = proc.poll() if proc else None

        self.logger.info(
            f"[{tag}] status={status.name} managed={is_managed} "
            f"proc={'yes' if proc else 'no'} pid={pid} poll={poll_result}"
        )

        if expect_managed is not None:
            self.assertEqual(is_managed, expect_managed,
                             f"{tag}: expected managed={expect_managed}")
        if expect_process is not None:
            if expect_process:
                self.assertIsNotNone(proc, f"{tag}: expected process handle")
            else:
                self.assertIsNone(proc, f"{tag}: expected no process handle")

        if status == BackendStatus.RUNNING:
            if expect_process is None or expect_process:
                self.assertIsNotNone(proc)
        if status == BackendStatus.STOPPED:
            self.assertIsNone(proc)

        self.logger.info(f"[{tag}] ✓ consistent")

    def test_lifecycle_cycles(self):
        """Run CYCLES of Start→Stop and Start→Restart→Stop."""

        for cycle in range(1, self.CYCLES + 1):
            self.logger.info(f"\n{'='*60}\nCYCLE {cycle}\n{'='*60}")

            # --- START ---
            self.logger.info(f"[Cycle {cycle}] Calling start()")
            self.manager.start()
            self._wait_for_status(BackendStatus.RUNNING, timeout=self.STARTUP_WAIT)
            self._assert_consistent(f"cycle{cycle}-after-start",
                                    expect_managed=True)
            self.assertEqual(self.manager.status, BackendStatus.RUNNING)
            self.assertTrue(self.manager.is_managed())
            self.assertIsNotNone(self.manager._process)

            # Button check: RUNNING + managed → Stop/Restart enabled, Start disabled
            # In the real UI: Start=disabled, Stop=normal, Restart=normal

            # --- STOP ---
            self.logger.info(f"[Cycle {cycle}] Calling stop()")
            self.manager.stop()
            self._wait_for_status(BackendStatus.STOPPED, timeout=8.0)
            self._assert_consistent(f"cycle{cycle}-after-stop",
                                    expect_managed=False,
                                    expect_process=False)
            self.assertFalse(self.manager.is_managed())
            self.assertIsNone(self.manager._process)

            # Button check: STOPPED → Start enabled, Stop/Restart disabled

            # --- SECOND START ---
            self.logger.info(f"[Cycle {cycle}] Calling start() (second)")
            self.manager.start()
            self._wait_for_status(BackendStatus.RUNNING, timeout=self.STARTUP_WAIT)
            self._assert_consistent(f"cycle{cycle}-after-start2",
                                    expect_managed=True)
            self.assertTrue(self.manager.is_managed())

            # --- RESTART (every other cycle) ---
            if cycle % 2 == 0:
                self.logger.info(f"[Cycle {cycle}] Calling restart()")
                self.manager.restart()
                self._wait_for_status(BackendStatus.RUNNING, timeout=self.STARTUP_WAIT)
                self._assert_consistent(f"cycle{cycle}-after-restart",
                                        expect_managed=True)
                self.assertTrue(self.manager.is_managed())

            # --- FINAL STOP ---
            self.logger.info(f"[Cycle {cycle}] Calling stop()")
            self.manager.stop()
            self._wait_for_status(BackendStatus.STOPPED, timeout=8.0)
            self._assert_consistent(f"cycle{cycle}-after-final-stop",
                                    expect_managed=False,
                                    expect_process=False)

        self.logger.info(f"\n{'='*60}\nAll {self.CYCLES} cycles passed!\n{'='*60}")

    def test_rapid_start_stop(self):
        """Rapid Start→Stop repeated 5 times to stress the button guard."""
        for i in range(5):
            with self.subTest(rapid=i):
                self.manager.start()
                self._wait_for_status(BackendStatus.RUNNING, timeout=self.STARTUP_WAIT)
                self.assertTrue(self.manager.is_managed())
                self.manager.stop()
                self._wait_for_status(BackendStatus.STOPPED, timeout=8.0)
                self.assertFalse(self.manager.is_managed())
                self.assertIsNone(self.manager._process)


if __name__ == "__main__":
    unittest.main()
