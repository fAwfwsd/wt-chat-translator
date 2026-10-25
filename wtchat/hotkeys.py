"""全局热键。

提供两套实现：

1. ``HotkeyManager``（默认，安全）
   Windows 官方的 RegisterHotKey。优点是干净、不会被反作弊盯上；
   缺点是**中文输入法激活时，符号键会被输入法先吃掉**，热键收不到。

2. ``KeyboardHook``（可选，能穿透输入法）
   低级键盘钩子 WH_KEYBOARD_LL。在输入法之前拿到原始按键，
   所以中文状态下按 `;` `[` `]` 也能触发，并且能把按键吞掉不让它进聊天框。
   代价：全局钩子的行为跟某些外挂常用的手段接近，**带反作弊的游戏可能不喜欢**。

配置里 ``hotkey_mode`` 选 "register"（默认）或 "hook"。
"""

from __future__ import annotations

import ctypes
import queue
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# 低级键盘钩子对句柄和回调指针的类型很敏感，不显式声明会拿到被截断的值
# （GetModuleHandleW 返回负数、SetWindowsHookExW 报错 126 MOD_NOT_FOUND）。
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p,
                                     wintypes.HMODULE, wintypes.DWORD]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
LLKHF_INJECTED = 0x10

VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12

_VK_NAMED = {
    "BACKSPACE": 0x08, "TAB": 0x09, "ENTER": 0x0D, "RETURN": 0x0D,
    "ESC": 0x1B, "ESCAPE": 0x1B,
    "SPACE": 0x20, "INS": 0x2D, "INSERT": 0x2D, "DEL": 0x2E, "DELETE": 0x2E,
    "HOME": 0x24, "END": 0x23,
    "PGUP": 0x21, "PGDN": 0x22, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "PAUSE": 0x13, "SCROLL": 0x91, "PRINT": 0x2C, "PRINTSCREEN": 0x2C,
    "NUMLOCK": 0x90, "CAPSLOCK": 0x14,
}
for _i in range(1, 25):
    _VK_NAMED[f"F{_i}"] = 0x6F + _i

# 符号键的虚拟键码。这些码是**物理按键**的码，跟输入法没关系；
# 用钩子模式时中文状态下按出来的是全角字符，但物理键码不变，所以能匹配。
_VK_SYMBOL = {
    ";": 0xBA, ":": 0xBA, "'": 0xDE, '"': 0xDE,
    ",": 0xBC, "<": 0xBC, ".": 0xBE, ">": 0xBE,
    "/": 0xBF, "?": 0xBF, "\\": 0xDC, "|": 0xDC,
    "[": 0xDB, "{": 0xDB, "]": 0xDD, "}": 0xDD,
    "-": 0xBD, "_": 0xBD, "=": 0xBB, "+": 0xBB,
    "`": 0xC0, "~": 0xC0,
}


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt", wintypes.POINT)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


def parse_hotkey(spec: str) -> tuple[int, int] | None:
    """'F8' / 'Ctrl+Shift+F9' / ';' / 'Ctrl+[' -> (modifiers, vk)"""
    if not spec:
        return None
    mods = 0
    key = None
    for part in str(spec).replace(" ", "").split("+"):
        p = part.upper()
        if p in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif p == "ALT":
            mods |= MOD_ALT
        elif p == "SHIFT":
            mods |= MOD_SHIFT
        elif p in ("WIN", "META"):
            mods |= MOD_WIN
        elif p:
            key = part
    if not key:
        return None

    upper = key.upper()
    if upper in _VK_NAMED:
        return mods | MOD_NOREPEAT, _VK_NAMED[upper]
    if key in _VK_SYMBOL:
        return mods | MOD_NOREPEAT, _VK_SYMBOL[key]
    if upper in _VK_SYMBOL:
        return mods | MOD_NOREPEAT, _VK_SYMBOL[upper]
    if upper.startswith("NUMPAD") and upper[6:].isdigit():
        return mods | MOD_NOREPEAT, 0x60 + int(upper[6:])
    if len(key) == 1 and key.isalnum():
        return mods | MOD_NOREPEAT, ord(upper)
    return None


def _mods_down() -> int:
    mods = 0
    if user32.GetAsyncKeyState(VK_CONTROL) & 0x8000:
        mods |= MOD_CONTROL
    if user32.GetAsyncKeyState(VK_SHIFT) & 0x8000:
        mods |= MOD_SHIFT
    if user32.GetAsyncKeyState(VK_MENU) & 0x8000:
        mods |= MOD_ALT
    return mods


class HotkeyManager:
    """RegisterHotKey 版。安全、干净，但中文输入法会吃掉符号键。"""

    def __init__(self) -> None:
        self.events: queue.Queue[str] = queue.Queue()
        self._bindings: list[tuple[int, int, str]] = []
        self._thread: threading.Thread | None = None
        self._stop = False
        self._ready = threading.Event()
        self.registered: set[str] = set()
        self.failed: set[str] = set()

    def add(self, spec: str, name: str) -> None:
        parsed = parse_hotkey(spec)
        if parsed:
            self._bindings.append((len(self._bindings) + 1, parsed, name))

    def start(self) -> None:
        if not self._bindings or self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=1.0)

    def stop(self, wait: float = 2.0) -> None:
        self._stop = True
        tid = self._thread.ident if self._thread else None
        if tid:
            user32.PostThreadMessageW(tid, 0x0012, 0, 0)
        if self._thread:
            self._thread.join(timeout=wait)
        for hid, _p, _n in self._bindings:
            try:
                user32.UnregisterHotKey(None, hid)
            except Exception:
                pass
        time.sleep(0.08)

    def _run(self) -> None:
        for hid, (mods, vk), name in self._bindings:
            ok = False
            for _ in range(4):
                if user32.RegisterHotKey(None, hid, mods, vk):
                    ok = True
                    break
                time.sleep(0.06)
            if ok:
                self.registered.add(name)
            else:
                self.failed.add(name)
                self.events.put(f"__fail__:{name}")
        self._ready.set()

        msg = MSG()
        while not self._stop and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                for hid, _p, name in self._bindings:
                    if hid == msg.wParam:
                        self.events.put(name)
                        break
        for hid, _p, _n in self._bindings:
            user32.UnregisterHotKey(None, hid)


class KeyboardHook:
    """低级键盘钩子版。能在输入法之前拿到按键，中文状态下符号键照样能用。

    装的是 WH_KEYBOARD_LL，属于全局钩子。带反作弊的游戏可能不喜欢这种手段，
    所以默认不用，配置里 hotkey_mode 改成 "hook" 才启用。
    """

    def __init__(self) -> None:
        self.events: queue.Queue[str] = queue.Queue()
        self._bindings: list[tuple[int, int, str]] = []
        self._thread: threading.Thread | None = None
        self._stop = False
        self._ready = threading.Event()
        self.registered: set[str] = set()
        self.failed: set[str] = set()
        self._hook = None
        self._proc = None
        self._lock = threading.Lock()
        self._swallow_next = False

    def add(self, spec: str, name: str) -> None:
        parsed = parse_hotkey(spec)
        if parsed:
            self._bindings.append((len(self._bindings) + 1, parsed, name))

    def start(self) -> None:
        if not self._bindings or self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=1.5)

    def stop(self, wait: float = 2.0) -> None:
        self._stop = True
        tid = self._thread.ident if self._thread else None
        if tid:
            user32.PostThreadMessageW(tid, 0x0012, 0, 0)
        if self._thread:
            self._thread.join(timeout=wait)

    # ---- 内部 ----
    def _callback(self, n_code, w_param, l_param):
        if n_code == 0 and not self._stop:
            try:
                info = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT))[0]
                if not (info.flags & LLKHF_INJECTED):     # 只认真实按键，别被自己的模拟键触发
                    if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        vk = info.vkCode
                        mods = _mods_down()
                        for _hid, (bmods, bvk), name in self._bindings:
                            need = bmods & (MOD_CONTROL | MOD_SHIFT | MOD_ALT)
                            if vk == bvk and mods == need:
                                self.events.put(name)
                                return 1             # 吞掉，不让它进游戏
            except Exception:
                pass
        return user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

    def _run(self) -> None:
        # 回调必须保持引用，否则被 GC 后钩子会崩
        HOOKPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        self._proc = HOOKPROC(self._callback)
        self._hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, ctypes.cast(self._proc, ctypes.c_void_p),
            kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            for _h, _p, name in self._bindings:
                self.failed.add(name)
                self.events.put(f"__fail__:{name}")
            self._ready.set()
            return

        for _h, _p, name in self._bindings:
            self.registered.add(name)
        self._ready.set()

        msg = MSG()
        while not self._stop and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        try:
            user32.UnhookWindowsHookEx(self._hook)
        except Exception:
            pass
        self._hook = None


def make_manager(mode: str = "register"):
    """按配置选实现。hook 装不上会自动退回 register。"""
    if str(mode).lower() == "hook":
        return KeyboardHook()
    return HotkeyManager()
