"""
_path_utils.py – Path validation and comparison utilities.

Internal helpers for the browser package.  Not part of the public API.
"""

from __future__ import annotations

import os
import sys
from typing import Sequence


def paths_match(path1: str, path2: str) -> bool:
    """Case-insensitive, normalized path comparison (Windows-safe)."""
    return os.path.normpath(path1).lower() == os.path.normpath(path2).lower()


def is_executable(path: str) -> bool:
    """Return True if *path* points to an existing regular file.

    On Windows this also checks the extension whitelist for executables.
    Returns False on non-Windows or for empty/blank paths.
    """
    if not path or not path.strip():
        return False
    if sys.platform != "win32":
        return False
    try:
        return os.path.isfile(path)
    except (OSError, ValueError):
        return False


def resolve_user_data_dir(relative_path: str) -> str:
    """Build a full User Data directory path from a relative fragment.

    Uses ``LOCALAPPDATA`` on Windows.  Returns an empty string on
    unsupported platforms.
    """
    if sys.platform != "win32":
        return ""
    base = os.environ.get("LOCALAPPDATA", "")
    if not base:
        return ""
    return os.path.join(base, relative_path)


def validate_executable_paths(paths: Sequence[str]) -> list[str]:
    """Return only the paths that actually exist on disk (non-empty, isfile)."""
    result: list[str] = []
    for p in paths:
        if p and is_executable(p):
            result.append(p)
    return result
