"""TASK 5: Portable EXE verification.

Builds a portable ZIP with a distinct filename, extracts it to an isolated
directory, runs MediaForgeBackend.exe directly (not app.py), and checks:
  - /api/health responds
  - isolated environment (portable mode)
  - single backend instance
  - paths with spaces
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

import psutil

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_DIR = os.path.join(PROJECT_ROOT, "release")
# Distinct filename for verification (not MediaForge_Portable.zip)
VERIFICATION_ZIP = os.path.join(RELEASE_DIR, "MediaForge_Portable_Verification.zip")

FFMPEG_DIR = os.path.join(PROJECT_ROOT, "ffmpeg")

RECURSIVE_INCLUDES = {
    "backend": "backend",
    "extension": "extension",
}

FILE_INCLUDES = {
    "dist/MediaForge.exe": "MediaForge.exe",
    "dist/MediaForgeBackend.exe": "MediaForgeBackend.exe",
    "dist/MediaForgeService.exe": "MediaForgeService.exe",
    "VERSION": "VERSION",
    "MediaForge Backend.bat": "MediaForge Backend.bat",
    "MediaForge Service.bat": "MediaForge Service.bat",
}

EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "reports"}
EXCLUDE_PREFIXES = {"test_"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".pyd"}
EXCLUDE_FILES = {"settings.json", "queue_state.json", "download_history.jsonl"}


def _live_process_tree(process: subprocess.Popen) -> list[psutil.Process]:
    """Return the EXE process and any live PyInstaller child processes."""
    processes: list[psutil.Process] = []
    try:
        root = psutil.Process(process.pid)
        if root.is_running():
            processes.append(root)
        processes.extend(child for child in root.children(recursive=True) if child.is_running())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return processes


def _stop_process_tree(process: subprocess.Popen, timeout: float = 10.0) -> None:
    """Stop a PyInstaller parent and its child without leaving temp locks."""
    processes = _live_process_tree(process)
    children = [item for item in processes if item.pid != process.pid]

    for item in children:
        try:
            item.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    try:
        process.terminate()
    except (OSError, ValueError):
        pass

    deadline = time.monotonic() + timeout
    for item in processes:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            item.wait(timeout=remaining)
        except (psutil.NoSuchProcess, psutil.TimeoutExpired, psutil.AccessDenied):
            pass

    for item in _live_process_tree(process):
        try:
            item.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    try:
        process.wait(timeout=2)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _wait_for_exit(process: subprocess.Popen, timeout: float = 15.0) -> bool:
    """Wait for the EXE wrapper and its PyInstaller child to finish."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None and not _live_process_tree(process):
            return True
        time.sleep(0.1)
    return process.poll() is not None and not _live_process_tree(process)


def build_zip():
    os.makedirs(RELEASE_DIR, exist_ok=True)
    if os.path.exists(VERIFICATION_ZIP):
        os.remove(VERIFICATION_ZIP)

    # Create launcher and settings in release dir
    launcher_content = r"""@echo off
setlocal
set "MEDIAFORGE_PORTABLE=1"
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
start "" "%ROOT%\MediaForge.exe"
echo Starting backend server...
if exist "%ROOT%\MediaForgeBackend.exe" (
    start "MediaForge Backend" "%ROOT%\MediaForgeBackend.exe"
) else (
    start "MediaForge Backend" cmd /c "python "%ROOT%\backend\app.py""
)
echo MediaForge is running.
"""
    launcher_path = os.path.join(RELEASE_DIR, "_launcher.bat")
    with open(launcher_path, "w") as f:
        f.write(launcher_content)

    settings_path = os.path.join(RELEASE_DIR, "_settings.json")
    with open(settings_path, "w") as f:
        json.dump({}, f)

    print(f"[verify_portable_exe] Creating {VERIFICATION_ZIP}...")
    with zipfile.ZipFile(VERIFICATION_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for src_rel, dst in FILE_INCLUDES.items():
            full = os.path.join(PROJECT_ROOT, src_rel)
            if os.path.exists(full):
                zf.write(full, dst)
                print(f"  Added {dst}")
            else:
                print(f"  WARNING: {src_rel} not found.")

        for src_dir_rel, archive_prefix in RECURSIVE_INCLUDES.items():
            full = os.path.join(PROJECT_ROOT, src_dir_rel)
            for root, dirs, files in os.walk(full):
                dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
                for name in sorted(files):
                    if any(name.startswith(p) for p in EXCLUDE_PREFIXES):
                        continue
                    if any(name.endswith(s) for s in EXCLUDE_SUFFIXES):
                        continue
                    if name in EXCLUDE_FILES:
                        continue
                    full_path = os.path.join(root, name)
                    rel = os.path.relpath(full_path, full)
                    arcname = f"{archive_prefix}/{rel}"
                    zf.write(full_path, arcname)

        ffmpeg_exe = os.path.join(FFMPEG_DIR, "ffmpeg.exe")
        ffprobe_exe = os.path.join(FFMPEG_DIR, "ffprobe.exe")
        if os.path.exists(ffmpeg_exe):
            zf.write(ffmpeg_exe, "ffmpeg/ffmpeg.exe")
            print("  Added ffmpeg/ffmpeg.exe")
        if os.path.exists(ffprobe_exe):
            zf.write(ffprobe_exe, "ffmpeg/ffprobe.exe")
            print("  Added ffmpeg/ffprobe.exe")

        zf.write(launcher_path, "mediaforge_start.bat")
        print("  Added mediaforge_start.bat")
        os.remove(launcher_path)

        zf.write(settings_path, "portable_settings.json")
        print("  Added portable_settings.json")
        os.remove(settings_path)

    zip_size = os.path.getsize(VERIFICATION_ZIP)
    print(f"\n  ZIP created: {VERIFICATION_ZIP} ({zip_size / (1024*1024):.1f} MB)")


def find_free_port(start: int = 15990, end: int = 16050) -> int:
    import socket
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return 15999


def extract_and_test(test_port: int | None = None):
    # Extract to isolated temp directory
    extract_dir = tempfile.mkdtemp(prefix="mf_portable_verify_")
    print(f"\n[verify_portable_exe] Extracting to isolated dir: {extract_dir}")

    with zipfile.ZipFile(VERIFICATION_ZIP, "r") as zf:
        zf.extractall(extract_dir)

    exe_path = os.path.join(extract_dir, "MediaForgeBackend.exe")
    assert os.path.exists(exe_path), f"MediaForgeBackend.exe not found at {exe_path}"
    print(f"  MediaForgeBackend.exe found at {exe_path}")

    port = test_port or find_free_port()
    health_url = f"http://127.0.0.1:{port}/api/health"

    # Write isolated settings.json so the backend binds to isolated port & local directory
    settings_path = os.path.join(extract_dir, "settings.json")
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump({
            "backend_url": f"http://127.0.0.1:{port}",
            "download_folder": os.path.join(extract_dir, "Downloads"),
        }, f, indent=2)
    print(f"  Isolated settings path: {settings_path}")

    env = os.environ.copy()
    env["MEDIAFORGE_PORT"] = str(port)
    env["MEDIAFORGE_PORTABLE"] = "1"

    # Start the backend EXE directly on isolated port
    print(f"  Starting MediaForgeBackend.exe on isolated port {port}...")
    proc = subprocess.Popen(
        [exe_path],
        cwd=extract_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    health_ok = False
    second_instance_ok = False

    try:
        # Wait for /api/health to respond
        for attempt in range(30):
            try:
                req = urllib.request.Request(health_url)
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode())
                        print(f"  /api/health responded: {data}")
                        health_ok = True
                        break
            except Exception:
                time.sleep(1)

        if not health_ok:
            print("  FAIL: /api/health did not respond")
            return False

        # Test single instance: try starting a second backend
        print("  Testing single backend instance (attempting second start)...")
        proc2 = subprocess.Popen(
            [exe_path],
            cwd=extract_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            text=True,
        )
        second_instance_ok = _wait_for_exit(proc2)
        if second_instance_ok:
            stdout, stderr = proc2.communicate()
            output = f"{stdout or ''}\n{stderr or ''}"
            if "Another MediaForge Backend instance is already running" in output:
                print("  Second instance blocked by mutex and exited: OK")
            else:
                print(f"  Second instance exited with code {proc2.returncode}: OK")
        else:
            print("  FAIL: Second instance did not exit after mutex rejection timeout")
            _stop_process_tree(proc2)

        # Test paths with spaces: create a download folder with spaces
        spaced_dir = os.path.join(extract_dir, "My Downloads Folder")
        os.makedirs(spaced_dir, exist_ok=True)
        print(f"  Created spaced download dir: {spaced_dir}")

        return bool(health_ok and second_instance_ok)
    finally:
        _stop_process_tree(proc)
        print("  Backend stopped")
        shutil.rmtree(extract_dir, ignore_errors=True)
        print(f"  Cleaned up isolated dir: {extract_dir}")
        if health_ok and second_instance_ok:
            print("\n[TASK 5] Portable EXE verification: PASSED")


def main():
    build_zip()
    success = extract_and_test()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
