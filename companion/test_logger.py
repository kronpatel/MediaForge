"""
test_logger.py – Unit tests for AppLogger.exception() (Phase 3 Area 2)

Tests cover:
  1. exception() inside an except block captures the live traceback (exc=None).
  2. exception() with an explicitly supplied exc argument.
  3. exception() outside any except block (no active exception) logs exc=None safely.
  4. Traceback emission is gated by self.debug (production vs debug mode).
  5. entry level is always ERROR regardless of debug setting.
  6. The human-readable message is preserved verbatim.
"""

import sys
import os
import unittest
from unittest.mock import patch

# Ensure companion/ is on the path so imports resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from logger import AppLogger  # noqa: E402


class DummyCallback:
    """Collects the most recent LogEntry for assertions."""

    def __init__(self) -> None:
        self.entry = None

    def __call__(self, entry) -> None:
        self.entry = entry


class TestExceptionActiveTraceback(unittest.TestCase):
    """exception() called inside an except block with no exc argument."""

    def test_captures_live_traceback_in_debug_mode(self):
        logger = AppLogger(debug=True)
        cb = DummyCallback()
        logger.register_callback(cb)

        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("something failed")

        entry = cb.entry
        self.assertIsNotNone(entry)
        self.assertEqual(entry.level, "ERROR")
        self.assertEqual(entry.message.splitlines()[0], "something failed")
        self.assertIn("ValueError: boom", entry.message)
        self.assertIn("Traceback", entry.message)

    def test_suppresses_traceback_in_production_mode(self):
        logger = AppLogger(debug=False)
        cb = DummyCallback()
        logger.register_callback(cb)

        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("something failed")

        entry = cb.entry
        self.assertIsNotNone(entry)
        self.assertEqual(entry.level, "ERROR")
        # Production mode: no traceback appended
        self.assertNotIn("Traceback", entry.message)
        self.assertNotIn("ValueError: boom", entry.message)


class TestExceptionSuppliedExc(unittest.TestCase):
    """exception() called with an explicitly supplied exc argument."""

    def test_uses_supplied_exception(self):
        logger = AppLogger(debug=True)
        cb = DummyCallback()
        logger.register_callback(cb)

        exc = RuntimeError("supplied")
        logger.exception("custom message", exc=exc)

        entry = cb.entry
        self.assertIsNotNone(entry)
        self.assertEqual(entry.level, "ERROR")
        self.assertEqual(entry.message.splitlines()[0], "custom message")
        self.assertIn("RuntimeError: supplied", entry.message)

    def test_supplied_exc_gated_by_debug(self):
        logger = AppLogger(debug=False)
        cb = DummyCallback()
        logger.register_callback(cb)

        exc = TypeError("typo")
        logger.exception("custom message", exc=exc)

        entry = cb.entry
        self.assertIsNotNone(entry)
        self.assertEqual(entry.level, "ERROR")
        self.assertNotIn("TypeError: typo", entry.message)


class TestExceptionNoActiveException(unittest.TestCase):
    """exception() called outside any except block (no live exception)."""

    def test_no_active_exception_logs_safely_in_debug(self):
        logger = AppLogger(debug=True)
        cb = DummyCallback()
        logger.register_callback(cb)

        # Not inside an except block -> sys.exc_info()[1] is None
        logger.exception("no exception active")

        entry = cb.entry
        self.assertIsNotNone(entry)
        self.assertEqual(entry.level, "ERROR")
        self.assertEqual(entry.message, "no exception active")
        # exc=None means no traceback appended even in debug mode
        self.assertNotIn("Traceback", entry.message)

    def test_no_active_exception_logs_safely_in_production(self):
        logger = AppLogger(debug=False)
        cb = DummyCallback()
        logger.register_callback(cb)

        logger.exception("no exception active")

        entry = cb.entry
        self.assertIsNotNone(entry)
        self.assertEqual(entry.level, "ERROR")
        self.assertEqual(entry.message, "no exception active")


class TestExceptionLevelAndMessage(unittest.TestCase):
    """Level and message guarantees independent of debug setting."""

    def test_level_always_error(self):
        for debug in (True, False):
            logger = AppLogger(debug=debug)
            cb = DummyCallback()
            logger.register_callback(cb)
            try:
                1 / 0
            except ZeroDivisionError:
                logger.exception("divide by zero")
            self.assertEqual(cb.entry.level, "ERROR")

    def test_message_preserved_verbatim(self):
        logger = AppLogger(debug=True)
        cb = DummyCallback()
        logger.register_callback(cb)
        try:
            raise KeyError("missing-key")
        except KeyError:
            logger.exception("user-facing message here")
        self.assertEqual(cb.entry.message.splitlines()[0], "user-facing message here")


if __name__ == "__main__":
    unittest.main()
