"""
queue_panel.py – QueuePage

Scrollable panel displaying active, pending, and completed download queue items.
Reuses row widgets from an internal pool to prevent memory leaks and maintain
smooth scroll performance.
"""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from base_page import BasePage

if TYPE_CHECKING:
    from backend_manager import BackendManager
    from logger import AppLogger


class QueueRow(ctk.CTkFrame):
    """
    A single reusable row widget in the queue table.
    """

    def __init__(self, master: ctk.CTkFrame, on_copy: Callable[[str], None], on_open_folder: Callable[[], None]) -> None:
        super().__init__(
            master,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=8,
        )
        self._on_copy = on_copy
        self._on_open_folder = on_open_folder
        self.job_id: str | None = None
        self.job_url: str = ""

        self._build_ui()

    def _build_ui(self) -> None:
        # Title & Meta Info
        self._title_lbl = ctk.CTkLabel(
            self,
            text="Job Title",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#e8eaf0",
            anchor="w",
        )
        self._title_lbl.pack(side="left", fill="x", expand=True, padx=12, pady=10)

        # Progress Block
        self._progress_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._progress_frame.pack(side="left", fill="y", padx=12)

        self._progress_bar = ctk.CTkProgressBar(
            self._progress_frame,
            fg_color="#20232f",
            progress_color="#4f8ef7",
            width=120,
            height=6,
        )
        self._progress_bar.pack(anchor="w", pady=(8, 2))
        self._progress_bar.set(0.0)

        self._progress_lbl = ctk.CTkLabel(
            self._progress_frame,
            text="0%",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color="#8b92a8",
        )
        self._progress_lbl.pack(anchor="w")

        # Stats Column (Speed & ETA)
        self._stats_frame = ctk.CTkFrame(self, fg_color="transparent", width=100)
        self._stats_frame.pack(side="left", padx=12)
        self._stats_frame.pack_propagate(False)

        self._speed_lbl = ctk.CTkLabel(
            self._stats_frame,
            text="— KB/s",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#e8eaf0",
            anchor="w",
        )
        self._speed_lbl.pack(fill="x")

        self._eta_lbl = ctk.CTkLabel(
            self._stats_frame,
            text="ETA: —",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color="#8b92a8",
            anchor="w",
        )
        self._eta_lbl.pack(fill="x")

        # Status badge
        self._status_lbl = ctk.CTkLabel(
            self,
            text="Queued",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#f59e0b",
            width=90,
            anchor="center",
        )
        self._status_lbl.pack(side="left", padx=12)

        # Action Buttons
        self._copy_btn = ctk.CTkButton(
            self,
            text="📋",
            width=28,
            height=28,
            fg_color="#20232f",
            hover_color="#2e3347",
            corner_radius=6,
            command=self._copy_click,
        )
        self._copy_btn.pack(side="left", padx=4)

        self._open_btn = ctk.CTkButton(
            self,
            text="📂",
            width=28,
            height=28,
            fg_color="#20232f",
            hover_color="#2e3347",
            corner_radius=6,
            command=self._open_click,
        )
        self._open_btn.pack(side="left", padx=(4, 12))

    def update_job(self, job: dict[str, Any]) -> None:
        self.job_id = job.get("id")
        self.job_url = job.get("url", "")
        
        # Title truncation
        label = job.get("label") or job.get("filename") or "Download Job"
        if len(label) > 42:
            label = label[:40] + "…"
        self._title_lbl.configure(text=label)

        # Progress
        progress = float(job.get("progress", 0.0))
        self._progress_bar.set(progress / 100.0)
        self._progress_lbl.configure(text=f"{int(progress)}%")

        # Status styling
        status = job.get("status", "queued").lower()
        status_colors = {
            "queued": "#f59e0b",      # amber
            "downloading": "#4f8ef7", # blue
            "completed": "#22c55e",   # green
            "failed": "#ef4444",      # red
        }
        color = status_colors.get(status, "#8b92a8")
        self._status_lbl.configure(text=status.capitalize(), text_color=color)

        # Speed and ETA
        speed = job.get("speed")
        eta = job.get("eta")
        self._speed_lbl.configure(text=speed if speed else "—")
        self._eta_lbl.configure(text=f"ETA: {eta}" if eta else "ETA: —")

    def _copy_click(self) -> None:
        if self.job_url:
            self._on_copy(self.job_url)

    def _open_click(self) -> None:
        self._on_open_folder()


class QueuePage(BasePage):
    """
    Page displaying the scrollable list of active and pending download jobs.
    Uses a reusable pool of widgets to avoid lag and CPU spikes.
    """

    def __init__(self, master: ctk.CTk, manager: BackendManager, logger: AppLogger) -> None:
        super().__init__(master, manager, logger)
        self._cached_hash = None
        self._row_widgets: list[QueueRow] = []
        self._download_folder = ""

        self._build_ui()

    def _build_ui(self) -> None:
        # Title & Toolbar
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(20, 10))

        ctk.CTkLabel(
            header_frame,
            text="Download Queue",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#e8eaf0",
        ).pack(side="left")

        # Toolbar Buttons
        self._open_dir_btn = ctk.CTkButton(
            header_frame,
            text="Open Download Folder",
            width=140,
            height=30,
            fg_color="#20232f",
            hover_color="#2e3347",
            text_color="#e8eaf0",
            corner_radius=8,
            command=self._open_download_dir,
        )
        self._open_dir_btn.pack(side="right", padx=4)

        # Scrollable rows frame
        self._scroll_frame = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            label_text="",
        )
        self._scroll_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        self._empty_lbl = ctk.CTkLabel(
            self._scroll_frame,
            text="No items in the queue.",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color="#8b92a8",
        )
        self._empty_lbl.pack(pady=40)

    def refresh(self, data: dict[str, Any]) -> None:
        """Called automatically on unified poll tick."""
        # Performance check
        current_hash = hash(str(data.get("queue")))
        if current_hash == self._cached_hash:
            return
        self._cached_hash = current_hash

        queue: list[dict[str, Any]] = data.get("queue", [])
        settings = data.get("settings", {})
        self._download_folder = settings.get("download_folder", "")

        # Manage widget pool size
        required_rows = len(queue)
        current_rows = len(self._row_widgets)

        if required_rows == 0:
            self._empty_lbl.pack(pady=40)
            for row in self._row_widgets:
                row.pack_forget()
            return
        else:
            self._empty_lbl.pack_forget()

        # Add rows if needed
        if required_rows > current_rows:
            for _ in range(required_rows - current_rows):
                new_row = QueueRow(
                    self._scroll_frame,
                    on_copy=self._copy_to_clipboard,
                    on_open_folder=self._open_download_dir,
                )
                self._row_widgets.append(new_row)

        # Populate/show and hide unused rows
        for i, row in enumerate(self._row_widgets):
            if i < required_rows:
                row.update_job(queue[i])
                row.pack(fill="x", pady=4, padx=2)
            else:
                row.pack_forget()

    def _copy_to_clipboard(self, text: str) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.logger.info("Copied download URL to clipboard.")
        except Exception:
            pass

    def _open_download_dir(self) -> None:
        if self._download_folder and os.path.exists(self._download_folder):
            try:
                subprocess.Popen(["explorer", os.path.abspath(self._download_folder)])
            except Exception as exc:
                self.logger.error(f"Failed to open download folder: {exc}")
        else:
            self.logger.warning("Download folder is not set or does not exist.")
