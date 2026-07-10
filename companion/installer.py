"""InstallerManager — portable ZIP update workflow (v1.2.0)."""
from __future__ import annotations

import os
import sys
import zipfile
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from logger import AppLogger
    from updater import UpdateManager
    from ui import CompanionWindow


class InstallerManager:
    def __init__(self, logger: AppLogger, updater: UpdateManager, window: CompanionWindow) -> None:
        self.logger = logger
        self.updater = updater
        self._window = window
        self._install_root = self._resolve_install_root()
        self._events: list[str] = []

    def _resolve_install_root(self) -> str:
        """Determine the project root directory where the ZIP should be extracted."""
        # updater.py lives in companion/ — project root is one level up
        import updater as _upd
        companion_dir = os.path.dirname(os.path.abspath(_upd.__file__))
        return os.path.dirname(companion_dir)

    def install_update(self) -> None:
        def _worker():
            try:
                zip_path = self.updater._installer_path
                if not zip_path or not os.path.exists(zip_path):
                    self.logger.error("[Installer] Update ZIP not found — nothing to install.")
                    self.updater._notify("Failed", 0.0, "Update ZIP not found.")
                    return

                self.updater._notify("Launching", 0.0)
                self.logger.info(f"[Installer] Extracting {zip_path} to {self._install_root}…")

                with zipfile.ZipFile(zip_path, "r") as zf:
                    # Validate expected structure
                    names = zf.namelist()
                    if not names:
                        raise ValueError("ZIP archive is empty.")

                    # Prepare window for restart
                    self._window.prepare_for_installation()

                    zf.extractall(self._install_root)

                self.logger.info("[Installer] Extraction complete.")
                self.updater._notify("Restarting Companion", 100.0)
                self._events.append(f"Extracted {zip_path} to {self._install_root}")

                # Schedule restart on main thread
                self._window.after(500, self._restart_companion)

            except Exception as exc:
                self.logger.error(f"[Installer] Install failed: {exc}", exc=exc)
                self.updater._notify("Failed", 0.0, str(exc))

        threading.Thread(target=_worker, daemon=True, name="InstallerWorker").start()

    def _restart_companion(self) -> None:
        """Restart the companion application after update extraction."""
        self.logger.info("[Installer] Restarting Companion…")
        python = sys.executable
        main_script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "main.py"
        )
        try:
            self._window.destroy()
        except Exception:
            pass
        os.execl(python, python, main_script)

    def get_status(self) -> str:
        return "Idle"

    def get_recent_events(self) -> list[str]:
        return list(self._events)
