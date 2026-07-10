"""
queue_panel.py – QueuePage

Scrollable panel displaying active, pending, and completed download queue items.
Supports batch selection, pause/resume, retry, remove, priority management,
context menus, keyboard shortcuts, incremental hash-based refresh, and live statistics.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import TYPE_CHECKING, Any, Callable

import tkinter as tk
import customtkinter as ctk

from base_page import BasePage

if TYPE_CHECKING:
    from backend_manager import BackendManager
    from logger import AppLogger


_EMPTY_ICON = "\u2b07"
_PAUSED_ICON = "\u23f8"

_PRIORITY_LABELS = {"high": "High", "normal": "Normal", "low": "Low"}
_PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}

_STATUS_ICONS = {
    "queued": "\u23f3",
    "downloading": "\u2b07",
    "paused": "\u23f8",
    "completed": "\u2705",
    "failed": "\u274c",
    "retrying": "\U0001f504",
    "cancelled": "\U0001f6ab",
}

_CANCEL_ICON = "\u26d4"
_PAUSE_ICON = "\u23f8"
_RESUME_ICON = "\u25b6"
_RETRY_ICON = "\u21ba"
_COPY_ICON = "\U0001f4cb"
_FOLDER_ICON = "\U0001f4c2"
_TOP_ICON = "\u2b06"
_UP_ICON = "\u25b2"
_DOWN_ICON = "\u25bc"
_BOTTOM_ICON = "\u2b07"
_DETAILS_ICON = "\U0001f4cb"

_EMPTY_ACTIVE_TEXT = "No active downloads"
_EMPTY_ACTIVE_SUBTITLE = "Downloads will appear here once started"


def _format_bytes(n: float) -> str:
    if n <= 0:
        return "0 B"
    if n >= 1024 ** 4:
        return f"{n / 1024 ** 4:.2f} TiB"
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GiB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MiB"
    if n >= 1024:
        return f"{n / 1024:.0f} KiB"
    return f"{n:.0f} B"


def _parse_speed_to_bytes(speed_str: str) -> float:
    if not speed_str or speed_str in ("\u2014", "-", ""):
        return 0.0
    s = speed_str.strip()
    units = [
        ("GiB/s", 1024 ** 3), ("GB/s", 1000 ** 3),
        ("MiB/s", 1024 ** 2), ("MB/s", 1000 ** 2),
        ("KiB/s", 1024), ("KB/s", 1000),
        ("B/s", 1),
    ]
    for suffix, mult in units:
        if s.endswith(suffix):
            try:
                return float(s[: -len(suffix)].strip()) * mult
            except ValueError:
                return 0.0
    return 0.0


def _format_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec <= 0:
        return "\u2014"
    if bytes_per_sec >= 1024 ** 3:
        return f"{bytes_per_sec / 1024 ** 3:.2f} GiB/s"
    if bytes_per_sec >= 1024 ** 2:
        return f"{bytes_per_sec / 1024 ** 2:.1f} MiB/s"
    if bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.0f} KiB/s"
    return f"{bytes_per_sec:.0f} B/s"


def _format_eta(seconds: float | None) -> str:
    if seconds is None or seconds <= 0:
        return "\u2014"
    if seconds >= 3600:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m:02d}m"
    if seconds >= 60:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m {s:02d}s"
    return f"{int(seconds)}s"


def _format_progress(
    progress: float,
    downloaded_bytes: float | None = None,
    total_bytes: float | None = None,
) -> str:
    pct = f"{int(progress)}%"
    if total_bytes and downloaded_bytes is not None and total_bytes > 0:
        return f"{pct} ({_format_bytes(downloaded_bytes)} / {_format_bytes(total_bytes)})"
    return pct


def _clamp_progress(value: float, previous: float | None = None) -> float:
    value = max(0.0, min(100.0, value))
    if previous is not None:
        value = max(value, previous - 5.0)
    return value


def _calculate_eta(
    remaining_bytes: float | None,
    avg_speed_bytes: float,
) -> float | None:
    if remaining_bytes is None or remaining_bytes <= 0 or avg_speed_bytes <= 0:
        return None
    return remaining_bytes / avg_speed_bytes


class _SpeedSmoother:
    """Rolling average speed calculator over last N samples."""

    def __init__(self, window: int = 5) -> None:
        self._window = max(window, 1)
        self._samples: list[float] = []

    def add_sample(self, speed_bytes: float) -> None:
        if speed_bytes > 0:
            self._samples.append(speed_bytes)
            if len(self._samples) > self._window:
                self._samples.pop(0)

    def reset(self) -> None:
        self._samples.clear()

    @property
    def average(self) -> float:
        if not self._samples:
            return 0.0
        return sum(self._samples) / len(self._samples)

    @property
    def has_samples(self) -> bool:
        return len(self._samples) > 0


def _compute_row_hash(job: dict[str, Any]) -> int:
    return hash((
        job.get("id"),
        job.get("status"),
        round(float(job.get("progress") or 0.0), 1),
        job.get("speed"),
        job.get("eta"),
        job.get("label"),
        job.get("priority", "normal"),
        round(float(job.get("downloaded_bytes") or 0.0), 0),
        round(float(job.get("total_bytes") or 0.0), 0),
        job.get("size"),
    ))


class QueueRow(ctk.CTkFrame):
    """
    A single reusable row widget in the queue table.
    Stacked layout: title row -> progress bar -> meta row (speed / ETA / badge).
    Supports right-click context menu, selection highlight, and batch actions.
    """

    _STATUS_COLORS = {
        "queued":      "#f59e0b",
        "downloading": "#4f8ef7",
        "completed":   "#22c55e",
        "failed":      "#ef4444",
        "paused":      "#a78bfa",
    }

    def __init__(self, master: ctk.CTkFrame, on_copy: Callable[[str], None],
                 on_open_folder: Callable[[], None],
                 on_retry: Callable[[str], None] | None = None,
                 on_remove: Callable[[str], None] | None = None,
                 on_cancel: Callable[[str], None] | None = None,
                 on_show_details: Callable[[dict], None] | None = None,
                 on_pause: Callable[[str], None] | None = None,
                 on_resume: Callable[[str], None] | None = None,
                 on_move_top: Callable[[str], None] | None = None,
                 on_move_up: Callable[[str], None] | None = None,
                 on_move_down: Callable[[str], None] | None = None,
                 on_move_bottom: Callable[[str], None] | None = None,
                 on_selection: Callable[[str, bool, bool, bool], None] | None = None) -> None:
        super().__init__(
            master,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=10,
        )
        self._on_copy = on_copy
        self._on_open_folder = on_open_folder
        self._on_retry = on_retry
        self._on_remove = on_remove
        self._on_cancel = on_cancel
        self._on_show_details = on_show_details
        self._on_pause = on_pause
        self._on_resume = on_resume
        self._on_move_top = on_move_top
        self._on_move_up = on_move_up
        self._on_move_down = on_move_down
        self._on_move_bottom = on_move_bottom
        self._on_selection = on_selection
        self.job_id: str | None = None
        self.job_url: str = ""
        self._job_data: dict[str, Any] = {}
        self._cached_values: dict[str, Any] = {}
        self._cached_hash: int | None = None
        self._selected: bool = False
        self._speed_smoother = _SpeedSmoother()
        self._prev_progress: float | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="x", padx=14, pady=10)

        top_row = ctk.CTkFrame(outer, fg_color="transparent")
        top_row.pack(fill="x")

        self._status_icon_lbl = ctk.CTkLabel(
            top_row,
            text="\u23f3",
            font=ctk.CTkFont(family="Segoe UI", size=14),
            text_color="#f59e0b",
            anchor="w",
            width=20,
        )
        self._status_icon_lbl.pack(side="left", padx=(0, 6))

        self._title_lbl = ctk.CTkLabel(
            top_row,
            text="Job Title",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#e8eaf0",
            anchor="w",
            wraplength=360,
            justify="left",
        )
        self._title_lbl.pack(side="left", fill="x", expand=True)

        self._priority_lbl = ctk.CTkLabel(
            top_row,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color="#8b92a8",
            padx=4,
        )
        self._priority_lbl.pack(side="right", padx=(4, 0))

        self._copy_btn = ctk.CTkButton(
            top_row, text="\U0001f4cb", width=26, height=26,
            fg_color="#20232f", hover_color="#2e3347", corner_radius=6,
            command=self._copy_click,
        )
        self._copy_btn.pack(side="right", padx=(4, 0))

        self._open_btn = ctk.CTkButton(
            top_row, text="\U0001f4c2", width=26, height=26,
            fg_color="#20232f", hover_color="#2e3347", corner_radius=6,
            command=self._open_click,
        )
        self._open_btn.pack(side="right", padx=(4, 0))

        prog_row = ctk.CTkFrame(outer, fg_color="transparent")
        prog_row.pack(fill="x", pady=(6, 3))

        self._progress_bar = ctk.CTkProgressBar(
            prog_row,
            fg_color="#20232f",
            progress_color="#4f8ef7",
            height=7,
            corner_radius=4,
        )
        self._progress_bar.pack(fill="x")
        self._progress_bar.set(0.0)

        meta_row = ctk.CTkFrame(outer, fg_color="transparent")
        meta_row.pack(fill="x")

        self._speed_badge_frame = ctk.CTkFrame(
            meta_row,
            fg_color="#20232f",
            corner_radius=6,
        )
        self._speed_badge_lbl = ctk.CTkLabel(
            self._speed_badge_frame,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#4f8ef7",
            padx=6, pady=1,
        )
        self._speed_badge_lbl.pack()
        self._speed_badge_frame.pack(side="left")

        self._eta_lbl = ctk.CTkLabel(
            meta_row,
            text="ETA: \u2014",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
            anchor="w",
        )
        self._eta_lbl.pack(side="left", padx=(12, 0))

        self._progress_lbl = ctk.CTkLabel(
            meta_row,
            text="0%",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        )
        self._progress_lbl.pack(side="left", padx=(12, 0))

        self._badge_frame = ctk.CTkFrame(
            meta_row,
            fg_color="#2e3347",
            corner_radius=8,
        )
        self._badge_frame.pack(side="right")
        self._badge_icon_lbl = ctk.CTkLabel(
            self._badge_frame,
            text="\u23f3",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color="#f59e0b",
            padx=(8, 0), pady=2,
        )
        self._badge_icon_lbl.pack(side="left")
        self._status_lbl = ctk.CTkLabel(
            self._badge_frame,
            text="Queued",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#f59e0b",
            padx=(2, 8), pady=2,
        )
        self._status_lbl.pack(side="left")

        try:
            self._context_menu = ctk.CTkMenu(self, tearoff=False)
        except (AttributeError, TypeError):
            self._context_menu = tk.Menu(self, tearoff=False)
        self._context_menu.add_command(label=f"{_CANCEL_ICON}  Cancel", command=self._context_cancel)
        self._context_menu.add_command(label=f"{_PAUSE_ICON}  Pause", command=self._context_pause)
        self._context_menu.add_command(label=f"{_RESUME_ICON}  Resume", command=self._context_resume)
        self._context_menu.add_command(label=f"{_RETRY_ICON}  Retry", command=self._context_retry)
        self._context_menu.add_command(label=f"{_CANCEL_ICON}  Remove", command=self._context_remove)
        self._context_menu.add_separator()
        self._context_menu.add_command(label=f"{_COPY_ICON}  Copy URL", command=self._context_copy_url)
        self._context_menu.add_command(label=f"{_FOLDER_ICON}  Open Download Folder", command=self._context_open_folder)
        self._context_menu.add_separator()
        self._context_menu.add_command(label=f"{_TOP_ICON}  Move to Top", command=self._context_move_top)
        self._context_menu.add_command(label=f"{_UP_ICON}  Move Up", command=self._context_move_up)
        self._context_menu.add_command(label=f"{_DOWN_ICON}  Move Down", command=self._context_move_down)
        self._context_menu.add_command(label=f"{_BOTTOM_ICON}  Move to Bottom", command=self._context_move_bottom)
        self._context_menu.add_separator()
        self._context_menu.add_command(label=f"{_DETAILS_ICON}  Properties", command=self._context_show_details)
        self.bind("<Button-3>", self._on_right_click)
        self.bind("<Button-1>", self._on_left_click)

    def _on_right_click(self, event) -> None:
        self._update_context_menu()
        self._context_menu.tk_popup(event.x_root, event.y_root)

    def _on_left_click(self, event) -> None:
        ctrl = (event.state & 0x0004) != 0
        shift = (event.state & 0x0001) != 0
        if self._on_selection:
            self._on_selection(self.job_id or "", ctrl, shift, True)

    def _update_context_menu(self) -> None:
        status = (self._job_data.get("status") or "").lower()
        cancellable = status in ("queued", "downloading", "paused", "retrying")
        self._set_menu_state(0, cancellable)
        self._set_menu_state(1, status == "downloading" or status == "queued")
        self._set_menu_state(2, status == "paused")
        self._set_menu_state(3, status == "failed")
        self._set_menu_state(4, True)
        self._set_menu_state(6, bool(self.job_url))
        self._set_menu_state(7, True)
        self._set_menu_state(9, True)
        self._set_menu_state(10, True)
        self._set_menu_state(11, True)
        self._set_menu_state(12, True)

    def _set_menu_state(self, index: int, enabled: bool) -> None:
        try:
            self._context_menu.entryconfigure(index, state="normal" if enabled else "disabled")
        except Exception:
            pass

    def _context_cancel(self) -> None:
        if self._on_cancel and self.job_id:
            self._on_cancel(self.job_id)

    def _context_pause(self) -> None:
        if self._on_pause and self.job_id:
            self._on_pause(self.job_id)

    def _context_resume(self) -> None:
        if self._on_resume and self.job_id:
            self._on_resume(self.job_id)

    def _context_retry(self) -> None:
        if self._on_retry and self.job_id:
            self._on_retry(self.job_id)

    def _context_remove(self) -> None:
        if self._on_remove and self.job_id:
            self._on_remove(self.job_id)

    def _context_copy_url(self) -> None:
        self._copy_click()

    def _context_open_folder(self) -> None:
        self._open_click()

    def _context_move_top(self) -> None:
        if self._on_move_top and self.job_id:
            self._on_move_top(self.job_id)

    def _context_move_up(self) -> None:
        if self._on_move_up and self.job_id:
            self._on_move_up(self.job_id)

    def _context_move_down(self) -> None:
        if self._on_move_down and self.job_id:
            self._on_move_down(self.job_id)

    def _context_move_bottom(self) -> None:
        if self._on_move_bottom and self.job_id:
            self._on_move_bottom(self.job_id)

    def _context_show_details(self) -> None:
        if self._on_show_details:
            self._on_show_details(self._job_data)

    @property
    def selected(self) -> bool:
        return self._selected

    @selected.setter
    def selected(self, value: bool) -> None:
        self._selected = value
        if value:
            self.configure(fg_color="#2a2d3f", border_color="#4f8ef7")
        else:
            self.configure(fg_color="#1a1d27", border_color="#2e3347")

    def update_job(self, job: dict[str, Any]) -> bool:
        new_hash = _compute_row_hash(job)
        if new_hash == self._cached_hash and self.job_id == job.get("id"):
            return False
        self._cached_hash = new_hash

        new_id = job.get("id")
        if new_id is not None and new_id != self.job_id:
            self._cached_values.clear()
            self._speed_smoother.reset()
            self._prev_progress = None
        self.job_id = new_id
        self.job_url = job.get("url", "")
        self._job_data = job

        status = job.get("status", "queued").lower()

        # ── Title ──────────────────────────────────────────────────────────
        new_label = job.get("label") or job.get("filename") or "Download Job"
        if len(new_label) > 55:
            new_label = new_label[:53] + "\u2026"
        if self._cached_values.get("title") != new_label:
            self._title_lbl.configure(text=new_label)
            self._cached_values["title"] = new_label

        # ── Status Icon ────────────────────────────────────────────────────
        icon = _STATUS_ICONS.get(status, "\u23f3")
        icon_color = self._STATUS_COLORS.get(status, "#8b92a8")
        if self._cached_values.get("status_icon") != icon:
            self._status_icon_lbl.configure(text=icon, text_color=icon_color)
            self._cached_values["status_icon"] = icon

        # ── Progress (clamped, smooth, no backwards jumps) ─────────────────
        raw = float(job.get("progress") or 0.0)
        progress = _clamp_progress(raw, self._prev_progress)
        self._prev_progress = progress
        if self._cached_values.get("progress") != progress:
            self._progress_bar.set(progress / 100.0)
            self._cached_values["progress"] = progress

        # ── Progress Text (with size info) ─────────────────────────────────
        downloaded_b = job.get("downloaded_bytes")
        total_b = job.get("total_bytes")
        try:
            downloaded_b_f = float(downloaded_b) if downloaded_b is not None else None
        except (ValueError, TypeError):
            downloaded_b_f = None
        try:
            total_b_f = float(total_b) if total_b is not None else None
        except (ValueError, TypeError):
            total_b_f = None
        progress_text = _format_progress(progress, downloaded_b_f, total_b_f)
        if self._cached_values.get("progress_text") != progress_text:
            self._progress_lbl.configure(text=progress_text)
            self._cached_values["progress_text"] = progress_text

        # ── Speed Badge (smooth rolling average) ───────────────────────────
        raw_speed_str = job.get("speed") or ""
        speed_bytes = _parse_speed_to_bytes(raw_speed_str)
        if speed_bytes > 0 and status == "downloading":
            self._speed_smoother.add_sample(speed_bytes)
        elif status != "downloading":
            self._speed_smoother.reset()
        avg_speed = self._speed_smoother.average

        show_badge = status == "downloading" and avg_speed > 0
        if show_badge:
            badge_text = "\u2b07 " + _format_speed(avg_speed)
            badge_color = "#4f8ef7"
        else:
            badge_text = ""
            badge_color = "#8b92a8"
        if self._cached_values.get("speed_badge") != badge_text:
            if badge_text:
                self._speed_badge_lbl.configure(text=badge_text, text_color=badge_color)
                self._speed_badge_frame.pack(side="left")
            else:
                self._speed_badge_lbl.configure(text="")
                self._speed_badge_frame.pack_forget()
            self._cached_values["speed_badge"] = badge_text

        # ── Accurate ETA ──────────────────────────────────────────────────
        remaining_b = None
        if downloaded_b_f is not None and total_b_f is not None and total_b_f > 0:
            remaining_b = total_b_f - downloaded_b_f
        eta_seconds = _calculate_eta(remaining_b, avg_speed) if remaining_b is not None and remaining_b > 0 else None
        if status in ("paused", "completed", "failed", "cancelled"):
            eta_seconds = None
        eta_formatted = _format_eta(eta_seconds)
        eta_text = f"ETA: {eta_formatted}"
        if self._cached_values.get("eta") != eta_text:
            self._eta_lbl.configure(text=eta_text)
            self._cached_values["eta"] = eta_text

        # ── Status Badge ───────────────────────────────────────────────────
        if self._cached_values.get("status") != status:
            color = self._STATUS_COLORS.get(status, "#8b92a8")
            self._status_lbl.configure(text=status.capitalize(), text_color=color)
            self._badge_icon_lbl.configure(text=icon, text_color=color)
            if status == "failed":
                self._progress_bar.configure(progress_color="#ef4444")
            elif status == "completed":
                self._progress_bar.configure(progress_color="#22c55e")
            elif status == "paused":
                self._progress_bar.configure(progress_color="#a78bfa")
            else:
                self._progress_bar.configure(progress_color="#4f8ef7")
            self._cached_values["status"] = status

        # ── Priority ──────────────────────────────────────────────────────
        priority = job.get("priority", "normal")
        if self._cached_values.get("priority") != priority:
            ptext = _PRIORITY_LABELS.get(priority, "")
            self._priority_lbl.configure(text=ptext)
            self._cached_values["priority"] = priority

        return True

    def _copy_click(self) -> None:
        if self.job_url:
            self._on_copy(self.job_url)

    def _open_click(self) -> None:
        self._on_open_folder()

    def reset_cache(self) -> None:
        self._cached_values.clear()
        self._cached_hash = None
        self._speed_smoother.reset()
        self._prev_progress = None


class QueuePage(BasePage):
    """
    Page displaying the scrollable list of active and pending download jobs.
    Supports pause/resume, batch selection, context menus, priority management,
    incremental hash-based rendering, and live statistics.
    """

    def __init__(self, master: ctk.CTk, manager: BackendManager, logger: AppLogger) -> None:
        super().__init__(master, manager, logger)
        self._cached_hash = None
        self._row_widgets: list[QueueRow] = []
        self._download_folder = ""
        self._paused = False
        self._scroll_y: tuple[float, float] | None = None
        self._selected_ids: set[str] = set()
        self._last_clicked_idx: int = -1
        self._queue_data: list[dict[str, Any]] = []

        self._cached_stats: dict[str, int | str] = {}
        self._build_ui()

    @property
    def paused(self) -> bool:
        return self._paused

    @paused.setter
    def paused(self, value: bool) -> None:
        self._paused = value
        self._update_pause_ui()

    def _update_pause_ui(self) -> None:
        if self._paused:
            self._pause_btn.configure(
                text="\u25b6 Resume Queue",
                fg_color="#22c55e",
                hover_color="#1ba94a",
            )
            self._status_indicator.configure(text="\u23f8 Queue Paused", text_color="#f59e0b")
        else:
            self._pause_btn.configure(
                text="\u23f8 Pause Queue",
                fg_color="#f59e0b",
                hover_color="#d98300",
            )
            self._status_indicator.configure(text="\u25b6 Queue Running", text_color="#22c55e")

    def toggle_pause(self) -> None:
        self.paused = not self._paused
        if self._paused:
            self.logger.info("[Queue] Queue paused")
        else:
            self.logger.info("[Queue] Queue resumed")

    def is_paused(self) -> bool:
        return self._paused

    def get_selected_job_ids(self) -> list[str]:
        if self._selected_ids:
            return list(self._selected_ids)
        return [r.job_id for r in self._row_widgets if r.job_id]

    def get_job_url(self, job_id: str) -> str:
        for r in self._row_widgets:
            if r.job_id == job_id:
                return r.job_url
        return ""

    def _on_row_selection(self, job_id: str, ctrl: bool, shift: bool, _left: bool) -> None:
        if not job_id:
            return
        if ctrl:
            if job_id in self._selected_ids:
                self._selected_ids.discard(job_id)
            else:
                self._selected_ids.add(job_id)
            idx = next((i for i, r in enumerate(self._row_widgets) if r.job_id == job_id), -1)
            if idx >= 0:
                self._last_clicked_idx = idx
        elif shift and self._last_clicked_idx >= 0:
            idx = next((i for i, r in enumerate(self._row_widgets) if r.job_id == job_id), -1)
            if idx >= 0:
                start = min(self._last_clicked_idx, idx)
                end = max(self._last_clicked_idx, idx)
                self._selected_ids.clear()
                for i in range(start, end + 1):
                    rid = self._row_widgets[i].job_id
                    if rid:
                        self._selected_ids.add(rid)
                self._last_clicked_idx = idx
        else:
            self._selected_ids.clear()
            self._selected_ids.add(job_id)
            idx = next((i for i, r in enumerate(self._row_widgets) if r.job_id == job_id), -1)
            if idx >= 0:
                self._last_clicked_idx = idx
        self._sync_selection_ui()
        self._update_toolbar_buttons()
        self.logger.info(f"[Queue] Selection updated — {len(self._selected_ids)} items selected")

    def select_all(self) -> None:
        self._selected_ids.clear()
        for r in self._row_widgets:
            if r.job_id:
                self._selected_ids.add(r.job_id)
        self._sync_selection_ui()
        self._update_toolbar_buttons()
        self.logger.info(f"[Queue] Selection updated — {len(self._selected_ids)} items selected")

    def clear_selection(self) -> None:
        self._selected_ids.clear()
        self._sync_selection_ui()
        self._update_toolbar_buttons()
        self.logger.info("[Queue] Selection cleared")

    def _sync_selection_ui(self) -> None:
        for r in self._row_widgets:
            r.selected = r.job_id in self._selected_ids if r.job_id else False

    def _update_toolbar_buttons(self) -> None:
        has_sel = len(self._selected_ids) > 0
        state = "normal" if has_sel else "disabled"
        self._pause_sel_btn.configure(state=state)
        self._resume_sel_btn.configure(state=state)
        self._cancel_sel_btn.configure(state=state)
        self._remove_sel_btn.configure(state=state)
        self._increase_prio_btn.configure(state=state)
        self._decrease_prio_btn.configure(state=state)

    # ── Batch Operations ──────────────────────────────────────────────────

    def pause_selected(self) -> None:
        ids = self.get_selected_job_ids()
        for jid in ids:
            try:
                self.manager.pause_download(jid)
            except Exception as exc:
                self.logger.error(f"[Queue] Pause failed — job: {jid}, error: {exc}")
        self.logger.info(f"[Queue] Paused {len(ids)} job(s)")
        self._trigger_refresh()

    def resume_selected(self) -> None:
        ids = self.get_selected_job_ids()
        for jid in ids:
            try:
                self.manager.resume_download(jid)
            except Exception as exc:
                self.logger.error(f"[Queue] Resume failed — job: {jid}, error: {exc}")
        self.logger.info(f"[Queue] Resumed {len(ids)} job(s)")
        self._trigger_refresh()

    def retry_all_failed(self) -> None:
        failed_ids = [r.job_id for r in self._row_widgets
                      if r.job_id and r._job_data.get("status") == "failed"]
        for jid in failed_ids:
            try:
                self.manager.retry_download(jid)
            except Exception as exc:
                self.logger.error(f"[Queue] Retry failed — job: {jid}, error: {exc}")
        self.logger.info(f"[Queue] Retried {len(failed_ids)} job(s)")
        self._trigger_refresh()

    def cancel_selected(self) -> None:
        ids = self.get_selected_job_ids()
        if not ids:
            return
        if not self._confirm_cancel(len(ids)):
            return
        for jid in ids:
            try:
                self.manager.cancel_download(jid)
            except Exception as exc:
                self.logger.error(f"[Queue] Cancel failed — job: {jid}, error: {exc}")
        self._selected_ids.clear()
        self.logger.info(f"[Queue] Cancelled {len(ids)} job(s)")
        self._trigger_refresh()

    def remove_selected(self) -> None:
        ids = self.get_selected_job_ids()
        if not ids:
            return
        if not self._confirm_remove(len(ids)):
            return
        for jid in ids:
            try:
                self.manager.remove_download(jid)
            except Exception as exc:
                self.logger.error(f"[Queue] Remove failed — job: {jid}, error: {exc}")
        self._selected_ids.clear()
        self.logger.info(f"[Queue] Removed {len(ids)} job(s)")
        self._trigger_refresh()

    def _confirm_cancel(self, count: int) -> bool:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Cancel Downloads")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        x = self.winfo_pointerx() - 180
        y = self.winfo_pointery() - 80
        dialog.geometry(f"360x170+{x}+{y}")

        msg = f"Cancel {count} selected download(s)?"
        lbl = ctk.CTkLabel(dialog, text=msg, font=ctk.CTkFont(size=14, weight="bold"),
                           wraplength=320, justify="left")
        lbl.pack(pady=(16, 4), padx=20)

        sub = ctk.CTkLabel(dialog, text="This will stop running and remove queued job(s).",
                           font=ctk.CTkFont(size=11), text_color="#8b92a8",
                           wraplength=320, justify="left")
        sub.pack(pady=(0, 12), padx=20)

        result = [False]

        def on_cancel_confirm():
            result[0] = True
            dialog.destroy()

        def on_cancel_dismiss():
            dialog.destroy()

        btn_fr = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_fr.pack(fill="x", pady=12, padx=20)

        ctk.CTkButton(btn_fr, text="Cancel Downloads", command=on_cancel_confirm, width=120,
                      fg_color="#f59e0b", hover_color="#d98300").pack(side="left")
        ctk.CTkButton(btn_fr, text="Back", command=on_cancel_dismiss, width=100,
                      fg_color="#20232f", hover_color="#2e3347").pack(side="right")

        dialog.bind("<Return>", lambda e: on_cancel_confirm())
        dialog.bind("<Escape>", lambda e: on_cancel_dismiss())
        self.wait_window(dialog)
        return result[0]

    def _confirm_remove(self, count: int) -> bool:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Remove Downloads")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        x = self.winfo_pointerx() - 180
        y = self.winfo_pointery() - 80
        dialog.geometry(f"360x170+{x}+{y}")

        msg = f"Remove {count} selected download(s)?"
        lbl = ctk.CTkLabel(dialog, text=msg, font=ctk.CTkFont(size=14, weight="bold"),
                           wraplength=320, justify="left")
        lbl.pack(pady=(16, 4), padx=20)

        sub = ctk.CTkLabel(dialog, text="This will remove the job(s) from the queue.",
                           font=ctk.CTkFont(size=11), text_color="#8b92a8",
                           wraplength=320, justify="left")
        sub.pack(pady=(0, 12), padx=20)

        result = [False]

        def on_remove():
            result[0] = True
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        btn_fr = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_fr.pack(fill="x", pady=12, padx=20)

        ctk.CTkButton(btn_fr, text="Remove", command=on_remove, width=100,
                      fg_color="#ef4444", hover_color="#b91c1c").pack(side="left")
        ctk.CTkButton(btn_fr, text="Cancel", command=on_cancel, width=100,
                      fg_color="#20232f", hover_color="#2e3347").pack(side="right")

        dialog.bind("<Return>", lambda e: on_remove())
        dialog.bind("<Escape>", lambda e: on_cancel())
        self.wait_window(dialog)
        return result[0]

    # ── Priority Operations ───────────────────────────────────────────────

    def increase_priority(self) -> None:
        ids = self.get_selected_job_ids()
        for jid in ids:
            row = next((r for r in self._row_widgets if r.job_id == jid), None)
            if not row:
                continue
            cur = row._job_data.get("priority", "normal")
            newp = {"high": "high", "normal": "high", "low": "normal"}.get(cur, "normal")
            try:
                self.manager.change_priority(jid, newp)
            except Exception as exc:
                self.logger.error(f"[Queue] Priority change failed — job: {jid}, error: {exc}")
        self.logger.info("[Queue] Priority changed")
        self._trigger_refresh()

    def decrease_priority(self) -> None:
        ids = self.get_selected_job_ids()
        for jid in ids:
            row = next((r for r in self._row_widgets if r.job_id == jid), None)
            if not row:
                continue
            cur = row._job_data.get("priority", "normal")
            newp = {"high": "normal", "normal": "low", "low": "low"}.get(cur, "normal")
            try:
                self.manager.change_priority(jid, newp)
            except Exception as exc:
                self.logger.error(f"[Queue] Priority change failed — job: {jid}, error: {exc}")
        self.logger.info("[Queue] Priority changed")
        self._trigger_refresh()

    def move_to_top(self, job_id: str) -> None:
        try:
            self.manager.reorder_job(job_id, 0)
            self.logger.info(f"[Queue] Priority changed — job: {job_id}")
        except Exception as exc:
            self.logger.error(f"[Queue] Reorder failed — job: {job_id}, error: {exc}")
        self._trigger_refresh()

    def move_up(self, job_id: str) -> None:
        idx = next((i for i, r in enumerate(self._row_widgets) if r.job_id == job_id), -1)
        if idx > 0:
            try:
                self.manager.reorder_job(job_id, idx - 1)
                self.logger.info(f"[Queue] Priority changed — job: {job_id}")
            except Exception as exc:
                self.logger.error(f"[Queue] Reorder failed — job: {job_id}, error: {exc}")
        self._trigger_refresh()

    def move_down(self, job_id: str) -> None:
        idx = next((i for i, r in enumerate(self._row_widgets) if r.job_id == job_id), -1)
        if 0 <= idx < len(self._row_widgets) - 1:
            try:
                self.manager.reorder_job(job_id, idx + 1)
                self.logger.info(f"[Queue] Priority changed — job: {job_id}")
            except Exception as exc:
                self.logger.error(f"[Queue] Reorder failed — job: {job_id}, error: {exc}")
        self._trigger_refresh()

    def move_to_bottom(self, job_id: str) -> None:
        try:
            self.manager.reorder_job(job_id, len(self._row_widgets))
            self.logger.info(f"[Queue] Priority changed — job: {job_id}")
        except Exception as exc:
            self.logger.error(f"[Queue] Reorder failed — job: {job_id}, error: {exc}")
        self._trigger_refresh()

    def _trigger_refresh(self) -> None:
        master = self.master.master
        if hasattr(master, "_dashboard_controller") and master._dashboard_controller:
            master._dashboard_controller.trigger_poll()

    # ── UI Build ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(20, 10))

        title_container = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_container.pack(side="left", anchor="w")

        ctk.CTkLabel(
            title_container,
            text="Download Queue",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#e8eaf0",
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_container,
            text="Manage ongoing and pending download tasks.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8b92a8",
        ).pack(anchor="w", pady=(4, 0))

        toolbar = ctk.CTkFrame(header_frame, fg_color="transparent")
        toolbar.pack(side="right")

        self._pause_sel_btn = ctk.CTkButton(
            toolbar,
            text="\u23f8 Pause Sel.",
            width=90,
            height=30,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            corner_radius=8,
            state="disabled",
            command=self.pause_selected,
        )
        self._pause_sel_btn.pack(side="right", padx=2)

        self._resume_sel_btn = ctk.CTkButton(
            toolbar,
            text="\u25b6 Resume Sel.",
            width=90,
            height=30,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            corner_radius=8,
            state="disabled",
            command=self.resume_selected,
        )
        self._resume_sel_btn.pack(side="right", padx=2)

        self._cancel_sel_btn = ctk.CTkButton(
            toolbar,
            text=f"{_CANCEL_ICON} Cancel Sel.",
            width=100,
            height=30,
            fg_color="#f59e0b",
            hover_color="#d98300",
            text_color="#ffffff",
            corner_radius=8,
            state="disabled",
            command=self.cancel_selected,
        )
        self._cancel_sel_btn.pack(side="right", padx=2)

        self._retry_failed_btn = ctk.CTkButton(
            toolbar,
            text="\u21ba Retry Failed",
            width=100,
            height=30,
            fg_color="#f59e0b",
            hover_color="#d98300",
            text_color="#ffffff",
            corner_radius=8,
            command=self.retry_all_failed,
        )
        self._retry_failed_btn.pack(side="right", padx=2)

        self._remove_sel_btn = ctk.CTkButton(
            toolbar,
            text="\u2716 Remove",
            width=90,
            height=30,
            fg_color="#ef4444",
            hover_color="#b91c1c",
            text_color="#ffffff",
            corner_radius=8,
            state="disabled",
            command=self.remove_selected,
        )
        self._remove_sel_btn.pack(side="right", padx=2)

        self._increase_prio_btn = ctk.CTkButton(
            toolbar,
            text="\u2b06 Priority",
            width=80,
            height=30,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            corner_radius=8,
            state="disabled",
            command=self.increase_priority,
        )
        self._increase_prio_btn.pack(side="right", padx=2)

        self._decrease_prio_btn = ctk.CTkButton(
            toolbar,
            text="\u2b07 Priority",
            width=80,
            height=30,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            corner_radius=8,
            state="disabled",
            command=self.decrease_priority,
        )
        self._decrease_prio_btn.pack(side="right", padx=2)

        self._pause_btn = ctk.CTkButton(
            toolbar,
            text="\u23f8 Pause Queue",
            width=130,
            height=30,
            fg_color="#f59e0b",
            hover_color="#d98300",
            text_color="#ffffff",
            corner_radius=8,
            command=self.toggle_pause,
        )
        self._pause_btn.pack(side="right", padx=2)

        self._open_dir_btn = ctk.CTkButton(
            toolbar,
            text="Open Folder",
            width=100,
            height=30,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            corner_radius=8,
            command=self._open_download_dir,
        )
        self._open_dir_btn.pack(side="right", padx=2)

        # Statistics card
        self._stats_frame = ctk.CTkFrame(self, fg_color="#1a1d27",
                                          border_color="#2e3347", border_width=1,
                                          corner_radius=10)
        self._stats_inner = ctk.CTkFrame(self._stats_frame, fg_color="transparent")
        self._stats_inner.pack(fill="x", padx=14, pady=8)

        stats_labels_row = ctk.CTkFrame(self._stats_inner, fg_color="transparent")
        stats_labels_row.pack(fill="x")

        self._stat_active_lbl = ctk.CTkLabel(stats_labels_row, text="Active: 0",
            font=ctk.CTkFont(size=11, weight="bold"), text_color="#4f8ef7")
        self._stat_active_lbl.pack(side="left", padx=(0, 12))

        self._stat_queued_lbl = ctk.CTkLabel(stats_labels_row, text="Queued: 0",
            font=ctk.CTkFont(size=11), text_color="#f59e0b")
        self._stat_queued_lbl.pack(side="left", padx=(0, 12))

        self._stat_paused_lbl = ctk.CTkLabel(stats_labels_row, text="Paused: 0",
            font=ctk.CTkFont(size=11), text_color="#a78bfa")
        self._stat_paused_lbl.pack(side="left", padx=(0, 12))

        self._stat_completed_lbl = ctk.CTkLabel(stats_labels_row, text="Completed: 0",
            font=ctk.CTkFont(size=11), text_color="#22c55e")
        self._stat_completed_lbl.pack(side="left", padx=(0, 12))

        self._stat_failed_lbl = ctk.CTkLabel(stats_labels_row, text="Failed: 0",
            font=ctk.CTkFont(size=11), text_color="#ef4444")
        self._stat_failed_lbl.pack(side="left", padx=(0, 12))

        self._stat_total_lbl = ctk.CTkLabel(stats_labels_row, text="Total: 0",
            font=ctk.CTkFont(size=11), text_color="#8b92a8")
        self._stat_total_lbl.pack(side="left", padx=(0, 12))

        self._stat_cancelled_lbl = ctk.CTkLabel(stats_labels_row, text="Cancelled: 0",
            font=ctk.CTkFont(size=11), text_color="#8b92a8")
        self._stat_cancelled_lbl.pack(side="right")

        stats_meta_row = ctk.CTkFrame(self._stats_inner, fg_color="transparent")
        stats_meta_row.pack(fill="x", pady=(4, 0))

        self._stat_avg_speed_lbl = ctk.CTkLabel(stats_meta_row, text="Avg Speed: \u2014",
            font=ctk.CTkFont(size=11), text_color="#8b92a8")
        self._stat_avg_speed_lbl.pack(side="left")

        self._stat_eta_lbl = ctk.CTkLabel(stats_meta_row, text="Est. Remaining: \u2014",
            font=ctk.CTkFont(size=11), text_color="#8b92a8")
        self._stat_eta_lbl.pack(side="right")

        self._stats_frame.pack(fill="x", padx=20, pady=(0, 8))

        # Status indicator
        self._status_indicator = ctk.CTkLabel(
            self,
            text="\u25b6 Queue Running",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#22c55e",
        )
        self._status_indicator.pack(anchor="w", padx=20, pady=(0, 4))

        # Scrollable rows frame
        self._scroll_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            label_text="",
        )
        self._scroll_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        # Empty state
        self._empty_frame = ctk.CTkFrame(self._scroll_frame, fg_color="transparent")
        self._empty_icon_lbl = ctk.CTkLabel(
            self._empty_frame,
            text="\U0001f4e5",
            font=ctk.CTkFont(family="Segoe UI", size=64),
            text_color="#2e3347",
        )
        self._empty_icon_lbl.pack(pady=(80, 12))
        ctk.CTkLabel(
            self._empty_frame,
            text=_EMPTY_ACTIVE_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color="#8b92a8",
        ).pack(pady=(0, 6))
        ctk.CTkLabel(
            self._empty_frame,
            text=_EMPTY_ACTIVE_SUBTITLE,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color="#5a6072",
        ).pack(pady=(0, 16))

        self._start_dl_btn = ctk.CTkButton(
            self._empty_frame,
            text="\u2b07 Start Download",
            fg_color="#4f8ef7",
            hover_color="#3a76e8",
            corner_radius=8,
            command=self._open_dashboard,
        )
        self._start_dl_btn.pack(pady=(0, 20))

        self._build_overall_progress()
        self._show_empty_state()

    def _open_dashboard(self) -> None:
        master = self.master.master
        if hasattr(master, "show_page"):
            master.show_page("Dashboard")

    def _build_overall_progress(self) -> None:
        self._overall_frame = ctk.CTkFrame(self, fg_color="#1a1d27",
                                           border_color="#2e3347", border_width=1,
                                           corner_radius=10)
        _inner = ctk.CTkFrame(self._overall_frame, fg_color="transparent")
        _inner.pack(fill="x", padx=14, pady=10)

        _top = ctk.CTkFrame(_inner, fg_color="transparent")
        _top.pack(fill="x")
        self._overall_title_lbl = ctk.CTkLabel(
            _top,
            text="Active Download",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#e8eaf0",
            anchor="w",
        )
        self._overall_title_lbl.pack(side="left")
        self._overall_count_lbl = ctk.CTkLabel(
            _top,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        )
        self._overall_count_lbl.pack(side="right")

        self._overall_progress_bar = ctk.CTkProgressBar(
            _inner,
            fg_color="#20232f",
            progress_color="#4f8ef7",
            height=6,
            corner_radius=3,
        )
        self._overall_progress_bar.pack(fill="x", pady=(6, 4))
        self._overall_progress_bar.set(0.0)

        _meta = ctk.CTkFrame(_inner, fg_color="transparent")
        _meta.pack(fill="x")
        self._overall_speed_lbl = ctk.CTkLabel(
            _meta,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
            anchor="w",
        )
        self._overall_speed_lbl.pack(side="left")
        self._overall_eta_lbl = ctk.CTkLabel(
            _meta,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
        )
        self._overall_eta_lbl.pack(side="right")
        self._overall_filename_lbl = ctk.CTkLabel(
            _meta,
            text="",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#5a6072",
        )
        self._overall_filename_lbl.pack(side="bottom", anchor="w")

        self._overall_frame.pack(fill="x", padx=20, pady=(0, 8))
        self._overall_visible = False
        self._cached_overall: dict[str, Any] = {}

    def _update_overall_progress(self, queue: list[dict[str, Any]]) -> None:
        total = len(queue)
        completed = sum(1 for j in queue if j.get("status") == "completed")
        active = [j for j in queue if j.get("status") == "downloading"]
        progress_values = [max(0.0, min(100.0, float(j.get("progress") or 0.0))) for j in queue
                          if j.get("status") in ("downloading", "queued")]
        overall_pct = sum(progress_values) / len(progress_values) if progress_values else 0.0

        has_jobs = total > 0
        if has_jobs and not self._overall_visible:
            self._overall_frame.pack(fill="x", padx=20, pady=(0, 8))
            self._overall_visible = True
        elif not has_jobs and self._overall_visible:
            self._overall_frame.pack_forget()
            self._overall_visible = False

        if not has_jobs:
            return

        count_text = f"{completed} / {total} files"
        if self._cached_overall.get("count") != count_text:
            self._overall_count_lbl.configure(text=count_text)
            self._cached_overall["count"] = count_text

        if self._cached_overall.get("progress") != overall_pct:
            self._overall_progress_bar.set(overall_pct / 100.0)
            self._cached_overall["progress"] = overall_pct

        if active:
            first = active[0]
            raw_speed = first.get("speed") or ""
            eta_val = first.get("eta") or ""
            filename = first.get("label") or first.get("filename") or ""
            if len(filename) > 50:
                filename = filename[:48] + "\u2026"
            speed_bytes = _parse_speed_to_bytes(raw_speed)
            smooth_speed = _format_speed(speed_bytes)
            speed_text = f"Speed: \u2b07 {smooth_speed}" if speed_bytes > 0 else ""
            eta_text = f"ETA: {eta_val}" if eta_val else ""
        else:
            speed_text = ""
            eta_text = ""
            filename = ""

        if self._cached_overall.get("speed") != speed_text:
            self._overall_speed_lbl.configure(text=speed_text)
            self._cached_overall["speed"] = speed_text
        if self._cached_overall.get("eta") != eta_text:
            self._overall_eta_lbl.configure(text=eta_text)
            self._cached_overall["eta"] = eta_text
        if self._cached_overall.get("filename") != filename:
            self._overall_filename_lbl.configure(text=filename)
            self._cached_overall["filename"] = filename

        if active:
            self._overall_title_lbl.configure(
                text=active[0].get("label") or active[0].get("filename") or "Downloading\u2026",
                text_color="#e8eaf0",
            )
        elif completed == total:
            self._overall_title_lbl.configure(text="All downloads completed", text_color="#22c55e")
        else:
            self._overall_title_lbl.configure(text=f"{total - completed} pending", text_color="#f59e0b")

    def _update_statistics(self, queue: list[dict[str, Any]]) -> None:
        active = sum(1 for j in queue if j.get("status") == "downloading")
        queued = sum(1 for j in queue if j.get("status") == "queued")
        paused = sum(1 for j in queue if j.get("status") == "paused")
        completed = sum(1 for j in queue if j.get("status") == "completed")
        failed = sum(1 for j in queue if j.get("status") == "failed")
        cancelled = sum(1 for j in queue if j.get("status") == "cancelled")
        total = len(queue)

        new_stats: dict[str, int | str] = {
            "active": active, "queued": queued, "paused": paused,
            "completed": completed, "failed": failed, "cancelled": cancelled,
            "total": total,
        }

        speeds = [j.get("speed", "") for j in queue if j.get("status") == "downloading" and j.get("speed")]
        avg_speed = f"Avg Speed: {speeds[0]}" if speeds else "Avg Speed: \u2014"
        new_stats["avg_speed"] = avg_speed

        etas = [j.get("eta", "") for j in queue if j.get("status") == "downloading" and j.get("eta")]
        est_remaining = f"Est. Remaining: {etas[0]}" if etas else "Est. Remaining: \u2014"
        new_stats["est_remaining"] = est_remaining

        if new_stats == self._cached_stats:
            return
        self._cached_stats = new_stats

        self._stat_active_lbl.configure(text=f"Active: {active}")
        self._stat_queued_lbl.configure(text=f"Queued: {queued}")
        self._stat_paused_lbl.configure(text=f"Paused: {paused}")
        self._stat_completed_lbl.configure(text=f"Completed: {completed}")
        self._stat_failed_lbl.configure(text=f"Failed: {failed}")
        self._stat_cancelled_lbl.configure(text=f"Cancelled: {cancelled}")
        self._stat_total_lbl.configure(text=f"Total: {total}")
        self._stat_avg_speed_lbl.configure(text=avg_speed)
        self._stat_eta_lbl.configure(text=est_remaining)

    def _show_empty_state(self) -> None:
        self._empty_frame.pack(fill="both", expand=True)

    def _hide_empty_state(self) -> None:
        self._empty_frame.pack_forget()

    def _get_scroll_position(self) -> tuple[float, float] | None:
        try:
            canvas = self._scroll_frame._parent_canvas
            return canvas.yview()
        except Exception:
            return None

    def _set_scroll_position(self, pos: tuple[float, float]) -> None:
        try:
            canvas = self._scroll_frame._parent_canvas
            canvas.yview_moveto(pos[0])
        except Exception:
            pass

    def save_scroll(self) -> None:
        self._scroll_y = self._get_scroll_position()

    def restore_scroll(self) -> None:
        if self._scroll_y:
            self._set_scroll_position(self._scroll_y)

    @staticmethod
    def _compute_queue_hash(queue: list[dict[str, Any]]) -> int:
        return hash(str([
            (
                j.get("id"), j.get("status"),
                round(float(j.get("progress") or 0.0), 1),
                j.get("speed"), j.get("eta"), j.get("label"),
                round(float(j.get("downloaded_bytes") or 0.0), 0),
                round(float(j.get("total_bytes") or 0.0), 0),
                j.get("size"),
            ) for j in queue
        ]))

    def refresh(self, data: dict[str, Any]) -> None:
        queue: list[dict[str, Any]] = data.get("queue", [])
        settings = data.get("settings", {})
        self._download_folder = settings.get("download_folder", "")
        self._queue_data = queue

        q_hash = self._compute_queue_hash(queue)
        if q_hash == self._cached_hash:
            self._update_statistics(queue)
            self._update_overall_progress(queue)
            return
        self._cached_hash = q_hash

        self.save_scroll()
        old_selection = set(self._selected_ids)

        required_rows = len(queue)

        if required_rows == 0:
            if hasattr(self, '_overall_frame') and self._overall_visible:
                self._overall_frame.pack_forget()
                self._overall_visible = False
            self._show_empty_state()
            for row in self._row_widgets:
                row.pack_forget()
            self._selected_ids.clear()
            self._update_statistics(queue)
            self._update_toolbar_buttons()
            return
        else:
            self._hide_empty_state()

        self._update_overall_progress(queue)
        self._update_statistics(queue)

        if required_rows > len(self._row_widgets):
            for _ in range(required_rows - len(self._row_widgets)):
                new_row = QueueRow(
                    self._scroll_frame,
                    on_copy=self._copy_to_clipboard,
                    on_open_folder=self._open_download_dir,
                    on_retry=self._retry_job,
                    on_remove=self._remove_job,
                    on_cancel=self._cancel_job,
                    on_show_details=self._show_details_dialog,
                    on_pause=self._pause_job,
                    on_resume=self._resume_job,
                    on_move_top=self.move_to_top,
                    on_move_up=self.move_up,
                    on_move_down=self.move_down,
                    on_move_bottom=self.move_to_bottom,
                    on_selection=self._on_row_selection,
                )
                self._row_widgets.append(new_row)

        for i, row in enumerate(self._row_widgets):
            if i < required_rows:
                row_changed = row.update_job(queue[i])
                row.pack(fill="x", pady=4, padx=2)
            else:
                row.pack_forget()

        self._selected_ids = old_selection & {r.job_id for r in self._row_widgets if r.job_id and r.job_id in old_selection}
        self._sync_selection_ui()
        self._update_toolbar_buttons()

        self.restore_scroll()

    def _cancel_job(self, job_id: str) -> None:
        try:
            self.manager.cancel_download(job_id)
            self.logger.info(f"[Queue] Cancelled job — id: {job_id}")
            self._selected_ids.discard(job_id)
            self._trigger_refresh()
        except Exception as exc:
            self.logger.error(f"[Queue] Cancel failed — job: {job_id}, error: {exc}")

    def _retry_job(self, job_id: str) -> None:
        try:
            self.manager.retry_download(job_id)
            self.logger.info(f"[Queue] Retry requested — job: {job_id}")
        except Exception as exc:
            self.logger.error(f"[Queue] Retry failed — job: {job_id}, error: {exc}")

    def _remove_job(self, job_id: str) -> None:
        try:
            self.manager.remove_download(job_id)
            self.logger.info(f"[Queue] Removed job — id: {job_id}")
            self._selected_ids.discard(job_id)
        except Exception as exc:
            self.logger.error(f"[Queue] Remove failed — job: {job_id}, error: {exc}")

    def _pause_job(self, job_id: str) -> None:
        try:
            self.manager.pause_download(job_id)
            self.logger.info(f"[Queue] Paused job — id: {job_id}")
        except Exception as exc:
            self.logger.error(f"[Queue] Pause failed — job: {job_id}, error: {exc}")

    def _resume_job(self, job_id: str) -> None:
        try:
            self.manager.resume_download(job_id)
            self.logger.info(f"[Queue] Resumed job — id: {job_id}")
        except Exception as exc:
            self.logger.error(f"[Queue] Resume failed — job: {job_id}, error: {exc}")

    def _show_details_dialog(self, job: dict[str, Any]) -> None:
        try:
            dialog = ctk.CTkToplevel(self)
            dialog.title("Download Properties")
            dialog.transient(self)
            dialog.grab_set()
            dialog.resizable(False, False)
            dialog.geometry("460x340")
            x = self.winfo_pointerx() - 230
            y = self.winfo_pointery() - 170
            dialog.geometry(f"+{x}+{y}")

            frame = ctk.CTkFrame(dialog, fg_color="transparent")
            frame.pack(fill="both", expand=True, padx=20, pady=16)

            status = job.get("status", "N/A")
            icon = _STATUS_ICONS.get(status, "")
            icon_color = QueueRow._STATUS_COLORS.get(status, "#8b92a8")
            ctk.CTkLabel(
                frame,
                text=f"{icon}  {status.capitalize()}" if icon else status.capitalize(),
                font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
                text_color=icon_color,
            ).pack(anchor="w", pady=(0, 12))

            fields = [
                ("Title", job.get("label") or job.get("filename", "N/A")),
                ("URL", job.get("url", "N/A")),
                ("Progress", f"{job.get('progress', 0)}%"),
                ("Speed", job.get("speed", "N/A")),
                ("ETA", job.get("eta", "N/A")),
                ("Mode", job.get("mode", "N/A")),
                ("Priority", job.get("priority", "normal")),
                ("Quality", job.get("quality", "N/A")),
                ("Format", job.get("format", "N/A")),
                ("Size", job.get("size", "N/A")),
            ]

            for label, value in fields:
                row = ctk.CTkFrame(frame, fg_color="transparent")
                row.pack(fill="x", pady=2)
                ctk.CTkLabel(
                    row,
                    text=f"{label}:",
                    font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
                    text_color="#8b92a8",
                    width=80,
                    anchor="w",
                ).pack(side="left")
                ctk.CTkLabel(
                    row,
                    text=str(value),
                    font=ctk.CTkFont(family="Segoe UI", size=12),
                    text_color="#e8eaf0",
                    anchor="w",
                    wraplength=340,
                    justify="left",
                ).pack(side="left", padx=(8, 0))

            ctk.CTkButton(
                frame,
                text="Close",
                command=dialog.destroy,
                width=100,
                fg_color="#20232f",
                hover_color="#2e3347",
            ).pack(pady=(16, 0))

            dialog.bind("<Escape>", lambda e: dialog.destroy())
        except Exception:
            from tkinter import messagebox
            details = (
                f"Title: {job.get('label') or job.get('filename', 'N/A')}\n"
                f"URL: {job.get('url', 'N/A')}\n"
                f"Status: {job.get('status', 'N/A')}\n"
                f"Progress: {job.get('progress', 0)}%\n"
                f"Speed: {job.get('speed', 'N/A')}\n"
                f"ETA: {job.get('eta', 'N/A')}\n"
                f"Mode: {job.get('mode', 'N/A')}\n"
                f"Priority: {job.get('priority', 'normal')}\n"
                f"Quality: {job.get('quality', 'N/A')}\n"
                f"Format: {job.get('format', 'N/A')}\n"
                f"Size: {job.get('size', 'N/A')}\n"
            )
            messagebox.showinfo("Download Properties", details, parent=self)

    def _copy_to_clipboard(self, text: str) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.logger.info("[Queue] Copied URL — {url}".format(url=text))
        except Exception:
            pass

    def _open_download_dir(self) -> None:
        if self._download_folder and os.path.exists(self._download_folder):
            try:
                subprocess.Popen(["explorer", os.path.abspath(self._download_folder)])
                self.logger.info("[Queue] Opened download folder — {path}".format(
                    path=self._download_folder))
            except Exception as exc:
                self.logger.error(f"[Queue] Failed to open download folder: {exc}")
        else:
            self.logger.warning("[Queue] Download folder is not set or does not exist.")
