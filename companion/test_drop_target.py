"""
test_drop_target.py – Tests for drop_target OLE registration + URL extraction.

Builds fake COM objects (ctypes vtables) and a fake ole32 module so nothing
requires a real drag-and-drop operation or a realized window.
"""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import drop_target


class _FakeOle32:
    """Stand-in for ctypes.windll.ole32 used to verify registration calls."""

    def __init__(self):
        self.calls = []

    def OleInitialize(self, pv):
        self.calls.append(("OleInitialize",))
        return 0  # S_OK

    def OleRegisterDropTarget(self, hwnd, target):
        self.calls.append(("Register", _ptr(hwnd), _ptr(target)))
        return 0  # S_OK

    def RevokeDropTarget(self, hwnd):
        self.calls.append(("Revoke", _ptr(hwnd)))
        return 0

    def OleUninitialize(self):
        self.calls.append(("OleUninitialize",))


def _ptr(value) -> int:
    """Coerce a raw int or ctypes pointer-ish into a plain int."""
    return int(getattr(value, "value", value))


_CALLBACKS_KEEPER = []


def _make_com_object(vtbl_entries):
    """Build a fake COM object pointer whose vtable is ``vtbl_entries``.

    Returns (object_pointer_address, vtable_array).
    """
    vtbl = (ctypes.c_void_p * len(vtbl_entries))()
    for index, entry in enumerate(vtbl_entries):
        vtbl[index] = entry
    obj = (ctypes.c_void_p * 1)(ctypes.addressof(vtbl))
    _CALLBACKS_KEEPER.extend([vtbl, obj])
    return ctypes.addressof(obj), vtbl


def _slot(func_type, func):
    """Wrap a Python function as a stdcall callback and return its vtable slot."""
    cb = func_type(func)
    _CALLBACKS_KEEPER.append(cb)
    return ctypes.cast(cb, ctypes.c_void_p).value


class TestDropTargetUrlExtraction(unittest.TestCase):
    """DropTarget._drop extracts URLs from fake IDataObject payloads."""

    def setUp(self):
        self.captured = []
        self.target = drop_target.DropTarget(
            lambda urls: self.captured.append(list(urls))
        )

    def _fake_data_object(self, text):
        """Fake IDataObject exposing CF_UNICODETEXT holding ``text``."""
        encoded = text.encode("utf-16-le") + b"\x00\x00"
        hglobal = drop_target.kernel32.GlobalAlloc(
            drop_target.GMEM_MOVEABLE, len(encoded)
        )
        locked = drop_target.kernel32.GlobalLock(hglobal)
        ctypes.memmove(locked, encoded, len(encoded))
        drop_target.kernel32.GlobalUnlock(hglobal)

        def get_data(this, p_fmtetc, p_medium):
            fmt = ctypes.cast(
                p_fmtetc, ctypes.POINTER(drop_target.FORMATETC)
            ).contents
            if fmt.cfFormat != drop_target.CF_UNICODETEXT:
                return drop_target.DV_E_CLIPFORMAT
            medium = ctypes.cast(p_medium, ctypes.POINTER(drop_target.STGMEDIUM)).contents
            medium.tymed = drop_target.TYMED_HGLOBAL
            medium.u = hglobal
            medium.pUnkForRelease = 0
            return drop_target.S_OK

        def query_get_data(this, p_fmtetc):
            fmt = ctypes.cast(
                p_fmtetc, ctypes.POINTER(drop_target.FORMATETC)
            ).contents
            if fmt.cfFormat == drop_target.CF_UNICODETEXT:
                return drop_target.S_OK
            return drop_target.DV_E_CLIPFORMAT

        entries = [
            _slot(drop_target._QUERY_INTERFACE, lambda _this, riid, ppv: drop_target.E_NOINTERFACE),
            _slot(drop_target._ADDREF, lambda _this: 2),
            _slot(drop_target._RELEASE, lambda _this: 1),
            _slot(drop_target._GET_DATA, get_data),
            _slot(drop_target._GET_DATA, lambda *args: drop_target.E_NOTIMPL),  # GetDataHere
            _slot(drop_target._QUERY_GET_DATA, query_get_data),
        ]
        pdo, _ = _make_com_object(entries)
        return pdo

    def test_drop_from_unicode_text(self):
        pdo = self._fake_data_object("see https://youtu.be/aaa and https://youtu.be/bbb.")
        pdw_effect = ctypes.c_uint(0)
        pt = drop_target.POINTL()
        hr = self.target._drop(0, pdo, 0, pt, ctypes.pointer(pdw_effect))
        self.assertEqual(hr, drop_target.S_OK)
        self.assertEqual(self.captured, [["https://youtu.be/aaa", "https://youtu.be/bbb"]])
        self.assertEqual(pdw_effect.value, drop_target.DROPEFFECT_COPY)

    def test_drop_unknown_clipformat_yields_none(self):
        # Same object, but QueryGetData only advertises CF_UNICODETEXT —
        # _drop calls _query_get_data which uses the real GetData path through
        # vtbl[5]... use an object that advertises nothing supported.
        def query_get_data(this, p_fmtetc):
            return drop_target.DV_E_CLIPFORMAT

        entries = [
            _slot(drop_target._QUERY_INTERFACE, lambda _this, riid, ppv: drop_target.E_NOINTERFACE),
            _slot(drop_target._ADDREF, lambda _this: 2),
            _slot(drop_target._RELEASE, lambda _this: 1),
            _slot(drop_target._GET_DATA, lambda *args: drop_target.E_NOTIMPL),
            _slot(drop_target._GET_DATA, lambda *args: drop_target.E_NOTIMPL),
            _slot(drop_target._QUERY_GET_DATA, query_get_data),
        ]
        pdo, _ = _make_com_object(entries)
        pdw_effect = ctypes.c_uint(99)
        self.target._drop(0, pdo, 0, drop_target.POINTL(), ctypes.pointer(pdw_effect))
        self.assertEqual(self.captured, [])
        self.assertEqual(pdw_effect.value, drop_target.DROPEFFECT_NONE)

    def test_drop_from_dropped_text_file(self):
        # _retrieve_text returns None; _get_hdrop_files points at a temp file.
        self.target._retrieve_text = lambda pdo: None
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write("Grab this link: https://youtu.be/fileurl\n")
            path = fh.name
        try:
            self.target._get_hdrop_files = lambda pdo: [path]
            pdw_effect = ctypes.c_uint(0)
            self.target._drop(
                ctypes.c_void_p(0), 0, 0, drop_target.POINTL(), ctypes.pointer(pdw_effect)
            )
            self.assertEqual(self.captured, [["https://youtu.be/fileurl"]])
            self.assertEqual(pdw_effect.value, drop_target.DROPEFFECT_COPY)
        finally:
            os.unlink(path)

    def test_drop_skips_binary_file(self):
        """BUG-10: Dropping a binary file (.mp4, .zip, etc.) must be skipped without reading."""
        self.target._retrieve_text = lambda pdo: None
        self.target._get_hdrop_files = lambda pdo: ["C:/videos/huge_movie.mp4"]
        pdw_effect = ctypes.c_uint(0)

        with patch("builtins.open") as mock_open:
            self.target._drop(
                ctypes.c_void_p(0), 0, 0, drop_target.POINTL(), ctypes.pointer(pdw_effect)
            )
            mock_open.assert_not_called()
        self.assertEqual(self.captured, [])
        self.assertEqual(pdw_effect.value, drop_target.DROPEFFECT_NONE)

    def test_drop_unknown_extension_skipped(self):
        """BUG-10: Dropping an unapproved extension (.xyz) is skipped."""
        self.target._retrieve_text = lambda pdo: None
        self.target._get_hdrop_files = lambda pdo: ["C:/files/notes.xyz"]
        pdw_effect = ctypes.c_uint(0)

        with patch("builtins.open") as mock_open:
            self.target._drop(
                ctypes.c_void_p(0), 0, 0, drop_target.POINTL(), ctypes.pointer(pdw_effect)
            )
            mock_open.assert_not_called()
        self.assertEqual(pdw_effect.value, drop_target.DROPEFFECT_NONE)

    def test_drop_reads_text_file_with_cap(self):
        """BUG-10: Dropped text files are read with a 1 MB cap to prevent OOM."""
        self.target._retrieve_text = lambda pdo: None
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write("https://youtu.be/capped_link\n" + "A" * (2 * 1024 * 1024))
            path = fh.name
        try:
            self.target._get_hdrop_files = lambda pdo: [path]
            pdw_effect = ctypes.c_uint(0)
            self.target._drop(
                ctypes.c_void_p(0), 0, 0, drop_target.POINTL(), ctypes.pointer(pdw_effect)
            )
            self.assertEqual(self.captured, [["https://youtu.be/capped_link"]])
            self.assertEqual(pdw_effect.value, drop_target.DROPEFFECT_COPY)
        finally:
            os.unlink(path)

    def test_drop_reads_normal_text_file(self):
        """BUG-10: Normal small text files are read and URLs extracted."""
        self.target._retrieve_text = lambda pdo: None
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write("https://youtu.be/small_link\n")
            path = fh.name
        try:
            self.target._get_hdrop_files = lambda pdo: [path]
            pdw_effect = ctypes.c_uint(0)
            self.target._drop(
                ctypes.c_void_p(0), 0, 0, drop_target.POINTL(), ctypes.pointer(pdw_effect)
            )
            self.assertEqual(self.captured, [["https://youtu.be/small_link"]])
            self.assertEqual(pdw_effect.value, drop_target.DROPEFFECT_COPY)
        finally:
            os.unlink(path)

    def test_drop_callback_failure_does_not_crash(self):
        def boom(urls):
            raise RuntimeError("callback died")

        target = drop_target.DropTarget(boom)
        pdo = self._fake_data_object("https://youtu.be/survives")
        pdw_effect = ctypes.c_uint(0)
        hr = target._drop(0, pdo, 0, drop_target.POINTL(), ctypes.pointer(pdw_effect))
        self.assertEqual(hr, drop_target.S_OK)
        self.assertEqual(pdw_effect.value, drop_target.DROPEFFECT_COPY)


class TestDropTargetRegistration(unittest.TestCase):
    """register_drop_target() talks to ole32 and returns a working revoke."""

    def test_register_and_revoke(self):
        fake = _FakeOle32()
        with patch.object(drop_target, "ole32", fake):
            revoke = drop_target.register_drop_target(12345, lambda urls: None)
            self.assertTrue(callable(revoke))
            self.assertEqual(fake.calls[0], ("OleInitialize",))
            self.assertEqual(fake.calls[1][0], "Register")
            self.assertEqual(fake.calls[1][1], 12345)
            revoke()
            self.assertIn(("Revoke", 12345), fake.calls)
            self.assertIn(("OleUninitialize",), fake.calls)

    def test_registration_failure_returns_noop_revoke(self):
        class BrokenOle32:
            def OleInitialize(self, pv):
                return 0

            def OleRegisterDropTarget(self, hwnd, target):
                return 5  # failure

            def OleUninitialize(self):
                pass

        with patch.object(drop_target, "ole32", BrokenOle32()):
            revoke = drop_target.register_drop_target(1, lambda urls: None)
        self.assertTrue(callable(revoke))
        revoke()  # must not raise even though nothing was registered

    def test_non_windows_platform_is_noop(self):
        with patch.object(sys, "platform", "linux"):
            revoke = drop_target.register_drop_target(1, lambda urls: None)
        self.assertTrue(callable(revoke))
        revoke()


if __name__ == "__main__":
    unittest.main()
