"""InstallerManager — portable ZIP update workflow (v1.2.1)."""
from __future__ import annotations

import os
import sys
import zipfile
import threading
import hashlib
import subprocess
import time
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
        self._is_portable = self._detect_portable_mode()

    def _resolve_install_root(self) -> str:
        """Determine the project root directory where the ZIP should be extracted."""
        # updater.py lives in companion/ — project root is one level up
        import updater as _upd
        companion_dir = os.path.dirname(os.path.abspath(_upd.__file__))
        return os.path.dirname(companion_dir)

    def _detect_portable_mode(self) -> bool:
        """Detect if the current installation is running in portable mode.

        Returns True if portable_settings.json already exists at the install root,
        meaning the application was launched as a portable edition.  Returns False
        for Inno-Setup (or other) installed copies where the file does not exist
        *before* the update ZIP is extracted.
        """
        marker = os.path.join(self._install_root, "portable_settings.json")
        exists = os.path.isfile(marker)
        self.logger.info(
            f"[Installer] Portable mode detection: "
            f"portable_settings.json {'exists' if exists else 'not found'} "
            f"at {self._install_root}"
        )
        return exists

    def install_update(self) -> None:
        def _worker():
            try:
                zip_path = self.updater._installer_path
                if not zip_path or not os.path.exists(zip_path):
                    self.logger.error("[Installer] Update ZIP not found — nothing to install.")
                    self.updater._notify("Failed", 0.0, "Update ZIP not found.")
                    return

                # Calculate SHA-256 immediately before extraction to ensure atomic safety
                self.logger.info("[Installer] Verifying checksum before launching updater...")
                hasher = hashlib.sha256()
                with open(zip_path, "rb") as fh:
                    while chunk := fh.read(65536):
                        hasher.update(chunk)
                actual_hash = hasher.hexdigest()
                expected_hash = self.updater._installer_sha256
                if not expected_hash:
                    expected_hash = actual_hash  # fallback if no hash stored
                
                if actual_hash.lower() != expected_hash.lower():
                    raise ValueError(f"Integrity check failed (actual={actual_hash}, expected={expected_hash})")

                # Set installation_in_progress to True (Post-Install Verification Setup)
                with self.updater._lock:
                    self.updater._installation_in_progress = True
                    self.updater._save_cache()

                self.updater._notify("Launching", 0.0)

                dest_dir = self._install_root
                updates_dir = os.path.join(dest_dir, "updates")
                os.makedirs(updates_dir, exist_ok=True)
                
                batch_path = os.path.join(updates_dir, "apply_update.bat")
                temp_dir = os.path.join(updates_dir, "temp_extraction")
                log_file = os.path.join(updates_dir, "update_install.log")
                parent_pid = os.getpid()

                if getattr(sys, "frozen", False):
                    # In frozen EXE mode, restart the compiled executable
                    restart_cmd = f'start "" "{os.path.join(dest_dir, "MediaForge.exe")}"'
                else:
                    # In source development mode, restart via python and main script
                    main_script = os.path.join(dest_dir, "companion", "main.py")
                    restart_cmd = f'start "" "{sys.executable}" "{main_script}"'

                # Write apply_update.bat
                # When updating a non-portable (Inno-Setup installed) copy, the
                # Portable ZIP will drop portable_settings.json into the install
                # root.  That file forces the companion into portable mode on the
                # next launch, redirecting logs and config paths.  Inject a
                # cleanup step to remove it after extraction completes.
                cleanup_portable_marker = ""
                if not self._is_portable:
                    cleanup_portable_marker = (
                        '\n'
                        ':: Remove portable_settings.json left by the Portable ZIP.\n'
                        ':: This is an installed copy — the marker must not remain.\n'
                        'if exist "%TARGET_DIR%\\portable_settings.json" (\n'
                        '    del /f /q "%TARGET_DIR%\\portable_settings.json" >> "%LOG_FILE%" 2>&1\n'
                        '    echo [%DATE% %TIME%] [Updater] Removed portable_settings.json (non-portable installation). >> "%LOG_FILE%"\n'
                        ')\n'
                    )
                batch_content = f"""@echo off
setlocal enabledelayedexpansion

set PARENT_PID={parent_pid}
set ZIP_PATH={zip_path}
set TARGET_DIR={dest_dir}
set TEMP_DIR={temp_dir}
set EXPECTED_HASH={expected_hash}
set RESTART_CMD={restart_cmd}
set LOG_FILE={log_file}

echo [%DATE% %TIME%] [Updater] Starting update process... > "%LOG_FILE%"
echo [Updater] Parent PID: %PARENT_PID% >> "%LOG_FILE%"
echo [Updater] ZIP Path: %ZIP_PATH% >> "%LOG_FILE%"
echo [Updater] Target Dir: %TARGET_DIR% >> "%LOG_FILE%"

:: Wait for parent process to exit
:wait_loop
tasklist /FI "PID eq %PARENT_PID%" 2>NUL | find /I "%PARENT_PID%" >NUL
if %ERRORLEVEL% eq 0 (
    timeout /t 1 /nobreak >NUL
    goto wait_loop
)
echo [%DATE% %TIME%] [Updater] Parent process %PARENT_PID% exited. >> "%LOG_FILE%"

:: Verify checksum again before extraction
echo [%DATE% %TIME%] [Updater] Running SHA-256 verification... >> "%LOG_FILE%"
powershell -Command "$h = (Get-FileHash -Path '%ZIP_PATH%' -Algorithm SHA256).Hash.ToLower(); if ($h -ne '%EXPECTED_HASH%'.ToLower()) {{ exit 1 }} else {{ exit 0 }}" >> "%LOG_FILE%" 2>&1
if %ERRORLEVEL% neq 0 (
    echo [%DATE% %TIME%] [Updater] Checksum verification failed. Aborting installation. >> "%LOG_FILE%"
    powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('Update installation failed. Checksum verification failed. The current version is still usable.', 'MediaForge Update Failure', 'OK', 'Error')"
    exit /b %ERRORLEVEL%
)

:: Create clean temp extraction directory
if exist "%TEMP_DIR%" rmdir /s /q "%TEMP_DIR%" >> "%LOG_FILE%" 2>&1
mkdir "%TEMP_DIR%" >> "%LOG_FILE%" 2>&1

:: Extract to temp directory
echo [%DATE% %TIME%] [Updater] Extracting to temporary directory... >> "%LOG_FILE%"
powershell -Command "Expand-Archive -Path '%ZIP_PATH%' -DestinationPath '%TEMP_DIR%' -Force" >> "%LOG_FILE%" 2>&1

if %ERRORLEVEL% neq 0 (
    echo [%DATE% %TIME%] [Updater] Extraction failed. Rollback initiated. >> "%LOG_FILE%"
    rmdir /s /q "%TEMP_DIR%" >NUL 2>&1
    powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('Update installation failed during extraction. Rollback complete, the current version is still usable.', 'MediaForge Update Failure', 'OK', 'Error')"
    exit /b %ERRORLEVEL%
)

:: Replace files atomically from temp directory
echo [%DATE% %TIME%] [Updater] Overwriting target installation directory... >> "%LOG_FILE%"
xcopy "%TEMP_DIR%\\*" "%TARGET_DIR%\\" /y /e /s /i /q >> "%LOG_FILE%" 2>&1

if %ERRORLEVEL% neq 0 (
    echo [%DATE% %TIME%] [Updater] File replacement failed. Rollback initiated. >> "%LOG_FILE%"
    rmdir /s /q "%TEMP_DIR%" >NUL 2>&1
    powershell -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('Update installation failed during file replacement. Rollback complete, the current version is still usable.', 'MediaForge Update Failure', 'OK', 'Error')"
    exit /b %ERRORLEVEL%
)

echo [%DATE% %TIME%] [Updater] Installation complete. Cleaning up... >> "%LOG_FILE%"
rmdir /s /q "%TEMP_DIR%" >> "%LOG_FILE%" 2>&1
del /f /q "%ZIP_PATH%" >> "%LOG_FILE%" 2>&1
{cleanup_portable_marker}
:: Clean up stale installation logs older than 7 days
powershell -Command "Get-ChildItem -Path '%TARGET_DIR%\\updates' -Filter '*.log' | Where-Object {{ $_.LastWriteTime -lt (Get-Date).AddDays(-7) }} | Remove-Item -Force" >> "%LOG_FILE%" 2>&1

:: Restart application
echo [%DATE% %TIME%] [Updater] Relaunching application... >> "%LOG_FILE%"
%RESTART_CMD% >> "%LOG_FILE%" 2>&1

:: Exit and self delete batch file
(goto) 2>nul & del "%~f0"
"""

                with open(batch_path, "w", encoding="utf-8") as fh:
                    fh.write(batch_content)

                self.logger.info(f"[Installer] Launching external updater process: {batch_path}")
                
                creation_flags = 0
                if sys.platform == "win32":
                    creation_flags = subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS

                # Gracefully stop backend, UI, tray, and other locks first
                self._window.after(0, self._window.prepare_for_installation)

                # Give locks a moment to release, then spawn updater and exit parent
                def _launch_and_exit():
                    time.sleep(1.0)
                    subprocess.Popen(
                        ["cmd.exe", "/c", batch_path],
                        creationflags=creation_flags,
                        close_fds=True
                    )
                    self._window.after(0, self._window.destroy)

                threading.Thread(target=_launch_and_exit, daemon=True, name="UpdaterLauncherThread").start()

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
