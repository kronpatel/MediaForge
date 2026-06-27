"""
stats_panel.py – StatsPage

Statistics dashboard panel displaying completed/failed metrics, success rates,
and backend uptime summaries inside information cards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import customtkinter as ctk

from base_page import BasePage

if TYPE_CHECKING:
    from backend_manager import BackendManager
    from logger import AppLogger


class StatsPage(BasePage):
    """
    Statistics view containing metrics cards showing total volume,
    daily metrics, success percentages, and uptime duration.
    """

    def __init__(self, master: ctk.CTk, manager: BackendManager, logger: AppLogger) -> None:
        super().__init__(master, manager, logger)
        self._cached_hash = None
        self._build_ui()

    def _build_ui(self) -> None:
        # Title
        ctk.CTkLabel(
            self,
            text="Statistics Center",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#e8eaf0",
        ).pack(anchor="w", padx=20, pady=(20, 10))

        # Subtitle
        ctk.CTkLabel(
            self,
            text="Live backend analytics and operational metrics.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#8b92a8",
        ).pack(anchor="w", padx=20, pady=(0, 20))

        # Cards Container
        self._cards_grid = ctk.CTkFrame(self, fg_color="transparent")
        self._cards_grid.pack(fill="both", expand=True, padx=16, pady=4)

        # Configure 3x2 grid layout
        self._cards_grid.columnconfigure((0, 1, 2), weight=1, uniform="equal")
        self._cards_grid.rowconfigure((0, 1), weight=1, uniform="equal")

        # Cards definitions
        self._today_card = self._create_stat_card(self._cards_grid, 0, 0, "Downloads Today", "0", "📈")
        self._completed_card = self._create_stat_card(self._cards_grid, 0, 1, "Completed Total", "0", "✅")
        self._failed_card = self._create_stat_card(self._cards_grid, 0, 2, "Failed Total", "0", "❌")
        
        self._rate_card = self._create_stat_card(self._cards_grid, 1, 0, "Success Rate", "100.0%", "📊")
        self._speed_card = self._create_stat_card(self._cards_grid, 1, 1, "Current Speed", "0 KB/s", "⚡")
        self._uptime_card = self._create_stat_card(self._cards_grid, 1, 2, "Uptime", "0s", "🕒")

    def _create_stat_card(self, parent: ctk.CTkFrame, row: int, col: int, title: str, val: str, emoji: str) -> ctk.CTkLabel:
        card = ctk.CTkFrame(
            parent,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=12,
        )
        card.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=16, pady=16, fill="both", expand=True)

        # Header: Emoji + Title
        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.pack(fill="x")

        ctk.CTkLabel(
            header,
            text=emoji,
            font=ctk.CTkFont(size=18),
        ).pack(side="left")

        ctk.CTkLabel(
            header,
            text=title,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#8b92a8",
        ).pack(side="left", padx=8)

        # Large Value
        val_lbl = ctk.CTkLabel(
            inner,
            text=val,
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold"),
            text_color="#e8eaf0",
            anchor="w",
        )
        val_lbl.pack(fill="x", pady=(14, 0))

        return val_lbl

    def refresh(self, data: dict[str, Any]) -> None:
        """Called automatically on unified poll tick."""
        stats = data.get("stats", {})
        
        # Serialize stats dict to check for changes
        current_hash = hash(str(stats))
        if current_hash == self._cached_hash:
            return
        self._cached_hash = current_hash

        offline = data.get("offline", True)

        if offline:
            self._today_card.configure(text="—")
            self._completed_card.configure(text="—")
            self._failed_card.configure(text="—")
            self._rate_card.configure(text="—")
            self._speed_card.configure(text="—")
            self._uptime_card.configure(text="—")
            return

        self._today_card.configure(text=str(stats.get("downloads_today", 0)))
        self._completed_card.configure(text=str(stats.get("completed_count", 0)))
        self._failed_card.configure(text=str(stats.get("failed_count", 0)))
        
        success_rate = stats.get("success_rate", 100.0)
        self._rate_card.configure(text=f"{success_rate}%")
        
        self._speed_card.configure(text=str(stats.get("average_speed", "0 KB/s")))
        
        uptime_sec = stats.get("backend_uptime", 0)
        self._uptime_card.configure(text=self._format_uptime(uptime_sec))

    def _format_uptime(self, secs: int) -> str:
        if secs < 60:
            return f"{secs}s"
        elif secs < 3600:
            return f"{secs // 60}m {secs % 60}s"
        else:
            hours = secs // 3600
            minutes = (secs % 3600) // 60
            return f"{hours}h {minutes}m"
