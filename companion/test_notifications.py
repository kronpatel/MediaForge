"""
test_notifications.py

Unit test suite for the NotificationManager architecture:
1. Notification dataclass creation and field defaults.
2. Source constants.
3. Priority queue ordering.
4. Renderer subscribe/unsubscribe.
5. Duplicate suppression (same ID and same title within window).
6. Quiet hours suppression and toggle.
7. History tracking and max-cap enforcement.
8. Statistics accumulation.
9. Atomic persistence (save and load).
10. Graceful shutdown.
11. Clear history.
"""

import json
import os
import time
import unittest

from notifications import (
    Notification,
    NotificationManager,
    PRIORITY_CRITICAL,
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_NORMAL,
    PriorityValue,
    SOURCE_BACKEND,
    SOURCE_UPDATER,
    SOURCE_INSTALLER,
    SOURCE_SCHEDULER,
    SOURCE_DASHBOARD,
    SOURCE_UI,
    _QueueItem,
    _priority_int,
    _serialize,
    _deserialize,
    SCHEMA_VERSION,
    init_notification_manager,
    get_notification_manager,
)


class DummyLogger:
    def info(self, msg, *args, **kwargs): pass
    def warning(self, msg, *args, **kwargs): pass
    def error(self, msg, *args, **kwargs): pass
    def log(self, msg, level="INFO", *args, **kwargs): pass


class TestNotificationConstants(unittest.TestCase):

    def test_source_constants(self):
        self.assertEqual(SOURCE_BACKEND, "backend")
        self.assertEqual(SOURCE_UPDATER, "updater")
        self.assertEqual(SOURCE_INSTALLER, "installer")
        self.assertEqual(SOURCE_SCHEDULER, "scheduler")
        self.assertEqual(SOURCE_DASHBOARD, "dashboard")
        self.assertEqual(SOURCE_UI, "ui")

    def test_priority_values(self):
        self.assertEqual(PriorityValue.CRITICAL, 0)
        self.assertEqual(PriorityValue.HIGH, 1)
        self.assertEqual(PriorityValue.NORMAL, 2)
        self.assertEqual(PriorityValue.LOW, 3)

    def test_priority_int_mapping(self):
        self.assertEqual(_priority_int(PRIORITY_CRITICAL), 0)
        self.assertEqual(_priority_int(PRIORITY_HIGH), 1)
        self.assertEqual(_priority_int(PRIORITY_NORMAL), 2)
        self.assertEqual(_priority_int(PRIORITY_LOW), 3)
        self.assertEqual(_priority_int("unknown"), 2)


class TestNotificationDataclass(unittest.TestCase):

    def test_default_fields(self):
        n = Notification()
        self.assertEqual(n.version, 1)
        self.assertIsInstance(n.timestamp, float)
        self.assertGreater(n.timestamp, 0)
        self.assertEqual(len(n.id), 12)
        self.assertEqual(n.priority, PRIORITY_NORMAL)
        self.assertEqual(n.source, "backend")
        self.assertIsNone(n.action)
        self.assertIsNone(n.action_data)
        self.assertIsNone(n.data)

    def test_all_fields(self):
        n = Notification(
            category="test.category",
            priority=PRIORITY_HIGH,
            source=SOURCE_SCHEDULER,
            title="Test Title",
            message="Test Message",
            action="open_url",
            action_data={"url": "http://example.com"},
            data={"key": "value"},
        )
        self.assertEqual(n.category, "test.category")
        self.assertEqual(n.priority, "high")
        self.assertEqual(n.source, "scheduler")
        self.assertEqual(n.title, "Test Title")
        self.assertEqual(n.message, "Test Message")
        self.assertEqual(n.action, "open_url")
        self.assertEqual(n.action_data, {"url": "http://example.com"})
        self.assertEqual(n.data, {"key": "value"})

    def test_serialize_deserialize(self):
        n = Notification(
            category="cat",
            priority=PRIORITY_LOW,
            source=SOURCE_UPDATER,
            title="Title",
            message="Msg",
            action="act",
            action_data={"a": 1},
            data={"b": 2},
        )
        data = _serialize(n)
        restored = _deserialize(data)
        self.assertEqual(restored.category, n.category)
        self.assertEqual(restored.priority, n.priority)
        self.assertEqual(restored.source, n.source)
        self.assertEqual(restored.title, n.title)
        self.assertEqual(restored.message, n.message)
        self.assertEqual(restored.action, n.action)
        self.assertEqual(restored.action_data, n.action_data)
        self.assertEqual(restored.data, n.data)
        self.assertEqual(restored.id, n.id)
        self.assertEqual(restored.version, n.version)
        self.assertEqual(restored.timestamp, n.timestamp)


class TestNotificationManager(unittest.TestCase):

    def setUp(self):
        self.logger = DummyLogger()
        self.tmp_history = os.path.join(
            os.path.dirname(__file__),
            "cache",
            f"test_history_{int(time.time())}.json",
        )
        self.manager = NotificationManager(
            logger=self.logger,
            history_path=self.tmp_history,
            history_max=10,
            duplicate_window=1.0,
        )
        self.manager.start()

    def tearDown(self):
        self.manager.shutdown()
        if os.path.exists(self.tmp_history):
            try:
                os.remove(self.tmp_history)
            except OSError:
                pass
        tmp = self.tmp_history + ".tmp"
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    def test_publish_and_renderer_called(self):
        received = []
        self.manager.subscribe(lambda n: received.append(n))
        self.manager.publish(
            category="test",
            title="Hello",
            message="World",
            source=SOURCE_UPDATER,
        )
        time.sleep(0.5)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].title, "Hello")
        self.assertEqual(received[0].source, "updater")

    def test_subscribe_unsubscribe(self):
        received = []
        def renderer(n):
            received.append(n)
        self.manager.subscribe(renderer)
        self.manager.publish(category="t", title="A", message="B")
        time.sleep(0.3)
        self.assertEqual(len(received), 1)
        self.manager.unsubscribe(renderer)
        self.manager.publish(category="t", title="C", message="D")
        time.sleep(0.3)
        self.assertEqual(len(received), 1)

    def test_multiple_renderers(self):
        recv1 = []
        recv2 = []
        self.manager.subscribe(lambda n: recv1.append(n))
        self.manager.subscribe(lambda n: recv2.append(n))
        self.manager.publish(category="t", title="Multi", message="Test")
        time.sleep(0.3)
        self.assertEqual(len(recv1), 1)
        self.assertEqual(len(recv2), 1)

    def test_priority_ordering(self):
        high = _QueueItem(0, 0, Notification(title="High"))
        normal = _QueueItem(2, 1, Notification(title="Normal"))
        low = _QueueItem(3, 2, Notification(title="Low"))
        items = [low, normal, high]
        items.sort()
        self.assertEqual([i.notification.title for i in items], ["High", "Normal", "Low"])

    def test_priority_processing_order(self):
        received = []
        self.manager.subscribe(lambda n: received.append(n.title))
        self.manager.publish(category="t", title="Low", priority=PRIORITY_LOW)
        self.manager.publish(category="t", title="High", priority=PRIORITY_HIGH)
        self.manager.publish(category="t", title="Normal", priority=PRIORITY_NORMAL)
        time.sleep(0.5)
        self.assertEqual(received, ["High", "Normal", "Low"])

    def test_duplicate_suppression_same_title(self):
        received = []
        self.manager.subscribe(lambda n: received.append(n))
        self.manager.publish(category="t", title="Dup", message="First")
        time.sleep(0.3)
        self.assertEqual(len(received), 1)
        self.manager.publish(category="t", title="Dup", message="Second")
        time.sleep(0.3)
        self.assertEqual(len(received), 1)

    def test_no_duplicate_suppression_different_titles(self):
        received = []
        self.manager.subscribe(lambda n: received.append(n))
        self.manager.publish(category="t", title="First", message="A")
        self.manager.publish(category="t", title="Second", message="B")
        time.sleep(0.5)
        self.assertEqual(len(received), 2)

    def test_duplicate_window_expiry(self):
        self.manager._duplicate_window = 0.1
        received = []
        self.manager.subscribe(lambda n: received.append(n))
        self.manager.publish(category="t", title="Expire", message="First")
        time.sleep(0.15)
        self.manager.publish(category="t", title="Expire", message="Second")
        time.sleep(0.3)
        self.assertGreaterEqual(len(received), 2)

    def test_quiet_hours_suppression(self):
        received = []
        self.manager.subscribe(lambda n: received.append(n))
        import time as tm
        now = tm.localtime()
        self.manager.set_quiet_hours((now.tm_hour, now.tm_min), (now.tm_hour, now.tm_min + 1))
        self.manager.set_quiet_hours_enabled(True)
        self.manager.publish(category="q", title="Quiet", message="Should be suppressed")
        time.sleep(0.3)
        self.assertEqual(len(received), 0)

    def test_quiet_hours_disabled(self):
        received = []
        self.manager.subscribe(lambda n: received.append(n))
        import time as tm
        now = tm.localtime()
        self.manager.set_quiet_hours((now.tm_hour, now.tm_min), (now.tm_hour, now.tm_min + 1))
        self.manager.set_quiet_hours_enabled(False)
        self.manager.publish(category="q", title="Not Quiet", message="Should go through")
        time.sleep(0.3)
        self.assertEqual(len(received), 1)

    def test_quiet_hours_no_start_end(self):
        received = []
        self.manager.subscribe(lambda n: received.append(n))
        self.manager.set_quiet_hours(None, None)
        self.manager.publish(category="t", title="Always On", message="OK")
        time.sleep(0.3)
        self.assertEqual(len(received), 1)

    def test_history_tracking(self):
        self.manager.publish(category="h", title="Hist1", message="A")
        self.manager.publish(category="h", title="Hist2", message="B")
        time.sleep(0.5)
        history = self.manager.get_history()
        self.assertGreaterEqual(len(history), 2)
        titles = [n.title for n in history]
        self.assertIn("Hist1", titles)
        self.assertIn("Hist2", titles)

    def test_history_max_cap(self):
        self.manager._history_max = 3
        for i in range(5):
            self.manager.publish(category="h", title=f"Item{i}", message=str(i))
        time.sleep(0.5)
        history = self.manager.get_history()
        self.assertLessEqual(len(history), 3)
        titles = [n.title for n in history]
        for i in range(2):
            self.assertNotIn(f"Item{i}", titles)

    def test_stats_accumulation(self):
        self.manager.publish(category="download", title="D1", source=SOURCE_BACKEND, priority=PRIORITY_HIGH)
        self.manager.publish(category="download", title="D2", source=SOURCE_BACKEND, priority=PRIORITY_HIGH)
        self.manager.publish(category="update", title="U1", source=SOURCE_UPDATER, priority=PRIORITY_NORMAL)
        time.sleep(0.5)
        stats = self.manager.get_stats()
        self.assertEqual(stats.get("category:download"), 2)
        self.assertEqual(stats.get("category:update"), 1)
        self.assertEqual(stats.get("source:backend"), 2)
        self.assertEqual(stats.get("source:updater"), 1)
        self.assertEqual(stats.get("priority:high"), 2)
        self.assertEqual(stats.get("priority:normal"), 1)
        self.assertEqual(stats.get("total"), 3)

    def test_clear_history(self):
        self.manager.publish(category="c", title="Clear Me", message="X")
        time.sleep(0.3)
        self.assertGreater(len(self.manager.get_history()), 0)
        self.manager.clear_history()
        self.assertEqual(len(self.manager.get_history()), 0)
        stats = self.manager.get_stats()
        self.assertEqual(stats.get("total", 0), 0)

    def test_persistence_save_and_load(self):
        self.manager.publish(category="p", title="Persist", message="Save me")
        time.sleep(0.3)
        self.manager.shutdown()
        self.assertTrue(os.path.exists(self.tmp_history))
        with open(self.tmp_history, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIsInstance(data, dict)
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)
        self.assertIsInstance(data["notifications"], list)
        self.assertGreaterEqual(len(data["notifications"]), 1)
        self.assertEqual(data["notifications"][0]["title"], "Persist")

    def test_persistence_load_restores_history(self):
        self.manager.publish(category="p", title="Load Me", message="Restore check")
        time.sleep(0.3)
        self.manager.shutdown()

        new_manager = NotificationManager(
            logger=self.logger,
            history_path=self.tmp_history,
            history_max=10,
        )
        new_manager.start()
        time.sleep(0.3)
        history = new_manager.get_history()
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0].title, "Load Me")
        new_manager.shutdown()

    def test_unknown_schema_version_starts_empty(self):
        """Unknown schema versions should safely start with empty history."""
        with open(self.tmp_history, "w", encoding="utf-8") as fh:
            json.dump({"schema_version": 999, "notifications": [{"title": "bad"}]}, fh)
        self.manager.shutdown()

        mgr = NotificationManager(
            logger=self.logger,
            history_path=self.tmp_history,
            history_max=10,
        )
        mgr.start()
        time.sleep(0.3)
        self.assertEqual(len(mgr.get_history()), 0)
        mgr.shutdown()

    def test_singleton_init_and_get(self):
        nm1 = init_notification_manager(logger=self.logger)
        nm2 = get_notification_manager()
        self.assertIs(nm1, nm2)
        nm1.shutdown()

    def test_singleton_get_before_init_raises(self):
        with self.assertRaises(RuntimeError):
            get_notification_manager()

    def test_shutdown_stops_worker(self):
        self.assertTrue(self.manager._started)
        self.manager.shutdown()
        self.assertFalse(self.manager._started)

    def test_shutdown_idempotent(self):
        self.manager.shutdown()
        self.manager.shutdown()

    def test_renderer_exception_does_not_crash_worker(self):
        def failing_renderer(n):
            raise ValueError("Intentional failure")
        self.manager.subscribe(failing_renderer)
        self.manager.publish(category="e", title="Fail", message="Should not crash")
        time.sleep(0.3)
        self.assertTrue(self.manager._started)

    def test_id_generation_unique(self):
        ids = {Notification().id for _ in range(100)}
        self.assertEqual(len(ids), 100)

    def test_publish_returns_notification(self):
        n = self.manager.publish(category="r", title="Return Value", message="Check")
        self.assertIsInstance(n, Notification)
        self.assertEqual(n.title, "Return Value")


if __name__ == "__main__":
    unittest.main()
