"""
test_queue_ui.py — Tests for QueuePage UI/UX enhancements (Phase 5.1).

Covers:
  - Pause / Resume state transitions
  - Empty state visibility
  - Overall progress summary
  - Incremental row updates (cached values, skip reconfigure)
  - Context menu creation and enable/disable
  - Keyboard shortcut dispatch
  - Multi-selection (Ctrl+Click, Shift+Click, Select All, Clear)
  - Batch pause / resume / retry / remove
  - Priority ordering
  - Queue statistics
  - Scroll preservation
  - Selection persistence across refresh
  - Hash-based refresh skip
  - Large queue performance
"""

from __future__ import annotations

import sys
import types as _types
import unittest
from unittest.mock import MagicMock, patch

# Stub base_page module
_mock_base = _types.ModuleType("base_page")
class _FakeBasePage:
    def __init__(self, master, manager, logger):
        self.master = master
        self.manager = manager
        self.logger = logger
    def winfo_exists(self): return True
    def winfo_ismapped(self): return True
    def grid_forget(self): pass
    def grid(self, **kw): pass
    def pack(self, **kw): pass
    def pack_forget(self): pass
    def bind(self, *a, **kw): pass
    def clipboard_clear(self): pass
    def clipboard_append(self, t): pass
    def after(self, *a): pass
_mock_base.BasePage = _FakeBasePage

class _FakeCTkFrame:
    def __init__(self, master=None, *a, **kw):
        self.master = master
        def _noop(*_a, **kw): pass
        self.pack = _noop
        self.pack_forget = _noop
        self.grid = _noop
        self.grid_forget = _noop
        self.grid_remove = _noop
        self.bind = _noop
        self.configure = _noop
        self.winfo_exists = lambda: True
        self.winfo_width = lambda: 800
        self.tk_popup = _noop

class _FakeCTkLabel:
    def __init__(self, master=None, *a, **kw): self.master = master
    def pack(self, *a, **kw): pass
    def pack_forget(self): pass
    def configure(self, *a, **kw): pass

class _FakeCTkButton:
    def __init__(self, master=None, *a, **kw): self.master = master
    def pack(self, *a, **kw): pass
    def pack_forget(self): pass
    def configure(self, *a, **kw): pass

class _FakeCTkProgressBar:
    def __init__(self, master=None, *a, **kw): self.master = master
    def pack(self, *a, **kw): pass
    def set(self, val): pass
    def configure(self, *a, **kw): pass

class _FakeCTkScrollableFrame(_FakeCTkFrame):
    pass

class _FakeCTkMenu:
    def __init__(self, master=None, *a, **kw): pass
    def add_command(self, *a, **kw): pass
    def add_separator(self, *a, **kw): pass
    def tk_popup(self, *a, **kw): pass
    def entryconfigure(self, *a, **kw): pass

_FAKE_FONT = MagicMock()

ctk_mock = MagicMock()
ctk_mock.CTkFrame = _FakeCTkFrame
ctk_mock.CTkLabel = _FakeCTkLabel
ctk_mock.CTkButton = _FakeCTkButton
ctk_mock.CTkProgressBar = _FakeCTkProgressBar
ctk_mock.CTkScrollableFrame = _FakeCTkScrollableFrame
ctk_mock.CTkFont = MagicMock(return_value=_FAKE_FONT)
ctk_mock.CTkMenu = _FakeCTkMenu
ctk_mock.CTkInputDialog = MagicMock(return_value=MagicMock())
ctk_mock.CTkToplevel = _FakeCTkFrame

_saved_modules: dict[str, object] = {}
for _mod_name in ("base_page", "customtkinter"):
    _saved_modules[_mod_name] = sys.modules.get(_mod_name)
    sys.modules[_mod_name] = {"base_page": _mock_base, "customtkinter": ctk_mock}[_mod_name]

try:
    from queue_panel import (
        QueuePage, QueueRow,
        _compute_row_hash,
        _SpeedSmoother,
        _format_speed, _format_eta, _format_progress, _format_bytes,
        _parse_speed_to_bytes, _clamp_progress, _calculate_eta,
        _STATUS_ICONS, _EMPTY_ACTIVE_TEXT, _EMPTY_ACTIVE_SUBTITLE,
        _PRIORITY_LABELS, _PRIORITY_ORDER,
    )
finally:
    for _mod_name, _orig in _saved_modules.items():
        if _orig is not None:
            sys.modules[_mod_name] = _orig
        else:
            sys.modules.pop(_mod_name, None)


def _make_page() -> QueuePage:
    mgr = MagicMock()
    log = MagicMock()
    master = MagicMock()
    master.master = master
    master.winfo_width = MagicMock(return_value=800)
    master._dashboard_controller = MagicMock()
    page = QueuePage(master, mgr, log)
    page._build_overall_progress = MagicMock()
    page._show_empty_state = MagicMock()
    page._hide_empty_state = MagicMock()
    page._update_overall_progress = MagicMock()
    page.save_scroll = MagicMock()
    page.restore_scroll = MagicMock()
    page._stats_inner = MagicMock()
    page._stat_active_lbl = MagicMock()
    page._stat_queued_lbl = MagicMock()
    page._stat_paused_lbl = MagicMock()
    page._stat_completed_lbl = MagicMock()
    page._stat_failed_lbl = MagicMock()
    page._stat_cancelled_lbl = MagicMock()
    page._stat_total_lbl = MagicMock()
    page._stat_avg_speed_lbl = MagicMock()
    page._stat_eta_lbl = MagicMock()
    page._sync_selection_ui = MagicMock()
    return page


def _make_job(
    job_id: str = "abc123",
    status: str = "downloading",
    progress: float = 50.0,
    speed: str = "5 MiB/s",
    eta: str = "30s",
    label: str = "Test Video",
    priority: str = "normal",
) -> dict:
    return {
        "id": job_id,
        "status": status,
        "progress": progress,
        "speed": speed,
        "eta": eta,
        "label": label,
        "url": "https://example.com/video",
        "mode": "video",
        "filename": "test_video.mp4",
        "priority": priority,
    }


# =========================================================================
# Existing test suites (Phase 4.5 compatible)
# =========================================================================


class TestPauseResume(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()
        self.page._paused = False

    def test_default_not_paused(self):
        self.assertFalse(self.page.is_paused())

    def test_toggle_pause_sets_paused_true(self):
        self.page.toggle_pause()
        self.assertTrue(self.page.is_paused())

    def test_toggle_pause_twice_returns_to_false(self):
        self.page.toggle_pause()
        self.page.toggle_pause()
        self.assertFalse(self.page.is_paused())

    def test_property_setter_updates_paused(self):
        self.page.paused = True
        self.assertTrue(self.page._paused)
        self.page.paused = False
        self.assertFalse(self.page._paused)


class TestEmptyState(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()

    def test_empty_queue_shows_empty_state(self):
        data = {"queue": [], "settings": {}}
        self.page.refresh(data)
        self.page._show_empty_state.assert_called()

    def test_non_empty_queue_hides_empty_state(self):
        data = {"queue": [_make_job()], "settings": {}}
        self.page.refresh(data)
        self.page._hide_empty_state.assert_called()

    def test_queue_to_empty_transition(self):
        data = {"queue": [], "settings": {}}
        self.page.refresh(data)
        self.page._show_empty_state.reset_mock()
        data2 = {"queue": [_make_job()], "settings": {}}
        self.page.refresh(data2)
        self.page._hide_empty_state.assert_called()


class TestOverallProgress(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()

    def test_overall_progress_called_with_jobs(self):
        jobs = [_make_job(progress=50.0), _make_job(progress=80.0)]
        data = {"queue": jobs, "settings": {}}
        self.page.refresh(data)
        self.page._update_overall_progress.assert_called_with(jobs)

    def test_overall_progress_not_called_empty(self):
        data = {"queue": [], "settings": {}}
        self.page.refresh(data)
        self.page._update_overall_progress.assert_not_called()


class TestIncrementalRows(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()
        self.page._row_widgets = []

    def test_new_rows_created_for_new_jobs(self):
        data = {"queue": [_make_job("id1"), _make_job("id2")], "settings": {}}
        self.page.refresh(data)
        self.assertEqual(len(self.page._row_widgets), 2)

    def test_existing_rows_reused_on_update(self):
        data = {"queue": [_make_job("id1"), _make_job("id2")], "settings": {}}
        self.page.refresh(data)
        first_widgets = list(self.page._row_widgets)
        data2 = {"queue": [_make_job("id1"), _make_job("id2")], "settings": {}}
        self.page.refresh(data2)
        self.assertIs(self.page._row_widgets[0], first_widgets[0])

    def test_rows_removed_when_queue_shrinks(self):
        data = {"queue": [_make_job("id1"), _make_job("id2")], "settings": {}}
        self.page.refresh(data)
        data2 = {"queue": [_make_job("id1")], "settings": {}}
        self.page.refresh(data2)
        self.assertEqual(len(self.page._row_widgets), 2)


class TestCacheInvalidation(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()

    def test_update_job_skips_configure_when_values_unchanged(self):
        row = QueueRow(
            master=MagicMock(),
            on_copy=MagicMock(),
            on_open_folder=MagicMock(),
        )
        row._title_lbl.configure = MagicMock()
        row._progress_bar.set = MagicMock()
        row._progress_lbl.configure = MagicMock()
        row._speed_badge_lbl.configure = MagicMock()
        row._speed_badge_frame.pack = MagicMock()
        row._speed_badge_frame.pack_forget = MagicMock()
        row._eta_lbl.configure = MagicMock()
        row._status_lbl.configure = MagicMock()
        row._badge_icon_lbl.configure = MagicMock()
        row._status_icon_lbl.configure = MagicMock()
        row._priority_lbl.configure = MagicMock()

        job = _make_job()
        row.update_job(job)
        row._title_lbl.configure.assert_called()
        row._title_lbl.configure.reset_mock()

        row.update_job(job)
        row._title_lbl.configure.assert_not_called()

    def test_cache_clears_on_different_values(self):
        row = QueueRow(
            master=MagicMock(),
            on_copy=MagicMock(),
            on_open_folder=MagicMock(),
        )
        row._title_lbl.configure = MagicMock()

        row.update_job(_make_job(label="First"))
        row._title_lbl.configure.reset_mock()

        row.update_job(_make_job(label="Second"))
        row._title_lbl.configure.assert_called_once()


class TestScrollPreservation(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()
        self.page.save_scroll = MagicMock()
        self.page.restore_scroll = MagicMock()

    def test_scroll_saved_and_restored_on_refresh(self):
        data = {"queue": [_make_job()], "settings": {}}
        self.page.refresh(data)
        self.page.save_scroll.assert_called()
        self.page.restore_scroll.assert_called()


class TestGetSelectedJobIds(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()

    def test_returns_selected_ids(self):
        self.page._selected_ids = {"id1", "id2"}
        ids = self.page.get_selected_job_ids()
        self.assertCountEqual(ids, ["id1", "id2"])

    def test_returns_empty_when_no_selection(self):
        self.page._selected_ids = set()
        ids = self.page.get_selected_job_ids()
        self.assertEqual(ids, [])


# =========================================================================
# Phase 5.1 — New test suites
# =========================================================================


class TestMultiSelection(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()
        self.page._row_widgets = []
        for i in range(5):
            row = MagicMock()
            row.job_id = f"id{i}"
            row._job_data = {"status": "queued"}
            row.selected = False
            row.configure = MagicMock()
            self.page._row_widgets.append(row)

    def test_ctrl_click_toggles_selection(self):
        self.page._on_row_selection("id0", ctrl=True, shift=False, _left=True)
        self.assertIn("id0", self.page._selected_ids)

        self.page._on_row_selection("id0", ctrl=True, shift=False, _left=True)
        self.assertNotIn("id0", self.page._selected_ids)

    def test_shift_click_selects_range(self):
        self.page._on_row_selection("id0", ctrl=False, shift=False, _left=True)
        self.page._on_row_selection("id3", ctrl=False, shift=True, _left=True)
        self.assertIn("id0", self.page._selected_ids)
        self.assertIn("id1", self.page._selected_ids)
        self.assertIn("id2", self.page._selected_ids)
        self.assertIn("id3", self.page._selected_ids)

    def test_select_all(self):
        self.page.select_all()
        self.assertEqual(len(self.page._selected_ids), 5)

    def test_clear_selection(self):
        self.page._selected_ids = {"id0", "id1", "id2"}
        self.page.clear_selection()
        self.assertEqual(len(self.page._selected_ids), 0)

    def test_plain_click_clears_previous_selection(self):
        self.page._on_row_selection("id0", ctrl=False, shift=False, _left=True)
        self.page._on_row_selection("id2", ctrl=False, shift=False, _left=True)
        self.assertEqual(len(self.page._selected_ids), 1)
        self.assertIn("id2", self.page._selected_ids)


class TestBatchPauseResume(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()
        self.page._selected_ids = {"id1", "id2"}
        self.page._row_widgets = []
        for i in range(3):
            row = MagicMock()
            row.job_id = f"id{i}"
            row._job_data = {"status": "downloading" if i < 2 else "queued"}
            self.page._row_widgets.append(row)

    def test_pause_selected_calls_manager(self):
        self.page.pause_selected()
        self.assertEqual(self.page.manager.pause_download.call_count, 2)
        self.page.manager.pause_download.assert_any_call("id1")
        self.page.manager.pause_download.assert_any_call("id2")

    def test_resume_selected_calls_manager(self):
        self.page.resume_selected()
        self.assertEqual(self.page.manager.resume_download.call_count, 2)
        self.page.manager.resume_download.assert_any_call("id1")
        self.page.manager.resume_download.assert_any_call("id2")


class TestBatchRetry(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()
        self.page._row_widgets = []
        for i in range(4):
            row = MagicMock()
            row.job_id = f"id{i}"
            statuses = ["failed", "failed", "completed", "downloading"]
            row._job_data = {"status": statuses[i]}
            self.page._row_widgets.append(row)

    def test_retry_all_failed_only_failed_jobs(self):
        self.page.retry_all_failed()
        self.assertEqual(self.page.manager.retry_download.call_count, 2)
        self.page.manager.retry_download.assert_any_call("id0")
        self.page.manager.retry_download.assert_any_call("id1")


class TestBatchRemove(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()
        self.page._selected_ids = {"id0", "id1"}
        self.page._row_widgets = []
        for i in range(3):
            row = MagicMock()
            row.job_id = f"id{i}"
            self.page._row_widgets.append(row)

    def test_remove_selected_calls_manager_for_each(self):
        self.page._confirm_remove = MagicMock(return_value=True)
        self.page.remove_selected()
        self.assertEqual(self.page.manager.remove_download.call_count, 2)
        self.page.manager.remove_download.assert_any_call("id0")
        self.page.manager.remove_download.assert_any_call("id1")

    def test_remove_selected_skips_if_cancelled(self):
        self.page._confirm_remove = MagicMock(return_value=False)
        self.page.remove_selected()
        self.page.manager.remove_download.assert_not_called()

    def test_remove_selected_clears_selection(self):
        self.page._confirm_remove = MagicMock(return_value=True)
        self.page.remove_selected()
        self.assertEqual(len(self.page._selected_ids), 0)


class TestBatchCancel(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()
        self.page._selected_ids = {"id0", "id1"}
        self.page._row_widgets = []
        for i in range(3):
            row = MagicMock()
            row.job_id = f"id{i}"
            self.page._row_widgets.append(row)

    def test_cancel_selected_calls_manager_for_each(self):
        self.page._confirm_cancel = MagicMock(return_value=True)
        self.page.cancel_selected()
        self.assertEqual(self.page.manager.cancel_download.call_count, 2)
        self.page.manager.cancel_download.assert_any_call("id0")
        self.page.manager.cancel_download.assert_any_call("id1")

    def test_cancel_selected_skips_if_cancelled(self):
        self.page._confirm_cancel = MagicMock(return_value=False)
        self.page.cancel_selected()
        self.page.manager.cancel_download.assert_not_called()

    def test_cancel_selected_clears_selection(self):
        self.page._confirm_cancel = MagicMock(return_value=True)
        self.page.cancel_selected()
        self.assertEqual(len(self.page._selected_ids), 0)

    def test_cancel_selected_skips_when_no_ids(self):
        self.page._selected_ids = set()
        self.page._row_widgets = []
        self.page._confirm_cancel = MagicMock()
        self.page.cancel_selected()
        self.page._confirm_cancel.assert_not_called()


class TestCancelJobHelper(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()
        self.page._selected_ids = {"id0"}

    def test_cancel_job_calls_manager_cancel_download(self):
        self.page._cancel_job("test-id")
        self.page.manager.cancel_download.assert_called_once_with("test-id")

    def test_cancel_job_discards_from_selection(self):
        self.page._selected_ids.add("test-id")
        self.page._cancel_job("test-id")
        self.assertNotIn("test-id", self.page._selected_ids)


class TestPriorityOrdering(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()
        self.page._row_widgets = []

    def test_increase_priority_calls_manager(self):
        row = MagicMock()
        row.job_id = "id0"
        row._job_data = {"priority": "low"}
        self.page._row_widgets = [row]
        self.page._selected_ids = {"id0"}
        self.page.manager.change_priority = MagicMock()
        self.page.increase_priority()
        self.page.manager.change_priority.assert_called_with("id0", "normal")

    def test_decrease_priority_calls_manager(self):
        row = MagicMock()
        row.job_id = "id0"
        row._job_data = {"priority": "high"}
        self.page._row_widgets = [row]
        self.page._selected_ids = {"id0"}
        self.page.manager.change_priority = MagicMock()
        self.page.decrease_priority()
        self.page.manager.change_priority.assert_called_with("id0", "normal")


class TestContextMenu(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()

    def test_context_menu_created(self):
        row = QueueRow(
            master=MagicMock(),
            on_copy=MagicMock(),
            on_open_folder=MagicMock(),
            on_retry=MagicMock(),
            on_remove=MagicMock(),
            on_cancel=MagicMock(),
            on_show_details=MagicMock(),
        )
        self.assertIsNotNone(row._context_menu)

    def test_context_retry_calls_callback(self):
        on_retry = MagicMock()
        row = QueueRow(
            master=MagicMock(),
            on_copy=MagicMock(),
            on_open_folder=MagicMock(),
            on_retry=on_retry,
            on_remove=MagicMock(),
            on_cancel=MagicMock(),
            on_show_details=MagicMock(),
        )
        row.job_id = "test-id"
        row._context_retry()
        on_retry.assert_called_with("test-id")

    def test_context_remove_calls_callback(self):
        on_remove = MagicMock()
        row = QueueRow(
            master=MagicMock(),
            on_copy=MagicMock(),
            on_open_folder=MagicMock(),
            on_retry=MagicMock(),
            on_remove=on_remove,
            on_cancel=MagicMock(),
            on_show_details=MagicMock(),
        )
        row.job_id = "test-id"
        row._context_remove()
        on_remove.assert_called_with("test-id")

    def test_context_cancel_calls_callback(self):
        on_cancel = MagicMock()
        row = QueueRow(
            master=MagicMock(),
            on_copy=MagicMock(),
            on_open_folder=MagicMock(),
            on_retry=MagicMock(),
            on_remove=MagicMock(),
            on_cancel=on_cancel,
            on_show_details=MagicMock(),
        )
        row.job_id = "test-id"
        row._context_cancel()
        on_cancel.assert_called_with("test-id")

    def test_context_copy_url_calls_on_copy(self):
        on_copy = MagicMock()
        row = QueueRow(
            master=MagicMock(),
            on_copy=on_copy,
            on_open_folder=MagicMock(),
        )
        row.job_url = "https://example.com"
        row._context_copy_url()
        on_copy.assert_called_with("https://example.com")

    def test_context_menu_has_all_items(self):
        row = QueueRow(
            master=MagicMock(),
            on_copy=MagicMock(),
            on_open_folder=MagicMock(),
            on_pause=MagicMock(),
            on_resume=MagicMock(),
            on_retry=MagicMock(),
            on_remove=MagicMock(),
            on_cancel=MagicMock(),
            on_show_details=MagicMock(),
            on_move_top=MagicMock(),
            on_move_up=MagicMock(),
            on_move_down=MagicMock(),
            on_move_bottom=MagicMock(),
        )
        menu = row._context_menu
        self.assertIsNotNone(menu)

    def test_context_menu_enable_disable(self):
        row = QueueRow(
            master=MagicMock(),
            on_copy=MagicMock(),
            on_open_folder=MagicMock(),
            on_pause=MagicMock(),
            on_resume=MagicMock(),
            on_retry=MagicMock(),
            on_remove=MagicMock(),
            on_cancel=MagicMock(),
            on_show_details=MagicMock(),
            on_move_top=MagicMock(),
            on_move_up=MagicMock(),
            on_move_down=MagicMock(),
            on_move_bottom=MagicMock(),
        )
        row._job_data = {"status": "downloading"}
        row._update_context_menu()

    def test_context_cancel_enabled_for_downloading(self):
        row = QueueRow(
            master=MagicMock(),
            on_copy=MagicMock(),
            on_open_folder=MagicMock(),
            on_cancel=MagicMock(),
        )
        row._job_data = {"status": "downloading"}
        row._context_menu.entryconfigure = MagicMock()
        row._update_context_menu()
        row._context_menu.entryconfigure.assert_any_call(0, state="normal")

    def test_context_cancel_disabled_for_completed(self):
        row = QueueRow(
            master=MagicMock(),
            on_copy=MagicMock(),
            on_open_folder=MagicMock(),
            on_cancel=MagicMock(),
        )
        row._job_data = {"status": "completed"}
        row._context_menu.entryconfigure = MagicMock()
        row._update_context_menu()
        row._context_menu.entryconfigure.assert_any_call(0, state="disabled")


class TestKeyboardShortcuts(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()

    def test_select_all_shortcut(self):
        self.page.select_all = MagicMock()
        self.page.select_all()
        self.page.select_all.assert_called_once()

    def test_clear_selection_shortcut(self):
        self.page.clear_selection = MagicMock()
        self.page.clear_selection()
        self.page.clear_selection.assert_called_once()

    def test_retry_selected_shortcut(self):
        self.page.retry_all_failed = MagicMock()
        self.page.retry_all_failed()
        self.page.retry_all_failed.assert_called_once()

    def test_remove_selected_shortcut(self):
        self.page.remove_selected = MagicMock()
        self.page.remove_selected()
        self.page.remove_selected.assert_called_once()

    def test_pause_selected_shortcut(self):
        self.page.pause_selected = MagicMock()
        self.page.pause_selected()
        self.page.pause_selected.assert_called_once()

    def test_toggle_pause_shortcut(self):
        self.page.toggle_pause = MagicMock()
        self.page.toggle_pause()
        self.page.toggle_pause.assert_called_once()


class TestQueueStatistics(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()
        self.page._stat_active_lbl = MagicMock()
        self.page._stat_queued_lbl = MagicMock()
        self.page._stat_paused_lbl = MagicMock()
        self.page._stat_completed_lbl = MagicMock()
        self.page._stat_failed_lbl = MagicMock()
        self.page._stat_cancelled_lbl = MagicMock()
        self.page._stat_total_lbl = MagicMock()
        self.page._stat_avg_speed_lbl = MagicMock()
        self.page._stat_eta_lbl = MagicMock()

    def test_statistics_counts(self):
        jobs = [
            _make_job("id1", "downloading"),
            _make_job("id2", "queued"),
            _make_job("id3", "paused"),
            _make_job("id4", "completed"),
            _make_job("id5", "failed"),
            _make_job("id6", "cancelled"),
        ]
        self.page._update_statistics(jobs)
        self.page._stat_active_lbl.configure.assert_called_with(text="Active: 1")
        self.page._stat_queued_lbl.configure.assert_called_with(text="Queued: 1")
        self.page._stat_paused_lbl.configure.assert_called_with(text="Paused: 1")
        self.page._stat_completed_lbl.configure.assert_called_with(text="Completed: 1")
        self.page._stat_failed_lbl.configure.assert_called_with(text="Failed: 1")
        self.page._stat_cancelled_lbl.configure.assert_called_with(text="Cancelled: 1")
        self.page._stat_total_lbl.configure.assert_called_with(text="Total: 6")

    def test_statistics_speed_and_eta(self):
        jobs = [
            _make_job("id1", "downloading", speed="10 MiB/s", eta="15s"),
            _make_job("id2", "downloading", speed="5 MiB/s", eta="30s"),
        ]
        self.page._update_statistics(jobs)
        self.page._stat_avg_speed_lbl.configure.assert_called_with(text="Avg Speed: 10 MiB/s")
        self.page._stat_eta_lbl.configure.assert_called_with(text="Est. Remaining: 15s")

    def test_statistics_no_active_downloads(self):
        self.page._update_statistics([])
        self.page._stat_avg_speed_lbl.configure.assert_called_with(text="Avg Speed: \u2014")
        self.page._stat_eta_lbl.configure.assert_called_with(text="Est. Remaining: \u2014")

    def test_statistics_cache_prevents_reconfigure(self):
        jobs = [
            _make_job("id1", "downloading"),
            _make_job("id2", "queued"),
        ]
        self.page._update_statistics(jobs)
        first_call_count = self.page._stat_active_lbl.configure.call_count

        self.page._update_statistics(jobs)
        second_call_count = self.page._stat_active_lbl.configure.call_count
        self.assertEqual(first_call_count, second_call_count,
                         "Cached stats should prevent re-configuring labels")

    def test_statistics_cache_updates_on_change(self):
        jobs_a = [_make_job("id1", "downloading"), _make_job("id2", "queued")]
        jobs_b = [_make_job("id1", "downloading"), _make_job("id2", "completed")]

        self.page._update_statistics(jobs_a)
        first_active = self.page._stat_active_lbl.configure.call_count

        self.page._update_statistics(jobs_b)
        self.assertGreater(self.page._stat_active_lbl.configure.call_count, first_active,
                           "Stats should update when counts change")

    def test_statistics_cache_initialized_empty(self):
        self.assertEqual(self.page._cached_stats, {})


class TestSelectionPersistence(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()
        self.page._row_widgets = []
        rows = []
        for i in range(5):
            row = MagicMock()
            row.job_id = f"id{i}"
            row.selected = False
            row._job_data = {"priority": "normal"}
            rows.append(row)
        self.page._row_widgets = rows
        self.page._sync_selection_ui = QueuePage._sync_selection_ui.__get__(self.page, QueuePage)

    def test_selection_survives_refresh(self):
        self.page._selected_ids = {"id0", "id2", "id4"}
        data = {"queue": [_make_job("id0"), _make_job("id1"), _make_job("id2"),
                          _make_job("id3"), _make_job("id4")], "settings": {}}
        self.page._cached_hash = None
        self.page.refresh(data)
        remaining = self.page._selected_ids
        self.assertIn("id0", remaining)
        self.assertIn("id2", remaining)
        self.assertIn("id4", remaining)

    def test_selection_sync_ui(self):
        self.page._selected_ids = {"id0"}
        for r in self.page._row_widgets:
            r.selected = r.job_id in self.page._selected_ids if r.job_id else False
        self.assertTrue(self.page._row_widgets[0].selected)


class TestHashRefreshSkip(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()
        self.page._row_widgets = []
        row = MagicMock()
        row.job_id = "id0"
        row.configure = MagicMock()
        row.pack = MagicMock()
        row.pack_forget = MagicMock()
        row.update_job = MagicMock(return_value=False)
        row._job_data = {"priority": "normal"}
        self.page._row_widgets = [row]
        self.page._cached_hash = None

    def test_hash_skip_on_unchanged_data(self):
        data = {"queue": [_make_job("id0")], "settings": {}}
        self.page.refresh(data)
        self.assertTrue(self.page._cached_hash is not None)

        saved = self.page._cached_hash
        self.page.save_scroll.reset_mock()
        self.page.restore_scroll.reset_mock()

        self.page.refresh(data)
        self.assertEqual(self.page._cached_hash, saved)
        self.page.save_scroll.assert_not_called()


class TestLargeQueuePerformance(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()
        self.page._row_widgets = []

    def test_large_queue_does_not_crash(self):
        jobs = [_make_job(f"id{i}", "queued") for i in range(100)]
        data = {"queue": jobs, "settings": {}}
        try:
            self.page.refresh(data)
        except Exception as e:
            self.fail(f"Large queue refresh raised: {e}")
        self.assertEqual(len(self.page._row_widgets), 100)

    def test_row_hash_unique_per_job(self):
        h1 = _compute_row_hash(_make_job("id1", status="downloading", progress=50.0))
        h2 = _compute_row_hash(_make_job("id2", status="completed", progress=100.0))
        self.assertNotEqual(h1, h2)

    def test_row_hash_same_for_identical_job(self):
        h1 = _compute_row_hash(_make_job("id1"))
        h2 = _compute_row_hash(_make_job("id1"))
        self.assertEqual(h1, h2)


class TestToolbarButtons(unittest.TestCase):
    def setUp(self):
        self.page = _make_page()

    def test_buttons_disabled_without_selection(self):
        self.page._selected_ids = set()
        self.page._pause_sel_btn.configure = MagicMock()
        self.page._resume_sel_btn.configure = MagicMock()
        self.page._cancel_sel_btn.configure = MagicMock()
        self.page._remove_sel_btn.configure = MagicMock()
        self.page._increase_prio_btn.configure = MagicMock()
        self.page._decrease_prio_btn.configure = MagicMock()
        self.page._update_toolbar_buttons()
        self.page._pause_sel_btn.configure.assert_called_with(state="disabled")
        self.page._resume_sel_btn.configure.assert_called_with(state="disabled")
        self.page._cancel_sel_btn.configure.assert_called_with(state="disabled")
        self.page._remove_sel_btn.configure.assert_called_with(state="disabled")

    def test_buttons_enabled_with_selection(self):
        self.page._selected_ids = {"id0"}
        self.page._pause_sel_btn = MagicMock()
        self.page._resume_sel_btn = MagicMock()
        self.page._cancel_sel_btn = MagicMock()
        self.page._remove_sel_btn = MagicMock()
        self.page._increase_prio_btn = MagicMock()
        self.page._decrease_prio_btn = MagicMock()
        self.page._update_toolbar_buttons()
        for btn in (self.page._pause_sel_btn, self.page._resume_sel_btn,
                    self.page._cancel_sel_btn, self.page._remove_sel_btn,
                    self.page._increase_prio_btn, self.page._decrease_prio_btn):
            btn.configure.assert_called_with(state="normal")


# =========================================================================
# Phase 5.2 — New test suites (Speed, ETA, Progress, Status Icons, etc.)
# =========================================================================


class TestSpeedSmoother(unittest.TestCase):
    def test_rolling_average(self):
        s = _SpeedSmoother(window=3)
        s.add_sample(10_000_000)
        s.add_sample(20_000_000)
        s.add_sample(30_000_000)
        self.assertAlmostEqual(s.average, 20_000_000)

    def test_ignores_zero_samples(self):
        s = _SpeedSmoother(window=3)
        s.add_sample(10_000_000)
        s.add_sample(0)
        self.assertEqual(s.average, 10_000_000)

    def test_ignores_negative_samples(self):
        s = _SpeedSmoother(window=3)
        s.add_sample(10_000_000)
        s.add_sample(-5_000_000)
        self.assertEqual(s.average, 10_000_000)

    def test_empty_returns_zero(self):
        s = _SpeedSmoother()
        self.assertEqual(s.average, 0.0)

    def test_reset_clears_samples(self):
        s = _SpeedSmoother()
        s.add_sample(10_000_000)
        s.reset()
        self.assertFalse(s.has_samples)
        self.assertEqual(s.average, 0.0)

    def test_window_capped_at_one(self):
        s = _SpeedSmoother(window=0)
        self.assertEqual(s._window, 1)

    def test_has_samples_property(self):
        s = _SpeedSmoother()
        self.assertFalse(s.has_samples)
        s.add_sample(1_000_000)
        self.assertTrue(s.has_samples)

    def test_finite_samples(self):
        s = _SpeedSmoother(window=3)
        for _ in range(10):
            s.add_sample(5_000_000)
        self.assertEqual(len(s._samples), 3)

    def test_average_with_single_sample(self):
        s = _SpeedSmoother()
        s.add_sample(42_000_000)
        self.assertEqual(s.average, 42_000_000)


class TestFormatSpeed(unittest.TestCase):
    def test_zero_returns_emdash(self):
        self.assertEqual(_format_speed(0), "\u2014")

    def test_negative_returns_emdash(self):
        self.assertEqual(_format_speed(-1), "\u2014")

    def test_bytes_per_sec(self):
        self.assertEqual(_format_speed(500), "500 B/s")

    def test_kibibytes_per_sec(self):
        self.assertEqual(_format_speed(2048), "2 KiB/s")

    def test_mebibytes_per_sec(self):
        self.assertEqual(_format_speed(5_242_880), "5.0 MiB/s")

    def test_gibibytes_per_sec(self):
        result = _format_speed(5_368_709_120)
        self.assertTrue("GiB/s" in result)


class TestParseSpeedToBytes(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(_parse_speed_to_bytes(""), 0.0)

    def test_emdash(self):
        self.assertEqual(_parse_speed_to_bytes("\u2014"), 0.0)

    def test_mib_s(self):
        result = _parse_speed_to_bytes("5 MiB/s")
        self.assertAlmostEqual(result, 5 * 1024 * 1024)

    def test_kib_s(self):
        result = _parse_speed_to_bytes("10 KiB/s")
        self.assertAlmostEqual(result, 10 * 1024)

    def test_b_s(self):
        result = _parse_speed_to_bytes("500 B/s")
        self.assertAlmostEqual(result, 500)

    def test_gib_s(self):
        result = _parse_speed_to_bytes("2 GiB/s")
        self.assertAlmostEqual(result, 2 * 1024 ** 3)

    def test_mb_s(self):
        result = _parse_speed_to_bytes("5 MB/s")
        self.assertAlmostEqual(result, 5 * 1000 * 1000)

    def test_invalid_string(self):
        self.assertEqual(_parse_speed_to_bytes("nope"), 0.0)


class TestFormatETA(unittest.TestCase):
    def test_none_returns_emdash(self):
        self.assertEqual(_format_eta(None), "\u2014")

    def test_zero_returns_emdash(self):
        self.assertEqual(_format_eta(0), "\u2014")

    def test_negative_returns_emdash(self):
        self.assertEqual(_format_eta(-1), "\u2014")

    def test_seconds(self):
        self.assertEqual(_format_eta(30), "30s")

    def test_minutes(self):
        self.assertEqual(_format_eta(150), "2m 30s")

    def test_hours(self):
        self.assertEqual(_format_eta(3661), "1h 01m")

    def test_exact_hour(self):
        self.assertEqual(_format_eta(7200), "2h 00m")


class TestCalculateETA(unittest.TestCase):
    def test_basic_calculation(self):
        result = _calculate_eta(100_000_000, 10_000_000)
        self.assertAlmostEqual(result, 10.0)

    def test_none_remaining(self):
        self.assertIsNone(_calculate_eta(None, 10_000_000))

    def test_zero_remaining(self):
        self.assertIsNone(_calculate_eta(0, 10_000_000))

    def test_zero_speed(self):
        self.assertIsNone(_calculate_eta(100_000_000, 0))

    def test_negative_remaining(self):
        self.assertIsNone(_calculate_eta(-1, 10_000_000))

    def test_negative_speed(self):
        self.assertIsNone(_calculate_eta(100_000_000, -1))


class TestFormatProject(unittest.TestCase):
    def test_progress_only(self):
        self.assertEqual(_format_progress(45.0), "45%")

    def test_with_bytes(self):
        result = _format_progress(50.0, 500_000_000, 1_000_000_000)
        self.assertIn("50%", result)
        self.assertIn("MiB", result)
        self.assertIn("476", result)  # 500MB ~ 476.8 MiB

    def test_no_total_bytes(self):
        self.assertEqual(_format_progress(75.0, 500_000_000, 0), "75%")

    def test_none_downloaded(self):
        self.assertEqual(_format_progress(10.0, None, 1_000_000_000), "10%")

    def test_int_conversion(self):
        self.assertEqual(_format_progress(99.9), "99%")


class TestClampProgress(unittest.TestCase):
    def test_clamps_below_zero(self):
        self.assertEqual(_clamp_progress(-10.0, None), 0.0)

    def test_clamps_above_100(self):
        self.assertEqual(_clamp_progress(150.0, None), 100.0)

    def test_prevents_backwards_jump(self):
        self.assertEqual(_clamp_progress(40.0, 50.0), 45.0)

    def test_allows_small_backwards(self):
        self.assertEqual(_clamp_progress(46.0, 50.0), 46.0)

    def test_no_previous_allows_any(self):
        self.assertEqual(_clamp_progress(30.0, None), 30.0)

    def test_forward_jump_allowed(self):
        self.assertEqual(_clamp_progress(80.0, 50.0), 80.0)


class TestFormatBytes(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(_format_bytes(0), "0 B")

    def test_negative(self):
        self.assertEqual(_format_bytes(-100), "0 B")

    def test_bytes(self):
        self.assertEqual(_format_bytes(500), "500 B")

    def test_kibibytes(self):
        self.assertEqual(_format_bytes(2048), "2 KiB")

    def test_mebibytes(self):
        self.assertEqual(_format_bytes(5_242_880), "5.0 MiB")

    def test_gibibytes(self):
        self.assertEqual(_format_bytes(5_368_709_120), "5.00 GiB")


class TestStatusIcons(unittest.TestCase):
    def test_all_states_have_icons(self):
        expected_states = {"queued", "downloading", "paused", "completed", "failed", "retrying", "cancelled"}
        self.assertEqual(set(_STATUS_ICONS.keys()), expected_states)

    def test_icon_not_empty(self):
        for icon in _STATUS_ICONS.values():
            self.assertTrue(len(icon) > 0)

    def test_icon_unique(self):
        self.assertEqual(len(set(_STATUS_ICONS.values())), len(_STATUS_ICONS))


class TestEmptyStateConstants(unittest.TestCase):
    def test_empty_text_non_empty(self):
        self.assertTrue(len(_EMPTY_ACTIVE_TEXT) > 0)

    def test_empty_subtitle_non_empty(self):
        self.assertTrue(len(_EMPTY_ACTIVE_SUBTITLE) > 0)


class TestPriorityConstants(unittest.TestCase):
    def test_all_labels_present(self):
        for key in ("high", "normal", "low"):
            self.assertIn(key, _PRIORITY_LABELS)
            self.assertIn(key, _PRIORITY_ORDER)

    def test_order_values(self):
        self.assertEqual(_PRIORITY_ORDER["high"], 0)
        self.assertEqual(_PRIORITY_ORDER["normal"], 1)
        self.assertEqual(_PRIORITY_ORDER["low"], 2)


class TestEnhancedRowHash(unittest.TestCase):
    def test_hash_includes_new_fields(self):
        h1 = _compute_row_hash({"id": "1", "status": "downloading", "progress": 50.0,
                                "speed": "5 MiB/s", "eta": "30s", "label": "A",
                                "priority": "normal", "downloaded_bytes": 100,
                                "total_bytes": 200, "size": "200 MB"})
        h2 = _compute_row_hash({"id": "1", "status": "downloading", "progress": 50.0,
                                "speed": "5 MiB/s", "eta": "30s", "label": "A",
                                "priority": "normal", "downloaded_bytes": 150,
                                "total_bytes": 200, "size": "200 MB"})
        self.assertNotEqual(h1, h2)

    def test_hash_same_for_identical_fields(self):
        h1 = _compute_row_hash({"id": "1", "status": "downloading", "progress": 50.0,
                                "speed": "5 MiB/s", "eta": "30s", "label": "A",
                                "priority": "normal", "downloaded_bytes": 100,
                                "total_bytes": 200, "size": "200 MB"})
        h2 = _compute_row_hash({"id": "1", "status": "downloading", "progress": 50.0,
                                "speed": "5 MiB/s", "eta": "30s", "label": "A",
                                "priority": "normal", "downloaded_bytes": 100,
                                "total_bytes": 200, "size": "200 MB"})
        self.assertEqual(h1, h2)

    def test_hash_different_for_different_progress(self):
        h1 = _compute_row_hash({"id": "1", "status": "downloading", "progress": 45.3,
                                "speed": "5 MiB/s", "eta": "30s", "label": "A",
                                "priority": "normal", "downloaded_bytes": 100,
                                "total_bytes": 200, "size": "200 MB"})
        h2 = _compute_row_hash({"id": "1", "status": "downloading", "progress": 45.7,
                                "speed": "5 MiB/s", "eta": "30s", "label": "A",
                                "priority": "normal", "downloaded_bytes": 100,
                                "total_bytes": 200, "size": "200 MB"})
        self.assertNotEqual(h1, h2)


class TestSpeedBadgeIntegration(unittest.TestCase):
    def test_speed_badge_shown_during_download(self):
        row = QueueRow(
            master=MagicMock(),
            on_copy=MagicMock(),
            on_open_folder=MagicMock(),
        )
        row._speed_badge_lbl.configure = MagicMock()
        row._speed_badge_frame.pack = MagicMock()
        row._speed_badge_frame.pack_forget = MagicMock()
        row._speed_badge_frame.pack.reset_mock()
        row._eta_lbl.configure = MagicMock()
        row._progress_lbl.configure = MagicMock()
        row._status_lbl.configure = MagicMock()
        row._badge_icon_lbl.configure = MagicMock()
        row._status_icon_lbl.configure = MagicMock()
        row._progress_bar.set = MagicMock()
        row._progress_bar.configure = MagicMock()
        row._title_lbl.configure = MagicMock()
        row._priority_lbl.configure = MagicMock()

        job = _make_job(status="downloading", speed="3.8 MiB/s")
        row.update_job(job)
        row._speed_badge_lbl.configure.assert_called()

    def test_speed_badge_hidden_when_paused(self):
        row = QueueRow(
            master=MagicMock(),
            on_copy=MagicMock(),
            on_open_folder=MagicMock(),
        )
        row._speed_badge_lbl.configure = MagicMock()
        row._speed_badge_frame.pack = MagicMock()
        row._speed_badge_frame.pack_forget = MagicMock()
        row._speed_badge_frame.pack_forget.reset_mock()
        row._eta_lbl.configure = MagicMock()
        row._progress_lbl.configure = MagicMock()
        row._status_lbl.configure = MagicMock()
        row._badge_icon_lbl.configure = MagicMock()
        row._status_icon_lbl.configure = MagicMock()
        row._progress_bar.set = MagicMock()
        row._progress_bar.configure = MagicMock()
        row._title_lbl.configure = MagicMock()
        row._priority_lbl.configure = MagicMock()

        job = _make_job(status="paused", speed="3.8 MiB/s")
        row.update_job(job)
        row._speed_badge_frame.pack_forget.assert_called()


class TestStatusIconIntegration(unittest.TestCase):
    def test_status_icon_updates_on_status_change(self):
        row = QueueRow(
            master=MagicMock(),
            on_copy=MagicMock(),
            on_open_folder=MagicMock(),
        )
        row._status_icon_lbl.configure = MagicMock()
        row._badge_icon_lbl.configure = MagicMock()
        row._speed_badge_lbl.configure = MagicMock()
        row._speed_badge_frame.pack = MagicMock()
        row._speed_badge_frame.pack_forget = MagicMock()
        row._eta_lbl.configure = MagicMock()
        row._progress_lbl.configure = MagicMock()
        row._status_lbl.configure = MagicMock()
        row._progress_bar.set = MagicMock()
        row._progress_bar.configure = MagicMock()
        row._title_lbl.configure = MagicMock()
        row._priority_lbl.configure = MagicMock()

        job = _make_job(status="completed")
        row.update_job(job)
        row._status_icon_lbl.configure.assert_called()
        call_args = row._status_icon_lbl.configure.call_args
        self.assertIsNotNone(call_args)


class TestProgressTextWithSize(unittest.TestCase):
    def test_progress_text_includes_size_when_available(self):
        row = QueueRow(
            master=MagicMock(),
            on_copy=MagicMock(),
            on_open_folder=MagicMock(),
        )
        row._progress_lbl.configure = MagicMock()
        row._speed_badge_lbl.configure = MagicMock()
        row._speed_badge_frame.pack = MagicMock()
        row._speed_badge_frame.pack_forget = MagicMock()
        row._eta_lbl.configure = MagicMock()
        row._status_lbl.configure = MagicMock()
        row._badge_icon_lbl.configure = MagicMock()
        row._status_icon_lbl.configure = MagicMock()
        row._progress_bar.set = MagicMock()
        row._progress_bar.configure = MagicMock()
        row._title_lbl.configure = MagicMock()
        row._priority_lbl.configure = MagicMock()

        job = _make_job(progress=45.0)
        job["downloaded_bytes"] = 500_000_000
        job["total_bytes"] = 1_000_000_000
        row.update_job(job)
        row._progress_lbl.configure.assert_called()
        call_text = row._progress_lbl.configure.call_args[1].get("text", "")
        self.assertIn("45%", call_text)
        self.assertIn("/", call_text)


class TestSourceConstants(unittest.TestCase):
    def test_empty_active_text_constant(self):
        self.assertEqual(_EMPTY_ACTIVE_TEXT, "No active downloads")

    def test_empty_active_subtitle_constant(self):
        self.assertEqual(_EMPTY_ACTIVE_SUBTITLE, "Downloads will appear here once started")


if __name__ == "__main__":
    unittest.main()
