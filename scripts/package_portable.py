"""Package the MediaForge portable edition into a ZIP archive.

The portable edition is a self-contained ZIP that runs from any folder:
  - MediaForge.exe (companion PyInstaller build)
  - backend/          (Flask backend — required)
  - ffmpeg/           (ffmpeg.exe, ffprobe.exe)
  - extension/        (browser extension — load at chrome://extensions)
  - VERSION
  - MediaForge Backend.bat  (launcher for the backend)
  - mediaforge_start.bat    (launcher for portable mode)
  - portable_settings.json  (default empty settings)
"""

import os
import sys
import zipfile
import shutil

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_DIR = os.path.join(PROJECT_ROOT, "release")
PORTABLE_ZIP = os.path.join(RELEASE_DIR, "MediaForge_Portable.zip")

FFMPEG_DIR = os.path.join(PROJECT_ROOT, "ffmpeg")

# Directories to include recursively (source -> archive prefix)
RECURSIVE_INCLUDES = {
    "backend": "backend",
    "extension": "extension",
}

# Single-file includes (relative source path -> archive path)
FILE_INCLUDES = {
    "dist/MediaForge.exe": "MediaForge.exe",
    "VERSION": "VERSION",
    "MediaForge Backend.bat": "MediaForge Backend.bat",
}


EXCLUDE_DIRS = {"__pycache__", "reports"}
EXCLUDE_PREFIXES = {"test_"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".pyd"}
EXCLUDE_FILES = {"settings.json", "queue_state.json", "download_history.jsonl"}


def _archive_dir(zf: zipfile.ZipFile, source_dir: str, archive_prefix: str) -> None:
    """Recursively add *source_dir* into the ZIP under *archive_prefix*."""
    if not os.path.isdir(source_dir):
        print(f"  SKIP  {source_dir}/ (not found)")
        return
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for name in sorted(files):
            if any(name.startswith(p) for p in EXCLUDE_PREFIXES):
                continue
            if any(name.endswith(s) for s in EXCLUDE_SUFFIXES):
                continue
            if name in EXCLUDE_FILES:
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, source_dir)
            arcname = f"{archive_prefix}/{rel}"
            zf.write(full, arcname)


def create_launcher_script():
    content = r"""@echo off
title MediaForge Portable
setlocal

:: Force portable mode (uses local folders instead of AppData)
set "MEDIAFORGE_PORTABLE=1"

echo ============================================
echo  MediaForge Portable Edition
echo ============================================
echo.

:: Get the directory this script is in
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

:: Launch MediaForge companion
start "" "%ROOT%\MediaForge.exe"

:: Launch the backend
echo Starting backend server...
start "MediaForge Backend" cmd /c "python "%ROOT%\backend\app.py""

echo MediaForge is running in the system tray.
echo.
echo You can close this window.
timeout /t 3 /nobreak >nul
"""
    script_path = os.path.join(PROJECT_ROOT, "release", "_launcher.bat")
    os.makedirs(RELEASE_DIR, exist_ok=True)
    with open(script_path, "w") as f:
        f.write(content)
    return "_launcher.bat", "mediaforge_start.bat"


def create_portable_settings():
    import json
    settings = {}
    settings_path = os.path.join(PROJECT_ROOT, "release", "_settings.json")
    os.makedirs(RELEASE_DIR, exist_ok=True)
    with open(settings_path, "w") as f:
        json.dump(settings, f)
    return "_settings.json", "portable_settings.json"


def main():
    os.makedirs(RELEASE_DIR, exist_ok=True)

    # Clean any previous portable files
    for f in ["_launcher.bat", "_settings.json"]:
        p = os.path.join(RELEASE_DIR, f)
        if os.path.exists(p):
            os.remove(p)

    launcher_src, launcher_dst = create_launcher_script()
    settings_src, settings_dst = create_portable_settings()

    if os.path.exists(PORTABLE_ZIP):
        os.remove(PORTABLE_ZIP)

    print(f"[package_portable] Creating {PORTABLE_ZIP}...")

    with zipfile.ZipFile(PORTABLE_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add single files
        for src_rel, dst in FILE_INCLUDES.items():
            full = os.path.join(PROJECT_ROOT, src_rel)
            if os.path.exists(full):
                zf.write(full, dst)
                size = os.path.getsize(full)
                print(f"  Added {dst} ({size / 1024:.1f} KB)" if size < 1024 * 1024 else f"  Added {dst} ({size / (1024*1024):.1f} MB)")
            else:
                print(f"  WARNING: {src_rel} not found. Skipping.")

        # Add recursive directories
        for src_dir_rel, archive_prefix in RECURSIVE_INCLUDES.items():
            full = os.path.join(PROJECT_ROOT, src_dir_rel)
            _archive_dir(zf, full, archive_prefix)
            dir_size = _dir_size(full) if os.path.isdir(full) else 0
            print(f"  Added {archive_prefix}/ ({dir_size / 1024:.1f} KB)" if dir_size < 1024 * 1024 else f"  Added {archive_prefix}/ ({dir_size / (1024*1024):.1f} MB)")

        # Add ffmpeg binaries if present
        ffmpeg_exe = os.path.join(FFMPEG_DIR, "ffmpeg.exe")
        ffprobe_exe = os.path.join(FFMPEG_DIR, "ffprobe.exe")
        if os.path.exists(ffmpeg_exe):
            zf.write(ffmpeg_exe, "ffmpeg/ffmpeg.exe")
            print("  Added ffmpeg/ffmpeg.exe")
        if os.path.exists(ffprobe_exe):
            zf.write(ffprobe_exe, "ffmpeg/ffprobe.exe")
            print("  Added ffmpeg/ffprobe.exe")

        # Add launcher script
        launcher_path = os.path.join(RELEASE_DIR, launcher_src)
        if os.path.exists(launcher_path):
            zf.write(launcher_path, launcher_dst)
            print(f"  Added {launcher_dst}")
            os.remove(launcher_path)

        # Add portable settings
        settings_path = os.path.join(RELEASE_DIR, settings_src)
        if os.path.exists(settings_path):
            zf.write(settings_path, settings_dst)
            print(f"  Added {settings_dst}")
            os.remove(settings_path)

    # Verify the ZIP
    zip_size = os.path.getsize(PORTABLE_ZIP)
    print(f"\n  Portable ZIP created: {PORTABLE_ZIP}")
    print(f"  Size: {zip_size / (1024*1024):.1f} MB")

    with zipfile.ZipFile(PORTABLE_ZIP, "r") as zf:
        names = zf.namelist()
        print(f"  Files inside: {len(names)}")
        for name in names:
            info = zf.getinfo(name)
            print(f"    {name} ({info.file_size / 1024:.1f} KB)")

    print("\n[package_portable] Done")
    return 0


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


if __name__ == "__main__":
    main()
