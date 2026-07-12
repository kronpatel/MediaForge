"""
_version_reader.py – Best-effort browser version extraction.

Uses Windows ``GetFileVersionInfoW`` when available, falling back to
an empty string on any error.  Never raises.
"""

from __future__ import annotations

import sys


def read_browser_version(exe_path: str) -> str:
    """Return the file version string for *exe_path*, or ``""``.

    On non-Windows platforms always returns ``""``.
    """
    if sys.platform != "win32":
        return ""

    try:
        import ctypes
        from ctypes import wintypes

        size = ctypes.windll.version.GetFileVersionInfoSizeW(exe_path, None)  # type: ignore[attr-defined]
        if not size:
            return ""

        data = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(exe_path, 0, size, data):  # type: ignore[attr-defined]
            return ""

        p_fixed = ctypes.c_void_p()
        length = wintypes.UINT()
        if not ctypes.windll.version.VerQueryValueW(  # type: ignore[attr-defined]
            data, "\\", ctypes.byref(p_fixed), ctypes.byref(length)
        ):
            return ""

        # Read the file version DWORDs from fixed info block
        buf = ctypes.cast(p_fixed, ctypes.POINTER(ctypes.c_uint32))  # type: ignore[no-any-explicitly]
        file_version_ms = buf[2]
        file_version_ls = buf[3]

        major = file_version_ms >> 16
        minor = file_version_ms & 0xFFFF
        build = file_version_ls >> 16
        patch = file_version_ls & 0xFFFF

        if major == 0 and minor == 0 and build == 0:
            return ""
        return f"{major}.{minor}.{build}.{patch}"
    except Exception:
        return ""
