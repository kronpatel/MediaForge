"""
main.py – MediaForge Companion entry point.

Wires together AppLogger, BackendManager, and CompanionWindow, then hands
control to the Tk event loop.

Usage
-----
    python companion/main.py

Or double-click via a shortcut / launcher.
"""

from __future__ import annotations

import sys

# Guard: must be run as a script, never imported
if __name__ != "__main__":
    raise RuntimeError(
        "companion/main.py should be executed directly, not imported."
    )

# ---------------------------------------------------------------------------
# Suppress console window on Windows when launched via pythonw.exe or a
# compiled executable.  When running through a normal python.exe in a terminal
# (e.g. during development) this has no effect.
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    import ctypes
    try:
        # SW_HIDE = 0; only hides the *current* console if one was accidentally
        # allocated by the shell – subprocess children inherit CREATE_NO_WINDOW.
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Imports (after potential console hide)
# ---------------------------------------------------------------------------

from logger import AppLogger
from backend_manager import BackendManager
from ui import CompanionWindow


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def main() -> None:
    """Create and run the MediaForge Companion application."""

    logger = AppLogger(debug=False)
    logger.info("MediaForge Companion starting…")

    try:
        manager = BackendManager(logger=logger)
    except Exception as exc:  # noqa: BLE001
        # A truly catastrophic failure before the window even opens
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "MediaForge Companion",
            f"Failed to initialise BackendManager:\n\n{exc}\n\n"
            "Please check your installation.",
        )
        root.destroy()
        sys.exit(1)

    try:
        window = CompanionWindow(manager=manager, logger=logger)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to create main window: {exc}", exc=exc)
        manager.shutdown()
        sys.exit(1)

    logger.info("Companion ready.")

    try:
        window.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        # Always signal the monitor thread to exit cleanly
        manager.shutdown()
        if manager.is_managed():
            manager.stop()

    logger.info("Companion exited.")


main()
