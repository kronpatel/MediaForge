"""
base_page.py – BasePage

Base class defining the shared page lifecycle for all dashboard panels.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import customtkinter as ctk

if TYPE_CHECKING:
    from backend_manager import BackendManager
    from logger import AppLogger


class BasePage(ctk.CTkFrame):
    """
    Standard interface for all Companion sidebar pages.
    """

    def __init__(self, master: ctk.CTk, manager: BackendManager, logger: AppLogger, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.manager = manager
        self.logger = logger

    def refresh(self, data: dict[str, Any]) -> None:
        """Called automatically on unified poll state changes (run on Tkinter thread)."""
        pass

    def on_show(self) -> None:
        """Invoked when the user switches to this page."""
        pass

    def on_hide(self) -> None:
        """Invoked when the user navigates away from this page."""
        pass
