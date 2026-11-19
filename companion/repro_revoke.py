from unittest.mock import patch
from ctypes import wintypes
import drop_target


class FakeOle32:
    def __init__(self):
        self.calls = []

    def OleInitialize(self, pv):
        self.calls.append(("OleInitialize",))
        return 0

    def OleRegisterDropTarget(self, hwnd, target):
        self.calls.append(("Register", int(getattr(hwnd, "value", hwnd)), int(target)))
        return 0

    def RevokeDropTarget(self, hwnd):
        self.calls.append(("Revoke", int(getattr(hwnd, "value", hwnd))))
        return 0

    def OleUninitialize(self):
        self.calls.append(("OleUninitialize",))


fake = FakeOle32()
with patch.object(drop_target, "ole32", fake):
    target = drop_target.DropTarget(lambda u: None)
    target.register(12345)
    print("after register:", target._hwnd, target._registered, target._ole_initialized)
    print("calls:", fake.calls)
    print("cond:", bool(target._hwnd is not None and target._registered))
    try:
        hw = wintypes.HWND(target._hwnd)
        print("HWND ok:", repr(hw), "value:", getattr(hw, "value", "NA"))
    except Exception as e:
        print("HWND raised:", type(e).__name__, e)
    try:
        fake.RevokeDropTarget(wintypes.HWND(target._hwnd))
        print("direct fake Revoke OK")
    except Exception as e:
        print("direct fake Revoke RAISED:", type(e).__name__, e)
    target.revoke()
    print("after revoke:", fake.calls, target._hwnd, target._registered, target._ole_initialized)
