"""
drop_target.py – Windows OLE drag-and-drop target for the MediaForge Companion.

Registers the main Companion window as an OLE drop target so users can drag
URLs (plain text from a browser) or text files onto the window to queue
downloads.  Uses only ``ctypes`` — no new pip dependencies.

On non-Windows platforms, or when COM registration fails,
``register_drop_target`` is a graceful no-op (paste still works).
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
from ctypes import wintypes
from typing import Callable

from url_paste import extract_urls

logger = logging.getLogger("kerzox.drop_target")

# ---------------------------------------------------------------------------
# COM / OLE constants
# ---------------------------------------------------------------------------

S_OK = 0
S_FALSE = 1
E_NOINTERFACE = 0x80004002
DV_E_CLIPFORMAT = 0x80040068

CLIPFORMAT = wintypes.UINT
CF_UNICODETEXT = 13
CF_HDROP = 15

DROPEFFECT_NONE = 0
DROPEFFECT_COPY = 1

TYMED_HGLOBAL = 1
DVASPECT_CONTENT = 1
GMEM_MOVEABLE = 0x0002

# File extensions whose contents are scanned for URLs when dropped.
_TEXT_EXTENSIONS = {".txt", ".log", ".md", ".url", ".ini"}
MAX_DROP_READ_BYTES = 1024 * 1024  # 1 MB cap to prevent OOM on large drops

# IID_IUnknown   = {00000000-0000-0000-C000-000000000046}
# IID_IDropTarget = {00000122-0000-0000-C000-000000000046}
# Raw 16-byte in-memory layout (Data1/2/3 little-endian, Data4 big-endian).
IID_IUNKNOWN = bytes(
    b"\x00\x00\x00\x00\x00\x00\x00\x00\xc0\x00\x00\x00\x00\x00\x00\x46"
)
IID_IDROPTARGET = bytes(
    b"\x22\x01\x00\x00\x00\x00\x00\x00\xc0\x00\x00\x00\x00\x00\x00\x46"
)

ole32 = ctypes.windll.ole32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

# 64-bit-safe COM/handle types: ctypes' default restype is c_int (32-bit),
# which truncates 64-bit global-memory handles on 64-bit Windows and would
# make GlobalLock/GlobalFree raise OverflowError.  Declare explicit signatures.
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]

_WINFUNCTYPE = ctypes.WINFUNCTYPE
_PVOID = ctypes.c_void_p

# ---------------------------------------------------------------------------
# COM structures
# ---------------------------------------------------------------------------


class FORMATETC(ctypes.Structure):
    _fields_ = [
        ("cfFormat", CLIPFORMAT),
        ("ptd", _PVOID),
        ("dwAspect", wintypes.DWORD),
        ("lindex", wintypes.LONG),
        ("tymed", wintypes.DWORD),
    ]


class STGMEDIUM(ctypes.Structure):
    _fields_ = [
        ("tymed", wintypes.DWORD),
        ("u", _PVOID),          # hGlobal (or HDROP for CF_HDROP)
        ("pUnkForRelease", _PVOID),
    ]


class POINTL(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


# ---------------------------------------------------------------------------
# COM callback signatures (first argument is always the `this` pointer)
# ---------------------------------------------------------------------------

# This Python build has no ctypes.GUID, so define a plain equivalent for the
# COM callback signatures (IUnknown/IDropTarget method pointers take refs to GUIDs).
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


_QUERY_INTERFACE = _WINFUNCTYPE(wintypes.HRESULT, _PVOID,
                                ctypes.POINTER(GUID), ctypes.POINTER(_PVOID))
_ADDREF = _WINFUNCTYPE(wintypes.ULONG, _PVOID)
_RELEASE = _WINFUNCTYPE(wintypes.ULONG, _PVOID)
_DRAG_ENTER = _WINFUNCTYPE(wintypes.HRESULT, _PVOID, _PVOID, wintypes.DWORD,
                           POINTL, ctypes.POINTER(wintypes.DWORD))
_DRAG_OVER = _WINFUNCTYPE(wintypes.HRESULT, _PVOID, _PVOID, wintypes.DWORD,
                          POINTL, ctypes.POINTER(wintypes.DWORD))
_DRAG_LEAVE = _WINFUNCTYPE(wintypes.HRESULT, _PVOID)
_DROP = _WINFUNCTYPE(wintypes.HRESULT, _PVOID, _PVOID, wintypes.DWORD,
                     POINTL, ctypes.POINTER(wintypes.DWORD))

_GET_DATA = _WINFUNCTYPE(wintypes.HRESULT, _PVOID,
                         ctypes.POINTER(FORMATETC), ctypes.POINTER(STGMEDIUM))
_QUERY_GET_DATA = _WINFUNCTYPE(wintypes.HRESULT, _PVOID,
                               ctypes.POINTER(FORMATETC))


def _fmtetc(cf_format: int) -> FORMATETC:
    return FORMATETC(
        cfFormat=cf_format,
        ptd=None,
        dwAspect=DVASPECT_CONTENT,
        lindex=-1,
        tymed=TYMED_HGLOBAL,
    )


# ---------------------------------------------------------------------------
# Drop target COM object
# ---------------------------------------------------------------------------


class DropTarget:
    """An OLE IDropTarget implementation.

    The Python wrapper owns lifetime (register → revoke), so the object is
    never self-destructed from Release.
    """

    def __init__(self, on_urls: Callable[[list[str]], None]) -> None:
        self._on_urls = on_urls
        self._ref_count = 1
        self._lock = threading.Lock()
        self._hwnd: int | None = None
        self._registered = False
        self._ole_initialized = False

        # Closures capture this instance; COM calls ignore the `this` pointer.
        self._callbacks = [
            _QUERY_INTERFACE(self._query_interface),
            _ADDREF(self._add_ref),
            _RELEASE(self._release),
            _DRAG_ENTER(self._drag_enter),
            _DRAG_OVER(self._drag_over),
            _DRAG_LEAVE(self._drag_leave),
            _DROP(self._drop),
        ]
        self._vtbl = (_PVOID * len(self._callbacks))()
        for index, callback in enumerate(self._callbacks):
            self._vtbl[index] = ctypes.cast(callback, _PVOID).value
        # Object layout: first 8 bytes = pointer to the vtable.
        self._instance = (_PVOID * 1)(ctypes.addressof(self._vtbl))

    # -- IUnknown -----------------------------------------------------------

    def _query_interface(self, _this, riid_ptr, ppv_ptr) -> int:
        riid = ctypes.string_at(riid_ptr, 16)
        if riid == IID_IUNKNOWN or riid == IID_IDROPTARGET:
            ppv_ptr[0] = ctypes.addressof(self._instance)
            self._add_ref()
            return S_OK
        return E_NOINTERFACE

    def _add_ref(self, _this) -> int:
        with self._lock:
            self._ref_count += 1
            return self._ref_count

    def _release(self, _this) -> int:
        with self._lock:
            self._ref_count -= 1
            return self._ref_count

    # -- IDropTarget --------------------------------------------------------

    def _drag_enter(self, _this, p_data_object, _grf_key_state, _pt,
                    pdw_effect) -> int:
        effect = DROPEFFECT_COPY if self._supports(p_data_object) else DROPEFFECT_NONE
        pdw_effect[0] = effect
        return S_OK

    def _drag_over(self, _this, p_data_object, _grf_key_state, _pt,
                   pdw_effect) -> int:
        effect = DROPEFFECT_COPY if self._supports(p_data_object) else DROPEFFECT_NONE
        pdw_effect[0] = effect
        return S_OK

    def _drag_leave(self, _this) -> int:
        return S_OK

    def _drop(self, _this, p_data_object, _grf_key_state, _pt,
              pdw_effect) -> int:
        urls: list[str] = []

        text = self._retrieve_text(p_data_object)
        if text:
            urls = extract_urls(text)

        if not urls:
            for path in self._get_hdrop_files(p_data_object):
                ext = os.path.splitext(path)[1].lower()
                if ext not in _TEXT_EXTENSIONS:
                    logger.debug("Skipping dropped non-text file: %s", path)
                    continue
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        urls = extract_urls(fh.read(MAX_DROP_READ_BYTES))
                        if urls:
                            break
                except OSError:
                    continue

        pdw_effect[0] = DROPEFFECT_COPY if urls else DROPEFFECT_NONE
        if urls:
            try:
                self._on_urls(urls)
            except Exception:  # noqa: BLE001 — never let a callback crash OLE
                logger.exception("Drop target callback failed")
        return S_OK

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _vtbl_ptr(p_com_object) -> int:
        ptr = ctypes.cast(
            p_com_object, ctypes.POINTER(_PVOID)
        ).contents.value
        if not ptr:
            raise ValueError("Invalid COM object pointer")
        return ptr

    def _supports(self, p_data_object) -> bool:
        return (self._query_get_data(p_data_object, CF_UNICODETEXT)
                or self._query_get_data(p_data_object, CF_HDROP))

    def _query_get_data(self, p_data_object, cf_format: int) -> bool:
        vtbl = (ctypes.c_void_p * 16).from_address(self._vtbl_ptr(p_data_object))
        query = ctypes.cast(vtbl[5], _QUERY_GET_DATA)
        fmtetc = _fmtetc(cf_format)
        try:
            return query(p_data_object, ctypes.byref(fmtetc)) == S_OK
        except Exception:  # noqa: BLE001
            return False

    def _retrieve_text(self, p_data_object) -> str | None:
        if not self._query_get_data(p_data_object, CF_UNICODETEXT):
            return None
        vtbl = (ctypes.c_void_p * 16).from_address(self._vtbl_ptr(p_data_object))
        get_data = ctypes.cast(vtbl[3], _GET_DATA)
        fmtetc = _fmtetc(CF_UNICODETEXT)
        medium = STGMEDIUM()
        hr = get_data(p_data_object, ctypes.byref(fmtetc), ctypes.byref(medium))
        if hr != S_OK:
            return None
        try:
            if medium.tymed != TYMED_HGLOBAL or not medium.u:
                return None
            locked = kernel32.GlobalLock(medium.u)
            if not locked:
                return None
            try:
                return ctypes.wstring_at(locked)
            finally:
                kernel32.GlobalUnlock(medium.u)
        finally:
            self._release_medium(medium)

    def _get_hdrop_files(self, p_data_object) -> list[str]:
        if not self._query_get_data(p_data_object, CF_HDROP):
            return []
        vtbl = (ctypes.c_void_p * 16).from_address(self._vtbl_ptr(p_data_object))
        get_data = ctypes.cast(vtbl[3], _GET_DATA)
        fmtetc = _fmtetc(CF_HDROP)
        medium = STGMEDIUM()
        hr = get_data(p_data_object, ctypes.byref(fmtetc), ctypes.byref(medium))
        if hr != S_OK:
            return []
        try:
            if medium.tymed != TYMED_HGLOBAL or not medium.u:
                return []
            hdrop = medium.u
            count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
            paths: list[str] = []
            for index in range(count):
                needed = shell32.DragQueryFileW(hdrop, index, None, 0)
                buffer = ctypes.create_unicode_buffer(needed + 1)
                shell32.DragQueryFileW(hdrop, index, buffer, needed + 1)
                paths.append(buffer.value)
            return paths
        finally:
            self._release_medium(medium)

    @staticmethod
    def _release_medium(medium: STGMEDIUM) -> None:
        if medium.pUnkForRelease:
            unk_vtbl = ctypes.cast(
                medium.pUnkForRelease, ctypes.POINTER(_PVOID)
            ).contents[0]
            release = ctypes.cast(unk_vtbl, _RELEASE)
            try:
                release(medium.pUnkForRelease)
            except Exception:  # noqa: BLE001
                pass
        # CF_HDROP handles are owned by the data object — never GlobalFree.
        # Real HGLOBAL memory (e.g. CF_UNICODETEXT) is freed by the caller of
        # GetData; we already copied the string out above, so free it here.
        elif medium.u:
            kernel32.GlobalFree(medium.u)

    # -- registration -------------------------------------------------------

    def register(self, hwnd: int) -> None:
        if ole32.OleInitialize(None) == S_OK:
            self._ole_initialized = True
        hr = ole32.OleRegisterDropTarget(wintypes.HWND(hwnd),
                                         ctypes.addressof(self._instance))
        if hr != S_OK:
            if self._ole_initialized:
                ole32.OleUninitialize()
                self._ole_initialized = False
            raise RuntimeError(f"OleRegisterDropTarget failed: hr={hr}")
        self._hwnd = hwnd
        self._registered = True
        logger.info("Registered OLE drop target on window %d", hwnd)

    def revoke(self) -> None:
        if self._hwnd is not None and self._registered:
            try:
                ole32.RevokeDropTarget(wintypes.HWND(self._hwnd))
            except Exception:  # noqa: BLE001
                pass
            if self._ole_initialized:
                try:
                    ole32.OleUninitialize()
                except Exception:  # noqa: BLE001
                    pass
            self._registered = False
            self._hwnd = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def register_drop_target(
    hwnd: int,
    on_urls: Callable[[list[str]], None],
) -> Callable[[], None]:
    """Register ``hwnd`` as an OLE drop target.

    ``on_urls`` is invoked with the list of URLs extracted from dropped text
    or files.  Returns a revoke callable (no-op if registration is not
    supported on this platform).
    """
    if sys.platform != "win32":
        logger.warning("Drag-and-drop is Windows-only; no-op drop target installed.")
        return lambda: None

    try:
        target = DropTarget(on_urls)
        target.register(hwnd)
        return target.revoke
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to register OLE drop target: %s", exc)
        return lambda: None
