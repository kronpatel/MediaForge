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
from tray import TrayManager


# ---------------------------------------------------------------------------
# Icon Caching / Helper
# ---------------------------------------------------------------------------

def _ensure_icons(logger: AppLogger) -> None:
    """Generate icon.ico and tray.ico from icon.png if they do not exist."""
    import os
    from PIL import Image

    base = os.path.dirname(os.path.abspath(__file__))
    resources_dir = os.path.join(base, "resources")
    png_path = os.path.join(resources_dir, "icon.png")
    ico_path = os.path.join(resources_dir, "icon.ico")
    tray_ico_path = os.path.join(resources_dir, "tray.ico")

    os.makedirs(resources_dir, exist_ok=True)

    if not os.path.exists(png_path):
        logger.warning(f"icon.png is missing from {png_path}; cannot generate .ico files.")
        return

    # Generate window icon if missing
    if not os.path.exists(ico_path):
        try:
            logger.info(f"Generating window icon {ico_path}…")
            img = Image.open(png_path)
            img.save(ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        except Exception as exc:
            logger.error(f"Failed to generate window icon: {exc}", exc=exc)

    # Generate tray icon if missing
    if not os.path.exists(tray_ico_path):
        try:
            logger.info(f"Generating tray icon {tray_ico_path}…")
            img = Image.open(png_path)
            img.save(tray_ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
        except Exception as exc:
            logger.error(f"Failed to generate tray icon: {exc}", exc=exc)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def main() -> None:
    """Create and run the MediaForge Companion application."""

    logger = AppLogger(debug=False)
    logger.info("MediaForge Companion starting…")

    # Generate icons only if they don't already exist
    _ensure_icons(logger)

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

    # Initialize and wire TrayManager
    tray_manager = None
    try:
        tray_manager = TrayManager(manager=manager, window=window, logger=logger)
        window.set_tray_manager(tray_manager)
        tray_ok = tray_manager.start()
        window.tray_active = tray_ok
        if not tray_ok:
            logger.warning("System tray is disabled; falling back to windowed-only mode.")
    except Exception as exc:
        logger.warning(f"Error starting tray manager: {exc}. Running window-only.", exc=exc)
        window.tray_active = False

    logger.info("Companion ready.")

    try:
        window.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        # Stop unified poller thread
        if hasattr(window, "_dashboard_controller") and window._dashboard_controller:
            try:
                window._dashboard_controller.stop()
            except Exception:
                pass

        # Clean Tray Thread Shutdown: Stop tray and wait for it to exit
        if window.tray_active and tray_manager:
            try:
                tray_manager.stop()
            except Exception:
                pass

        # Always signal the monitor thread to exit cleanly
        manager.shutdown()
        if manager.is_managed():
            manager.stop()

    logger.info("Companion exited.")


main()
