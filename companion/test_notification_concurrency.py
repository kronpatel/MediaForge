"""
test_notification_concurrency.py – Concurrency tests for NotificationManager.publish
and TrayManager.notify (Phase 3 Area 8).

Uses threading.Barrier to synchronize worker threads so they hammer the shared
code paths simultaneously, then asserts shared state stays consistent and no
exceptions escape.
"""

import os
import sys
import threading
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from notifications import (
    NotificationManager,
    PRIORITY_NORMAL,
    SOURCE_BACKEND,
    QUEUE_MAX,
)
from tray import TrayManager


class DummyLogger:
    def info(self, msg, *args, **kwargs): pass
    def warning(self, msg, *args, **kwargs): pass
    def error(self, msg, *args, **kwargs): pass
    def log(self, msg, level="INFO", *args, **kwargs): pass


def _hammer_publish(mgr, barrier, results, errors):
    barrier.wait()
    for _ in range(25):
        try:
            n = mgr.publish(
                title="concurrent",
                message="hello",
                source=SOURCE_BACKEND,
                priority=PRIORITY_NORMAL,
            )
            if n is not None:
                results.append(n.id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)


class TestConcurrentNotificationPublish(unittest.TestCase):
    """Concurrent publish() must not corrupt the queue, counter, or notification ids."""

    def setUp(self):
        self.mgr = NotificationManager(logger=DummyLogger())

    def test_concurrent_publish_no_errors(self):
        num_threads = 8
        barrier = threading.Barrier(num_threads)
        results = []
        errors = []
        threads = [
            threading.Thread(target=_hammer_publish, args=(self.mgr, barrier, results, errors))
            for _ in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        expected = num_threads * 25
        self.assertEqual(len(results), expected)
        self.assertEqual(len(set(results)), expected)  # every id is unique
        self.assertEqual(self.mgr._queue.qsize(), expected)
        self.assertEqual(self.mgr._counter, expected)
        self.assertLessEqual(self.mgr._queue.qsize(), QUEUE_MAX)


class TestConcurrentTrayNotify(unittest.TestCase):
    """Concurrent TrayManager.notify() must stay consistent under the _notify_lock."""

    def setUp(self):
        self.manager = MagicMock()
        self.window = MagicMock()
        self.logger = DummyLogger()
        self.tray = TrayManager(self.manager, self.window, self.logger)
        # Pretend the tray icon is running so notify() reaches _notify_lock.
        self.tray._icon = MagicMock()

    def _hammer_notify(self, barrier, errors):
        barrier.wait()
        for _ in range(25):
            try:
                self.tray.notify("title", "message")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    def test_concurrent_notify_no_errors(self):
        num_threads = 8
        barrier = threading.Barrier(num_threads)
        errors = []
        threads = [
            threading.Thread(target=self._hammer_notify, args=(barrier, errors))
            for _ in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertIsNotNone(self.tray._last_notification)
        self.tray._icon.notify.assert_called()


if __name__ == "__main__":
    unittest.main()
