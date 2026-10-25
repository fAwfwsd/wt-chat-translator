"""往游戏里送字：剪贴板 + 模拟按键（SendInput，走扫描码，游戏更容易认）。

注意：这是模拟输入，理论上任何反作弊都可能有意见，所以默认只在你自己点按钮、
且游戏窗口在前台时才真的按键；平时只复制到剪贴板，你自己 Ctrl+V 最稳。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
INPUT_KEYBOARD = 1

SC_RETURN = 0x1C
SC_CTRL = 0x1D
SC_V = 0x2F
SC_BACK = 0x0E
SC_ESC = 0x01

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT


def _send(scancodes: list[tuple[int, bool]]) -> None:
    """scancodes: [(扫描码, 是否抬起)]"""
    events = (INPUT * len(scancodes))()
    for i, (sc, up) in enumerate(scancodes):
        events[i].type = INPUT_KEYBOARD
        events[i].u.ki.wVk = 0
        events[i].u.ki.wScan = sc
        events[i].u.ki.dwFlags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
        events[i].u.ki.time = 0
        events[i].u.ki.dwExtraInfo = 0
    user32.SendInput(len(scancodes), events, ctypes.sizeof(INPUT))


def tap(scancode: int, hold: float = 0.02) -> None:
    """按一下。按下/抬起分开发，一次塞进同一批有些游戏会漏键。"""
    _send([(scancode, False)])
    time.sleep(hold)
    _send([(scancode, True)])


KEYEVENTF_UNICODE = 0x0004


def type_text(text: str, delay: float = 0.012) -> None:
    """逐字符发 Unicode 输入事件。

    比 Ctrl+V 靠谱：不要求游戏聊天框支持粘贴，直接产生字符输入，
    绝大多数游戏输入框都吃这一套。
    """
    for ch in text:
        code = ord(ch)
        if code > 0xFFFF:            # 辅助平面字符，译文里基本遇不到
            code = 0x3F              # '?'
        events = (INPUT * 2)()
        events[0].type = INPUT_KEYBOARD
        events[0].u.ki.wVk = 0
        events[0].u.ki.wScan = code
        events[0].u.ki.dwFlags = KEYEVENTF_UNICODE
        events[1].type = INPUT_KEYBOARD
        events[1].u.ki.wVk = 0
        events[1].u.ki.wScan = code
        events[1].u.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
        user32.SendInput(2, events, ctypes.sizeof(INPUT))
        time.sleep(delay)


def clear_field(times: int = 24, delay: float = 0.006) -> None:
    """退格清掉聊天框里可能残留的旧字符。"""
    for _ in range(times):
        tap(SC_BACK)
        time.sleep(delay)


def ctrl_v() -> None:
    """Ctrl 单独先按下、留出间隔再按 V，否则游戏不认这个修饰键组合。"""
    _send([(SC_CTRL, False)])
    time.sleep(0.04)
    _send([(SC_V, False)])
    time.sleep(0.04)
    _send([(SC_V, True)])
    time.sleep(0.04)
    _send([(SC_CTRL, True)])


# ---------------- 剪贴板 ----------------

def set_clipboard(text: str) -> bool:
    """写 Unicode 文本到剪贴板。"""
    for _ in range(6):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        user32.CloseClipboard()


# ---------------- 前台窗口检测 ----------------

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)


def foreground_title() -> str:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    # WT 会在标题里掺零宽字符，统一清掉免得比较时踩坑
    return buf.value.translate(_ZERO_WIDTH)


def game_in_front(keyword: str = "War Thunder") -> bool:
    return keyword.lower() in foreground_title().lower()


SW_RESTORE = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _window_process(hwnd: int) -> str:
    """拿窗口所属进程的可执行文件名，小写。"""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1].lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def find_game_window(prefix: str = "War Thunder") -> int:
    """找游戏主窗口。

    坑：游戏标题是动态的（"War Thunder" / "War Thunder - 战斗中" / "- 车库"），
    按精确标题 FindWindow 找不到，只能枚举窗口做前缀匹配，再用进程名 aces.exe 确认。
    """
    exact = user32.FindWindowW(None, prefix)
    if exact:
        return int(exact)

    found: list[tuple[int, bool]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        if title.startswith(prefix):
            found.append((hwnd, _window_process(hwnd) == "aces.exe"))
        return True

    try:
        user32.EnumWindows(_cb, 0)
    except Exception:
        pass
    if not found:
        return 0
    found.sort(key=lambda item: (not item[1], item[0]))   # aces.exe 优先
    return int(found[0][0])


def focus_window(hwnd: int) -> bool:
    """把窗口抢到前台。Windows 有前台锁，直接 SetForegroundWindow 经常没反应，
    所以先把当前前台线程和自己的输入队列临时接一起，再抢。"""
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.05)
    if user32.GetForegroundWindow() == hwnd:
        return True

    my_thread = kernel32.GetCurrentThreadId()
    fg = user32.GetForegroundWindow()
    fg_thread = 0
    if fg:
        pid = wintypes.DWORD()
        fg_thread = user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))

    attached = False
    try:
        if fg_thread and fg_thread != my_thread:
            attached = bool(user32.AttachThreadInput(my_thread, fg_thread, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    except Exception:
        pass
    finally:
        if attached:
            try:
                user32.AttachThreadInput(my_thread, fg_thread, False)
            except Exception:
                pass

    time.sleep(0.12)
    return user32.GetForegroundWindow() == hwnd


# ---------------- 组合动作 ----------------

def paste_into_chat(text: str, send_now: bool = True, delay: float = 0.15,
                    title: str = "War Thunder", mode: str = "paste") -> tuple[bool, str]:
    """把译文丢进游戏聊天框并发送。

    流程：写剪贴板 -> 抢游戏前台 -> 回车（开聊天）-> 塞字 -> 回车（发）。
    mode:
      paste = Ctrl+V（快，但有些游戏聊天框不认粘贴）
      type  = 逐字符 Unicode 输入（慢一点，更接近真人打字）
    抢不到前台就只留剪贴板，不乱按键。
    """
    if not set_clipboard(text):
        return False, "剪贴板写入失败"

    if not send_now:
        return False, "已复制到剪贴板"

    hwnd = find_game_window(title)
    if not hwnd:
        return False, "没找到游戏窗口，已复制到剪贴板"
    if not focus_window(hwnd):
        return False, "切不到游戏前台，已复制到剪贴板"

    time.sleep(0.05)
    tap(SC_RETURN)               # 开聊天框
    time.sleep(delay)
    if mode == "type":
        clear_field()
        time.sleep(0.05)
        type_text(text)
    else:
        ctrl_v()
    time.sleep(delay)
    tap(SC_RETURN)               # 发送
    return True, "已按键发送"
