"""Windows user-level background service controller for MediaForge Backend.

MediaForge uses Task Scheduler rather than a LocalSystem Windows service.  A
user-level task keeps the backend in the same Windows account as the user, so
download folders and AppData settings remain available and no administrator
privileges are required.  It also avoids touching browser processes.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass


TASK_NAME = r"MediaForge\Backend"


@dataclass(frozen=True)
class ServiceResult:
    success: bool
    message: str
    output: str = ""


def _project_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def backend_command() -> list[str]:
    """Return the command Task Scheduler should launch."""
    root = _project_root()
    if getattr(sys, "frozen", False):
        return [os.path.join(root, "MediaForgeBackend.exe")]

    interpreter = sys.executable
    if os.name == "nt":
        candidate = os.path.join(os.path.dirname(interpreter), "pythonw.exe")
        if os.path.exists(candidate):
            interpreter = candidate
    return [interpreter, os.path.join(root, "backend", "app.py")]


def _run(args: list[str]) -> ServiceResult:
    if sys.platform != "win32":
        return ServiceResult(False, "Windows background service is available only on Windows.")
    try:
        result = subprocess.run(
            ["schtasks.exe", *args],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        return ServiceResult(False, f"Windows Task Scheduler is unavailable: {exc}")

    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return ServiceResult(False, output or "Task Scheduler command failed.", output)
    return ServiceResult(True, output or "Task Scheduler command completed.", output)


def install_service() -> ServiceResult:
    executable = backend_command()[0]
    if not os.path.exists(executable):
        return ServiceResult(False, f"Backend executable not found: {executable}")
    command = subprocess.list2cmdline(backend_command())
    return _run([
        "/Create",
        "/TN",
        TASK_NAME,
        "/TR",
        command,
        "/SC",
        "ONLOGON",
        "/RL",
        "LIMITED",
        "/F",
    ])


def start_service() -> ServiceResult:
    return _run(["/Run", "/TN", TASK_NAME])


def stop_service() -> ServiceResult:
    return _run(["/End", "/TN", TASK_NAME])


def remove_service() -> ServiceResult:
    return _run(["/Delete", "/TN", TASK_NAME, "/F"])


def query_service() -> ServiceResult:
    # SCHTASKS rejects /NH together with the LIST format.
    return _run(["/Query", "/TN", TASK_NAME, "/FO", "LIST"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the MediaForge Backend background task.")
    parser.add_argument("action", choices=("install", "start", "stop", "remove", "status"))
    args = parser.parse_args(argv)
    action = {
        "install": install_service,
        "start": start_service,
        "stop": stop_service,
        "remove": remove_service,
        "status": query_service,
    }[args.action]
    result = action()
    print(result.message)
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
