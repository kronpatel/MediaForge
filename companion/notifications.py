from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from queue import PriorityQueue
from typing import Any, Callable


# ------------------------------------------------------------------
# Source constants
# ------------------------------------------------------------------

SOURCE_BACKEND = "backend"
SOURCE_UPDATER = "updater"
SOURCE_INSTALLER = "installer"
SOURCE_SCHEDULER = "scheduler"
SOURCE_DASHBOARD = "dashboard"
SOURCE_UI = "ui"

# ------------------------------------------------------------------
# Priority constants
# ------------------------------------------------------------------

PRIORITY_CRITICAL = "critical"
PRIORITY_HIGH = "high"
PRIORITY_NORMAL = "normal"
PRIORITY_LOW = "low"


class PriorityValue(IntEnum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3

# ------------------------------------------------------------------
# Category constants
# ------------------------------------------------------------------

CATEGORY_DOWNLOAD_STARTED = "download.started"
CATEGORY_DOWNLOAD_COMPLETED = "download.completed"
CATEGORY_DOWNLOAD_FAILED = "download.failed"
CATEGORY_UPDATE_AVAILABLE = "update.available"
CATEGORY_UPDATE_DOWNLOADED = "update.downloaded"
CATEGORY_UPDATE_INSTALLED = "update.installed"
CATEGORY_UPDATE_FAILED = "update.failed"
CATEGORY_UPDATE_CANCELLED = "update.cancelled"
CATEGORY_SCHEDULE_STARTED = "schedule.started"
CATEGORY_SCHEDULE_COMPLETED = "schedule.completed"
CATEGORY_SCHEDULE_FAILED = "schedule.failed"
CATEGORY_BACKEND_STARTED = "backend.started"
CATEGORY_BACKEND_STOPPED = "backend.stopped"
CATEGORY_BACKEND_CRASHED = "backend.crashed"
CATEGORY_QUEUE_RESTORED = "queue.restored"
CATEGORY_DOWNLOAD_RECOVERED = "download.recovered"
CATEGORY_RECOVERY_FAILED = "recovery.failed"
CATEGORY_CLEANUP_COMPLETED = "cleanup.completed"
CATEGORY_QUEUE_CORRUPTED = "queue.corrupted"
CATEGORY_INFO = "info"
CATEGORY_WARNING = "warning"
CATEGORY_ERROR = "error"

# ------------------------------------------------------------------
# Internal mappings
# ------------------------------------------------------------------

_PRIORITY_MAP: dict[str, int] = {
    PRIORITY_CRITICAL: PriorityValue.CRITICAL,
    PRIORITY_HIGH: PriorityValue.HIGH,
    PRIORITY_NORMAL: PriorityValue.NORMAL,
    PRIORITY_LOW: PriorityValue.LOW,
}

QUEUE_MAX = 500
SCHEMA_VERSION = 1
HISTORY_SAVE_DEBOUNCE = 2.0

_QUEUE_DEFAULTS_PATH = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_HISTORY_PATH = os.path.join(
    _QUEUE_DEFAULTS_PATH, "cache", "notification_history.json"
)


def _priority_int(priority: str) -> int:
    return _PRIORITY_MAP.get(priority.lower(), PriorityValue.NORMAL)


# ------------------------------------------------------------------
# Notification dataclass
# ------------------------------------------------------------------

@dataclass
class Notification:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    version: int = 1
    timestamp: float = field(default_factory=time.time)
    category: str = ""
    priority: str = PRIORITY_NORMAL
    source: str = SOURCE_BACKEND
    title: str = ""
    message: str = ""
    action: str | None = None
    action_data: dict | None = None
    data: dict | None = None


# ------------------------------------------------------------------
# Internal queue item
# ------------------------------------------------------------------

class _QueueItem:
    def __init__(self, priority: int, counter: int, notification: Notification) -> None:
        self.priority = priority
        self.counter = counter
        self.notification = notification

    def __lt__(self, other: _QueueItem) -> bool:
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.counter < other.counter


Listener = Callable[[Notification], None]


def _serialize(obj: Notification) -> dict[str, Any]:
    return asdict(obj)


def _deserialize(data: dict[str, Any]) -> Notification:
    return Notification(**data)


# ------------------------------------------------------------------
# Windows Toast helper
# ------------------------------------------------------------------

try:
    import winrt.windows.ui.notifications as _win_notifications
    import winrt.windows.data.xml.dom as _win_xml
    _HAS_WINRT = True
except Exception:
    _HAS_WINRT = False


def _try_native_toast(title: str, message: str) -> bool:
    if not _HAS_WINRT:
        return False
    try:
        notifier = _win_notifications.ToastNotificationManager.create_toast_notifier()
        template = _win_notifications.ToastNotificationManager.get_template_content(
            _win_notifications.ToastTemplateType.toast_text02
        )
        text_nodes = template.get_elements_by_tag_name("text")
        text_nodes.item(0).append_child(template.create_text_node(title))
        if text_nodes.length > 1:
            text_nodes.item(1).append_child(template.create_text_node(message))
        notification = _win_notifications.ToastNotification(template)
        notifier.show(notification)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------
# NotificationManager
# ------------------------------------------------------------------

class NotificationManager:
    def __init__(
        self,
        logger: Any = None,
        tray_manager: Any = None,
        history_path: str | None = None,
        history_max: int = 200,
        duplicate_window: float = 5.0,
    ) -> None:
        self._logger = logger
        self._tray_manager = tray_manager

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._accepting = True

        self._queue: PriorityQueue = PriorityQueue(maxsize=QUEUE_MAX)
        self._counter = 0

        self._renderers: list[Listener] = []

        self._duplicate_window = duplicate_window
        self._recent_ids: set[str] = set()
        self._recent_titles: dict[str, float] = {}
        self._dup_lock = threading.Lock()

        self._quiet_hours_start: tuple[int, int] | None = None
        self._quiet_hours_end: tuple[int, int] | None = None
        self._quiet_hours_lock = threading.Lock()
        self._quiet_hours_enabled = True

        self._history_path = history_path or _DEFAULT_HISTORY_PATH
        self._history_max = history_max
        self._history: list[Notification] = []
        self._history_dirty = False
        self._history_last_save = 0.0

        self._stats_lock = threading.Lock()
        self._stats: dict[str, int] = {}
        self._queue_peak = 0

        self._thread: threading.Thread | None = None
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the notification worker thread and load persisted history."""
        with self._lock:
            if self._started:
                return
            self._stop_event.clear()
            self._accepting = True
            self._started = True
            self._load_history()
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="NotificationWorker",
                daemon=True,
            )
            self._thread.start()
            self._log("[NotificationManager] Queue started.")

    def shutdown(self) -> None:
        """Gracefully stop the worker, drain remaining items, and persist history."""
        with self._lock:
            if not self._started:
                return
            self._accepting = False
            self._started = False
        self._drain_queue()
        self._persist_history()
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._log("[NotificationManager] Queue stopped.")

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    def publish(
        self,
        category: str = "",
        title: str = "",
        message: str = "",
        source: str = SOURCE_BACKEND,
        priority: str = PRIORITY_NORMAL,
        action: str | None = None,
        action_data: dict | None = None,
        data: dict | None = None,
    ) -> Notification | None:
        """Queue a notification for asynchronous delivery."""
        if not self._accepting:
            return None

        notification = Notification(
            category=category,
            priority=priority,
            source=source,
            title=title,
            message=message,
            action=action,
            action_data=action_data,
            data=data,
        )
        prio_int = _priority_int(priority)
        with self._lock:
            self._counter += 1
            counter = self._counter

        queue_item = _QueueItem(prio_int, counter, notification)

        if self._queue.qsize() >= QUEUE_MAX:
            if prio_int >= PriorityValue.LOW:
                _increment_stat(self._stats, "dropped_notifications", self._stats_lock)
                return None
            try:
                self._queue.put(queue_item, block=False)
            except Exception:
                _increment_stat(self._stats, "dropped_notifications", self._stats_lock)
                return None
        else:
            self._queue.put(queue_item)

        qsize = self._queue.qsize()
        if qsize > self._queue_peak:
            self._queue_peak = qsize

        return notification

    # ------------------------------------------------------------------
    # Subscribe / unsubscribe
    # ------------------------------------------------------------------

    def subscribe(self, listener: Listener) -> None:
        """Register a renderer callback to receive every delivered notification."""
        with self._lock:
            if listener not in self._renderers:
                self._renderers.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        """Remove a previously registered renderer callback."""
        with self._lock:
            self._renderers = [r for r in self._renderers if r is not listener]

    # ------------------------------------------------------------------
    # Quiet hours
    # ------------------------------------------------------------------

    def set_quiet_hours(
        self,
        start: tuple[int, int] | None,
        end: tuple[int, int] | None,
    ) -> None:
        """Set the daily quiet hours window (inclusive, local 24h time)."""
        with self._quiet_hours_lock:
            self._quiet_hours_start = start
            self._quiet_hours_end = end

    def set_quiet_hours_enabled(self, enabled: bool) -> None:
        """Enable or disable quiet hours suppression."""
        with self._quiet_hours_lock:
            self._quiet_hours_enabled = enabled

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_history(self) -> list[Notification]:
        """Return a copy of the notification history."""
        with self._lock:
            return list(self._history)

    def clear_history(self) -> None:
        """Clear all history, statistics, and persist the empty state."""
        with self._lock:
            self._history.clear()
        with self._stats_lock:
            self._stats.clear()
            self._queue_peak = 0
        self._persist_history()

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, int]:
        """Return accumulated statistics (category/source/priority counts and peak queue depth)."""
        with self._stats_lock:
            stats = dict(self._stats)
            stats["queue_peak"] = self._queue_peak
            return stats

    # ------------------------------------------------------------------
    # Worker loop (private)
    # ------------------------------------------------------------------

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except Exception:
                self._flush_history_if_dirty()
                continue

            if self._stop_event.is_set():
                break
            self._process_item(item)
            self._flush_history_if_dirty()

    def _process_item(self, item: _QueueItem) -> None:
        notification = item.notification

        if self._is_duplicate(notification):
            _increment_stat(self._stats, "duplicate_count", self._stats_lock)
            return

        if self._is_quiet_hours():
            _increment_stat(self._stats, "suppressed_quiet_hours", self._stats_lock)
            return

        self._add_to_history(notification)
        self._update_stats(notification)

        toast_ok = self._render_toast(notification)
        if not toast_ok:
            _increment_stat(self._stats, "tray_fallbacks", self._stats_lock)
            self._render_tray(notification)

        self._render_activity_log(notification)
        self._call_subscribers(notification)

    # ------------------------------------------------------------------
    # Duplicate suppression
    # ------------------------------------------------------------------

    def _is_duplicate(self, notification: Notification) -> bool:
        now = time.monotonic()
        with self._dup_lock:
            if notification.id in self._recent_ids:
                return True

            expiry = now - self._duplicate_window
            stale = [t for t, ts in self._recent_titles.items() if ts < expiry]
            for s in stale:
                del self._recent_titles[s]

            if notification.title in self._recent_titles:
                return True

            self._recent_ids.add(notification.id)
            self._recent_titles[notification.title] = now
            return False

    # ------------------------------------------------------------------
    # Quiet hours check
    # ------------------------------------------------------------------

    def _is_quiet_hours(self) -> bool:
        with self._quiet_hours_lock:
            if not self._quiet_hours_enabled:
                return False
            start = self._quiet_hours_start
            end = self._quiet_hours_end
            if start is None or end is None:
                return False

        now = time.localtime()
        now_minutes = now.tm_hour * 60 + now.tm_min
        start_minutes = start[0] * 60 + start[1]
        end_minutes = end[0] * 60 + end[1]

        if start_minutes <= end_minutes:
            return start_minutes <= now_minutes <= end_minutes
        else:
            return now_minutes >= start_minutes or now_minutes <= end_minutes

    # ------------------------------------------------------------------
    # Rendering pipeline
    # ------------------------------------------------------------------

    def _render_toast(self, notification: Notification) -> bool:
        try:
            if _try_native_toast(notification.title, notification.message):
                return True
        except Exception:
            _increment_stat(self._stats, "toast_failures", self._stats_lock)
        return False

    def _render_tray(self, notification: Notification) -> None:
        if self._tray_manager is None:
            return
        try:
            self._tray_manager.notify(notification.title, notification.message)
        except Exception:
            _increment_stat(self._stats, "renderer_failures", self._stats_lock)

    def _call_subscribers(self, notification: Notification) -> None:
        renderers: list[Listener] = []
        with self._lock:
            renderers = list(self._renderers)
        for renderer in renderers:
            try:
                renderer(notification)
            except Exception:
                _increment_stat(self._stats, "renderer_failures", self._stats_lock)

    def _render_activity_log(self, notification: Notification) -> None:
        if self._logger is None:
            return
        try:
            self._logger.info(
                f"[NotificationManager] [{notification.category}] {notification.title}: {notification.message}"
            )
        except Exception:
            _increment_stat(self._stats, "renderer_failures", self._stats_lock)

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def _add_to_history(self, notification: Notification) -> None:
        with self._lock:
            self._history.append(notification)
            if len(self._history) > self._history_max:
                self._history = self._history[-self._history_max:]
            self._history_dirty = True

    def _flush_history_if_dirty(self) -> None:
        with self._lock:
            if not self._history_dirty:
                return
            now = time.monotonic()
            if now - self._history_last_save < HISTORY_SAVE_DEBOUNCE:
                return
        self._persist_history()

    def _drain_queue(self) -> None:
        processed = 0
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
            except Exception:
                break
            self._process_item(item)
            processed += 1
        if processed:
            self._log(f"[NotificationManager] Drained {processed} queued notification(s) during shutdown.")

    def _history_path_atomic(self) -> tuple[str, str]:
        tmp = self._history_path + ".tmp"
        return tmp, self._history_path

    def _load_history(self) -> None:
        path = self._history_path
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                schema_version = data.get("schema_version", 0)
                if schema_version != SCHEMA_VERSION:
                    self._log(
                        f"[NotificationManager] Unknown notification history schema version "
                        f"({schema_version}); starting with empty history."
                    )
                    return
                raw_list = data.get("notifications", [])
            elif isinstance(data, list):
                raw_list = data
            else:
                return
            history = []
            for entry in raw_list:
                try:
                    history.append(_deserialize(entry))
                except Exception:
                    continue
            with self._lock:
                self._history = history[-self._history_max:]
        except Exception as exc:
            self._log(f"[NotificationManager] Failed to load notification history: {exc}")

    def _persist_history(self) -> None:
        history_snapshot = self.get_history()
        try:
            raw_list = [_serialize(n) for n in history_snapshot]
            data = {
                "schema_version": SCHEMA_VERSION,
                "notifications": raw_list,
            }
            tmp_path, final_path = self._history_path_atomic()
            os.makedirs(os.path.dirname(final_path), exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp_path, final_path)
            with self._lock:
                self._history_dirty = False
                self._history_last_save = time.monotonic()
        except Exception as exc:
            self._log(f"[NotificationManager] Failed to persist notification history: {exc}")

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def _update_stats(self, notification: Notification) -> None:
        with self._stats_lock:
            key_category = f"category:{notification.category}"
            self._stats[key_category] = self._stats.get(key_category, 0) + 1
            key_source = f"source:{notification.source}"
            self._stats[key_source] = self._stats.get(key_source, 0) + 1
            key_priority = f"priority:{notification.priority}"
            self._stats[key_priority] = self._stats.get(key_priority, 0) + 1
            self._stats["total"] = self._stats.get("total", 0) + 1

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _log(self, message: str) -> None:
        if self._logger is not None:
            try:
                self._logger.info(message)
            except Exception:
                pass


def _increment_stat(stats: dict[str, int], key: str, lock: threading.Lock) -> None:
    with lock:
        stats[key] = stats.get(key, 0) + 1


# ------------------------------------------------------------------
# Singleton API
# ------------------------------------------------------------------

_instance: NotificationManager | None = None


def init_notification_manager(
    logger: Any = None,
    tray_manager: Any = None,
    history_path: str | None = None,
    history_max: int = 200,
    duplicate_window: float = 5.0,
) -> NotificationManager:
    """Initialize and start the global NotificationManager singleton."""
    global _instance
    if _instance is not None:
        return _instance
    manager = NotificationManager(
        logger=logger,
        tray_manager=tray_manager,
        history_path=history_path,
        history_max=history_max,
        duplicate_window=duplicate_window,
    )
    manager.start()
    _instance = manager
    return manager


def get_notification_manager() -> NotificationManager:
    """Return the initialized global NotificationManager singleton."""
    global _instance
    if _instance is None:
        raise RuntimeError(
            "NotificationManager has not been initialized. "
            "Call init_notification_manager() first."
        )
    return _instance
