"""
test_clipboard_monitor.py – Unit tests for ClipboardMonitor.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipboard_monitor import ClipboardMonitor


class TestClipboardMonitor(unittest.TestCase):
    """Test background clipboard sequence tracking and media URL detection."""

    def test_init_state(self):
        cb = MagicMock()
        monitor = ClipboardMonitor(on_url_detected=cb, poll_interval=0.05)
        self.assertFalse(monitor.is_running())
        self.assertIsNone(monitor._thread)

    def test_disabled_by_callback(self):
        """When is_enabled_fn returns False, check_once returns None."""
        detected = []
        monitor = ClipboardMonitor(
            on_url_detected=lambda urls: detected.append(urls),
            is_enabled_fn=lambda: False,
            poll_interval=0.01,
        )

        with patch.object(monitor, "_read_clipboard_text_win32", return_value="https://youtu.be/dQw4w9WgXcQ"):
            result = monitor.check_once()

        self.assertIsNone(result)
        self.assertEqual(len(detected), 0)

    def test_url_detection(self):
        """Valid URL extracted from clipboard is returned by check_once."""
        detected = []
        monitor = ClipboardMonitor(
            on_url_detected=lambda urls: detected.append(urls),
            is_enabled_fn=lambda: True,
            poll_interval=0.01,
        )

        mock_user32 = MagicMock()
        mock_user32.GetClipboardSequenceNumber.return_value = 101
        monitor._user32 = mock_user32
        monitor._last_seq = 100

        with patch.object(
            monitor,
            "_read_clipboard_text_win32",
            return_value="Check this: https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ):
            urls = monitor.check_once()

        self.assertEqual(urls, ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"])
        self.assertEqual(monitor._last_seq, 101)

    def test_non_media_text_ignored(self):
        """Non-media text returns None."""
        monitor = ClipboardMonitor(
            on_url_detected=lambda urls: None,
            poll_interval=0.01,
        )

        mock_user32 = MagicMock()
        mock_user32.GetClipboardSequenceNumber.return_value = 102
        monitor._user32 = mock_user32
        monitor._last_seq = 101

        with patch.object(
            monitor,
            "_read_clipboard_text_win32",
            return_value="Hello world, this is regular copied text without links",
        ):
            urls = monitor.check_once()

        self.assertIsNone(urls)

    def test_deduplication_same_sequence(self):
        """If clipboard sequence number hasn't changed, check_once returns None immediately."""
        monitor = ClipboardMonitor(
            on_url_detected=lambda urls: None,
            poll_interval=0.01,
        )

        mock_user32 = MagicMock()
        mock_user32.GetClipboardSequenceNumber.return_value = 50
        monitor._user32 = mock_user32
        monitor._last_seq = 50

        with patch.object(monitor, "_read_clipboard_text_win32") as mock_read:
            result = monitor.check_once()
            # Should not even open/read the clipboard
            mock_read.assert_not_called()

        self.assertIsNone(result)

    def test_deduplication_same_text(self):
        """Repeated copy of the exact same text returns None."""
        monitor = ClipboardMonitor(
            on_url_detected=lambda urls: None,
            poll_interval=0.01,
        )

        mock_user32 = MagicMock()
        mock_user32.GetClipboardSequenceNumber.return_value = 51
        monitor._user32 = mock_user32
        monitor._last_seq = 50
        monitor._last_text = "https://youtu.be/xyz123"

        with patch.object(
            monitor,
            "_read_clipboard_text_win32",
            return_value="https://youtu.be/xyz123",
        ):
            result = monitor.check_once()

        self.assertIsNone(result)

    def test_lifecycle_start_and_stop(self):
        """Monitor thread starts and stops gracefully without leaking."""
        cb = MagicMock()
        monitor = ClipboardMonitor(on_url_detected=cb, poll_interval=0.02)
        monitor.start()
        self.assertTrue(monitor.is_running())
        self.assertIsNotNone(monitor._thread)
        self.assertTrue(monitor._thread.is_alive())

        monitor.stop()
        self.assertFalse(monitor.is_running())
        if monitor._thread:
            monitor._thread.join(timeout=1.0)
            self.assertFalse(monitor._thread.is_alive())

    def test_sequence_not_advanced_on_read_failure_and_retries(self):
        """BUG-11: If _read_clipboard_text_win32 returns None (lock contention),
        _last_seq is NOT advanced, allowing subsequent polls to retry and capture URL."""
        monitor = ClipboardMonitor(
            on_url_detected=lambda urls: None,
            poll_interval=0.01,
        )

        mock_user32 = MagicMock()
        mock_user32.GetClipboardSequenceNumber.return_value = 200
        monitor._user32 = mock_user32
        monitor._last_seq = 100

        # Attempt 1: lock fails -> returns None
        with patch.object(monitor, "_read_clipboard_text_win32", return_value=None):
            result1 = monitor.check_once()

        self.assertIsNone(result1)
        # Sequence number must NOT advance to 200 on failure!
        self.assertEqual(monitor._last_seq, 100)

        # Attempt 2: retry succeeds with media URL
        with patch.object(
            monitor,
            "_read_clipboard_text_win32",
            return_value="https://youtu.be/captured_on_retry",
        ):
            result2 = monitor.check_once()

        self.assertEqual(result2, ["https://youtu.be/captured_on_retry"])
        self.assertEqual(monitor._last_seq, 200)


if __name__ == "__main__":
    unittest.main()
