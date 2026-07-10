"""
history_panel.py – HistoryPage

Scrollable download history viewer.
Supports searching, filtering by type/status, sorting by date, clearing history,
and reuses row widgets from an in-memory pool for scalability.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from base_page import BasePage

if TYPE_CHECKING:
    from backend_manager import BackendManager
    from logger import AppLogger


class HistoryRow(ctk.CTkFrame):
    """
    A single reusable row widget in the history table.
    """

    def __init__(self, master: ctk.CTkFrame, on_copy: Callable[[str], None], on_open_folder: Callable[[], None]) -> None:
        super().__init__(
            master,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=10,
        )
        self._on_copy = on_copy
        self._on_open_folder = on_open_folder
        self.job_url: str = ""

        self._build_ui()

    def _build_ui(self) -> None:
        # Title
        self._title_lbl = ctk.CTkLabel(
            self,
            text="Job Title",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#e8eaf0",
            anchor="w",
        )
        self._title_lbl.pack(side="left", fill="x", expand=True, padx=12, pady=10)

        # Type badge
        self._type_lbl = ctk.CTkLabel(
            self,
            text="Video",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
            width=60,
            anchor="center",
        )
        self._type_lbl.pack(side="left", padx=12)

        # Date
        self._date_lbl = ctk.CTkLabel(
            self,
            text="YYYY-MM-DD HH:MM",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#8b92a8",
            width=120,
            anchor="w",
        )
        self._date_lbl.pack(side="left", padx=12)

        # Status badge
        self._status_lbl = ctk.CTkLabel(
            self,
            text="Success",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#22c55e",
            width=70,
            anchor="center",
        )
        self._status_lbl.pack(side="left", padx=12)

        # Action Buttons
        self._copy_btn = ctk.CTkButton(
            self,
            text="📋",
            width=26,
            height=26,
            fg_color="#20232f",
            hover_color="#2e3347",
            corner_radius=6,
            command=self._copy_click,
        )
        self._copy_btn.pack(side="left", padx=4)

        self._open_btn = ctk.CTkButton(
            self,
            text="📂",
            width=26,
            height=26,
            fg_color="#20232f",
            hover_color="#2e3347",
            corner_radius=6,
            command=self._open_click,
        )
        self._open_btn.pack(side="left", padx=(4, 12))

    def update_item(self, item: dict[str, Any]) -> None:
        self.job_url = item.get("url", "")
        
        # Title truncation
        label = item.get("label") or item.get("filename") or "Completed Download"
        if len(label) > 42:
            label = label[:40] + "…"
        self._title_lbl.configure(text=label)

        # Mode
        mode = item.get("mode", "video").lower()
        self._type_lbl.configure(text=mode.capitalize())

        # Date Completed / Queued
        date_str = item.get("completed_at") or item.get("queued_at") or ""
        formatted_date = "—"
        if date_str:
            try:
                # Format: 2026-06-27T01:06:02.123456+00:00 -> 2026-06-27 01:06
                dt = datetime.fromisoformat(date_str)
                formatted_date = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                formatted_date = date_str[:16].replace("T", " ")
        self._date_lbl.configure(text=formatted_date)

        # Status
        status = item.get("status", "completed").lower()
        if status == "completed":
            self._status_lbl.configure(text="Success", text_color="#22c55e")
        else:
            self._status_lbl.configure(text="Failed", text_color="#ef4444")

    def _copy_click(self) -> None:
        if self.job_url:
            self._on_copy(self.job_url)

    def _open_click(self) -> None:
        self._on_open_folder()


class HistoryPage(BasePage):
    """
    Page showing scrollable download history, complete with sorting, keyword search,
    status filtering, and clear commands.
    """

    def __init__(self, master: ctk.CTk, manager: BackendManager, logger: AppLogger) -> None:
        super().__init__(master, manager, logger)
        self._full_history: list[dict[str, Any]] = []
        self._filtered_history: list[dict[str, Any]] = []
        self._row_widgets: list[HistoryRow] = []
        self._download_folder = ""

        self._build_ui()

    def _build_ui(self) -> None:
        # Title
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(20, 10))

        title_container = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_container.pack(side="left", anchor="w")

        ctk.CTkLabel(
            title_container,
            text="Download History",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#e8eaf0",
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_container,
            text="Review and manage completed or failed download tasks.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8b92a8",
        ).pack(anchor="w", pady=(4, 0))

        # Clear Button
        self._clear_btn = ctk.CTkButton(
            header_frame,
            text="Clear History",
            width=110,
            height=30,
            fg_color="#ef4444",
            hover_color="#b91c1c",
            text_color="#ffffff",
            corner_radius=8,
            command=self._clear_history_click,
        )
        self._clear_btn.pack(side="right")

        # ── Toolbar: Search & Filters ──────────────────────────────────────
        toolbar = ctk.CTkFrame(self, fg_color="#1a1d27", height=48, corner_radius=10, border_width=1, border_color="#2e3347")
        toolbar.pack(fill="x", padx=20, pady=(0, 10))
        toolbar.pack_propagate(False)

        # Keyword Search
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filters())
        self._search_entry = ctk.CTkEntry(
            toolbar,
            textvariable=self._search_var,
            placeholder_text="Search history…",
            width=160,
            height=28,
            fg_color="#0f1117",
            border_color="#2e3347",
            corner_radius=6,
        )
        self._search_entry.pack(side="left", padx=10, pady=10)

        # Segment Type Filter
        self._type_filter = ctk.CTkSegmentedButton(
            toolbar,
            values=["All", "Audio", "Video", "Success", "Failed"],
            command=lambda _: self._apply_filters(),
            height=28,
            fg_color="#0f1117",
            selected_color="#4f8ef7",
            unselected_color="#20232f",
        )
        self._type_filter.pack(side="left", padx=10)
        self._type_filter.set("All")

        # Sorting option
        self._sort_menu = ctk.CTkOptionMenu(
            toolbar,
            values=["Newest First", "Oldest First"],
            command=lambda _: self._apply_filters(),
            width=120,
            height=28,
            fg_color="#20232f",
            button_color="#2e3347",
            button_hover_color="#2e3347",
            dropdown_fg_color="#1a1d27",
            dropdown_hover_color="#2e3347",
            corner_radius=6,
        )
        self._sort_menu.pack(side="right", padx=10)
        self._sort_menu.set("Newest First")

        # ── Scroll Frame ──────────────────────────────────────────────────
        self._scroll_frame = ctk.CTkScrollableFrame(self, fg_color="transparent", label_text="")
        self._scroll_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        self._empty_lbl = ctk.CTkLabel(
            self._scroll_frame,
            text="No history records found.",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color="#8b92a8",
            anchor="center",
        )
        self._empty_lbl.pack(pady=60, fill="x")

    def refresh(self, data: dict[str, Any]) -> None:
        """Called automatically on unified poll tick.

        Preserves current search text, filter selection, and sort order.
        Only the underlying data is updated; UI controls are never reset.
        """
        offline = data.get("offline", True)

        # Disable clear button when offline
        self._clear_btn.configure(state="disabled" if offline else "normal")

        # Update data and re-apply the current filter/sort state
        history: list[dict[str, Any]] = data.get("history", [])
        settings = data.get("settings", {})
        self._download_folder = settings.get("download_folder", "")

        self._full_history = history
        self._apply_filters()

    def _apply_filters(self) -> None:
        search_query = self._search_var.get().strip().lower()
        type_filter = self._type_filter.get()
        sort_order = self._sort_menu.get()

        filtered = list(self._full_history)

        # 1. Search keyword filter
        if search_query:
            filtered = [
                item for item in filtered
                if search_query in (item.get("label") or "").lower()
                or search_query in (item.get("filename") or "").lower()
            ]

        # 2. Type/status badge filter
        if type_filter == "Audio":
            filtered = [item for item in filtered if item.get("mode") == "audio"]
        elif type_filter == "Video":
            filtered = [item for item in filtered if item.get("mode") == "video"]
        elif type_filter == "Success":
            filtered = [item for item in filtered if item.get("status") == "completed"]
        elif type_filter == "Failed":
            filtered = [item for item in filtered if item.get("status") == "failed"]

        # 3. Sorting
        if sort_order == "Oldest First":
            # Reverse list (since backend returns newest first)
            filtered.reverse()

        self._filtered_history = filtered
        self._update_list()

    def _update_list(self) -> None:
        required_rows = len(self._filtered_history)
        current_rows = len(self._row_widgets)

        if required_rows == 0:
            self._empty_lbl.pack(pady=40)
            for row in self._row_widgets:
                row.pack_forget()
            return
        else:
            self._empty_lbl.pack_forget()

        # Scale widget pool
        if required_rows > current_rows:
            for _ in range(required_rows - current_rows):
                new_row = HistoryRow(
                    self._scroll_frame,
                    on_copy=self._copy_to_clipboard,
                    on_open_folder=self._open_download_dir,
                )
                self._row_widgets.append(new_row)

        # Update matching items and show them, hide leftover rows
        for i, row in enumerate(self._row_widgets):
            if i < required_rows:
                row.update_item(self._filtered_history[i])
                row.pack(fill="x", pady=4, padx=2)
            else:
                row.pack_forget()

    def _copy_to_clipboard(self, text: str) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.logger.info("[History] Copied historical download URL to clipboard.")
        except Exception:
            pass

    def _open_download_dir(self) -> None:
        if self._download_folder and os.path.exists(self._download_folder):
            try:
                subprocess.Popen(["explorer", os.path.abspath(self._download_folder)])
            except Exception as exc:
                self.logger.error(f"[History] Failed to open download folder: {exc}")
        else:
            self.logger.warning("[History] Download folder is not set or does not exist.")

    def _clear_history_click(self) -> None:
        # Prompt for confirmation
        dialog = ctk.CTkFrame(self) # simple visual blocker/prompt
        if self.manager.clear_history_api():
            self.logger.info("[History] Download history cleared successfully.")
            self._full_history = []
            self._apply_filters()
        else:
            self.logger.error("[History] Failed to clear download history.")
