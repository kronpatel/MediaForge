"""
clipboard_monitor.py – Background clipboard auto-detection for MediaForge Companion.

Monitors the Windows clipboard for copied media URLs (YouTube, Instagram, etc.)
with near-zero overhead by tracking Windows user32 GetClipboardSequenceNumber.
When a new supported media URL is detected, notifies listeners (UI / tray notification)
to offer one-click downloading or pre-fill the queue input.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from typing import Callable

from url_paste import extract_urls

logger = logging.getLogger("MediaForge.ClipboardMonitor")


class ClipboardMonitor:
    """Lightweight background thread that monitors the clipboard for media links."""

    def __init__(
        self,
        on_url_detected: Callable[[list[str]], None],
        is_enabled_fn: Callable[[], bool] | None = None,
        poll_interval: float = 0.8,
    ) -> None:
        self._on_url_detected = on_url_detected
        self._is_enabled_fn = is_enabled_fn or (lambda: True)
        self._poll_interval = poll_interval
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_seq = 0
        self._last_text = ""

        # Windows user32 API setup
        self._user32 = getattr(ctypes, "windll", None) and getattr(ctypes.windll, "user32", None)
        self._kernel32 = getattr(ctypes, "windll", None) and getattr(ctypes.windll, "kernel32", None)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        # Initialize last sequence number so we don't trigger on stale clipboard from before app launch
        if self._user32 and hasattr(self._user32, "GetClipboardSequenceNumber"):
            try:
                self._last_seq = self._user32.GetClipboardSequenceNumber()
            except Exception:
                self._last_seq = 0

        self._thread = threading.Thread(
            target=self._monitor_loop,
            name="MediaForgeClipboardMonitor",
            daemon=True,
        )
        self._thread.start()
        logger.info("[ClipboardMonitor] Started clipboard auto-detect service")

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        logger.info("[ClipboardMonitor] Stopped clipboard auto-detect service")

    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def _read_clipboard_text_win32(self) -> str | None:
        # Preferred: win32clipboard (safe 64-bit pywin32 C extension)
        try:
            import win32clipboard
            import win32con
            for _ in range(3):
                try:
                    win32clipboard.OpenClipboard()
                    try:
                        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                            data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                            return data if isinstance(data, str) else None
                        return None
                    finally:
                        win32clipboard.CloseClipboard()
                except Exception:
                    time.sleep(0.02)
            return None
        except ImportError:
            pass

        # Fallback: ctypes with explicit 64-bit pointers and restype
        if not self._user32 or not self._kernel32:
            return None
        try:
            self._user32.OpenClipboard.argtypes = [ctypes.c_void_p]
            self._user32.OpenClipboard.restype = ctypes.c_bool
            self._user32.CloseClipboard.argtypes = []
            self._user32.CloseClipboard.restype = ctypes.c_bool
            self._user32.GetClipboardData.argtypes = [ctypes.c_uint]
            self._user32.GetClipboardData.restype = ctypes.c_void_p
            self._kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
            self._kernel32.GlobalLock.restype = ctypes.c_void_p
            self._kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
            self._kernel32.GlobalUnlock.restype = ctypes.c_bool

            CF_UNICODETEXT = 13
            for _ in range(3):
                if self._user32.OpenClipboard(None):
                    try:
                        h_mem = self._user32.GetClipboardData(CF_UNICODETEXT)
                        if not h_mem:
                            return None
                        p_mem = self._kernel32.GlobalLock(h_mem)
                        if not p_mem:
                            return None
                        try:
                            return ctypes.c_wchar_p(p_mem).value
                        finally:
                            self._kernel32.GlobalUnlock(h_mem)
                    finally:
                        self._user32.CloseClipboard()
                time.sleep(0.02)
        except Exception:
            return None
        return None

    def check_once(self) -> list[str] | None:
        """Single check of clipboard status. Returns list of detected URLs or None."""
        if not self._is_enabled_fn():
            return None

        # Check sequence number first (0 CPU, 0 disk, 0 clipboard locking)
        current_seq = None
        if self._user32 and hasattr(self._user32, "GetClipboardSequenceNumber"):
            try:
                current_seq = self._user32.GetClipboardSequenceNumber()
                if current_seq == self._last_seq:
                    return None
            except Exception:
                current_seq = None

        text = self._read_clipboard_text_win32()
        if not text:
            # BUG-11: Do not advance sequence number on read failure (e.g. lock contention),
            # allowing subsequent polls to retry and capture the copied URL.
            return None

        if current_seq is not None:
            self._last_seq = current_seq

        if text == self._last_text:
            return None
        self._last_text = text

        urls = extract_urls(text)
        if not urls:
            return None

        return urls

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                urls = self.check_once()
                if urls:
                    logger.info("[ClipboardMonitor] Detected media URL(s): %s", urls)
                    try:
                        self._on_url_detected(urls)
                    except Exception as e:
                        logger.warning("[ClipboardMonitor] Error dispatching callback: %s", e)
            except Exception as e:
                logger.debug("[ClipboardMonitor] Loop error: %s", e)

            self._stop_event.wait(self._poll_interval)

