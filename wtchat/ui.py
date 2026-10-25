"""悬浮窗：读游戏聊天、翻译、显示、回话。"""

from __future__ import annotations

import ctypes
import os
import queue
import threading
import time
import tkinter as tk

from . import api as wtapi
from . import config as wtconfig
from . import translate as wttr
from . import winput
from .hotkeys import HotkeyManager, make_manager
from .settings import SettingsDialog
from .updater import Updater, UpdateInfo
from . import __version__ as VERSION


def _dpi_scale() -> float:
    """Windows 缩放比例。开了 DPI 感知，字体才不糊；窗口尺寸得自己按倍数放大。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            return 1.0
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        return max(1.0, dpi / 96.0)
    except Exception:
        return 1.0


_user32 = ctypes.WinDLL("user32", use_last_error=True)


def _shade(color: str, factor: float) -> str:
    """把 #rrggbb 按系数调亮/调暗，factor=1 原样，0.5 压暗一半。"""
    try:
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
    except Exception:
        return color
    f = max(0.0, min(1.5, factor))
    r, g, b = (min(255, int(c * f)) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def _mix(fg: str, bg: str, alpha: float) -> str:
    """把前景色按 alpha 向背景色混合，用来模拟文字半透明。

    单窗口下窗口 -alpha 管的是整个窗口（含文字），没法把背景和文字拆成两个真透明度。
    所以文字透明度用混色模拟：alpha=1 原色，alpha=0 退化成背景色（等于看不见）。
    观感和真透明几乎一致，而且不破坏任何鼠标交互。
    """
    try:
        fr, fg_, fb = int(fg[1:3], 16), int(fg[3:5], 16), int(fg[5:7], 16)
        br, bg_, bb = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
    except Exception:
        return fg
    a = max(0.0, min(1.0, alpha))
    r = int(fr * a + br * (1 - a))
    g = int(fg_ * a + bg_ * (1 - a))
    b = int(fb * a + bb * (1 - a))
    return f"#{r:02x}{g:02x}{b:02x}"


def _top_hwnd(widget) -> int:
    """widget 对应的真正顶层窗口句柄。Tk 的 winfo_id 给的是子窗口，得往上找一层。"""
    try:
        h = widget.winfo_id()
        parent = _user32.GetParent(h)
        return int(parent or h)
    except Exception:
        return 0


GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
GWLP_HWNDPARENT = -8


def _set_owner(widget, owner) -> None:
    """把 widget 设成 owner 的 **owned window**。

    Windows 对 owned window 有一条硬保证：它永远显示在 owner 之上，且不占任务栏。
    这比"两个 topmost 靠调用顺序"可靠得多——之前背板翻上去盖住文字就是栽在这里。
    """
    hw = _top_hwnd(widget)
    ho = _top_hwnd(owner)
    if not hw or not ho:
        return
    try:
        _user32.SetWindowLongW(hw, GWLP_HWNDPARENT, ho)
    except Exception:
        pass


def _set_noactivate(widget, on: bool) -> None:
    """让窗口点击时不被激活。

    这是关键：悬浮窗一旦被点一下就抢走焦点，游戏里按 Enter 就再没反应了
    （表现就是"开局能开聊天框，点过悬浮窗之后就不行"）。
    平时设成 NOACTIVATE，点它不抢焦点；要打字时按 F9 临时取消。
    """
    h = _top_hwnd(widget)
    if not h:
        return
    try:
        style = _user32.GetWindowLongW(h, GWL_EXSTYLE)
        style = (style | WS_EX_NOACTIVATE) if on else (style & ~WS_EX_NOACTIVATE)
        _user32.SetWindowLongW(h, GWL_EXSTYLE, style)
    except Exception:
        pass


# ---- 配色 ----
BG = "#0f1319"
TITLE_BG = "#1a2230"
TITLE_BG_HI = "#232e40"
LINE = "#2a3646"
TXT = "#cfd8e3"
DIM = "#6f7d8f"
NAME = "#7fb2ff"
NAME_ENEMY = "#ff8b8b"
TRANS = "#65e2c4"
PENDING = "#5c6a7d"
BTN = "#8fa3bd"

FONT = "Microsoft YaHei UI"
TITLE_H = 26

# 抠色：这个颜色在文字层里会被完全抠掉，露出下面的背板。别用在任何可见元素上
KEY = "#010203"

# 标题栏和输入框得有自己的底色，否则点上去会穿透到背板，按钮和光标都点不中
BAR = "#0d1218"
FIELD = "#0b1015"


class WtChatApp:
    def __init__(self, cfg: dict, demo: bool = False):
        self.cfg = cfg
        self.demo = demo
        self.api = wtapi.WtApi(cfg["api_host"], cfg["api_port"], timeout=2.0)
        self.tr = wttr.Translator(cfg["providers"], cfg["glossary"], cfg["cache_size"],
                                  llm_cfg=cfg.get("llm"))
        self._sent: list[str] = []   # 自己刚发出去的内容，用来过滤接口里回显的那条

        self.messages: list[dict] = []
        self.seen: set[int] = set()
        self.last_max_id = 0
        self.api_ok: bool | None = None
        self.online = False

        self.jobs: queue.Queue = queue.Queue()
        self.results: queue.Queue = queue.Queue()
        self.hotkeys = make_manager(cfg.get("hotkey_mode", "register"))
        self._drag = None
        self._resize = None
        self._collapsed = False
        self._status_until = 0.0
        self._update_info: UpdateInfo | None = None
        self.updater = Updater(cfg.get("update_repo", ""), VERSION)
        self._menu = None

        self._build()
        self._start_worker()
        self._register_hotkeys()
        # 拖动只用 Tk 事件（bg 窗口上绑了 <Button-1> 等）。
        # 之前加过一套 GetAsyncKeyState 轮询拖动，和 Tk 事件双轨运行、
        # 状态互相干扰，导致右键菜单关掉后左键全被当成拖动。已移除。

        self.root.after(200, self._poll_hotkeys)
        self.root.after(150, self._drain_results)
        self.root.after(300, self._poll_chat)
        self.root.after(3000, self._keep_on_top)
        if demo:
            self.root.after(600, self._demo_feed)
        # 更新检查：启动后稍等一会儿再查，别跟前面这些抢
        if cfg.get("update_check", True) and cfg.get("update_repo", ""):
            self.root.after(5000, self._check_update)

    # ================= 界面 =================
    def _build(self) -> None:
        cfg = self.cfg
        win = cfg["window"]
        self.s = _dpi_scale()          # 字号按点走，Tk 自己会跟 DPI 缩放；像素尺寸得手动乘
        s = self.s
        frameless = bool(cfg.get("frameless", True))

        # 单窗口。背景透明度和文字透明度是两个独立参数：
        #   - 窗口 -alpha 管背景（整窗透明度）
        #   - 文字透明度用混色模拟（_mix，颜色往背景色靠）
        # 双窗口方案在 Windows 上有个硬伤：owned window 收不到鼠标按钮事件，
        # 标题栏按钮/右键全点不了、只剩拖动。所以回到单窗口，交互最稳。
        self.root = tk.Tk()
        self.root.title("战争雷霆 · 聊天翻译")
        self.root.configure(bg=BG)
        self.root.overrideredirect(frameless)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        try:
            self.root.attributes("-alpha", float(win.get("bg_alpha", win.get("alpha", 0.86))))
        except tk.TclError:
            pass

        geo = (f"{int(win['w'] * s)}x{int(win['h'] * s)}"
               f"+{int(win['x'] * s)}+{int(win['y'] * s)}")
        self.root.geometry(geo)
        self.root.update_idletasks()
        _set_noactivate(self.root, True)

        # 外描边
        outer = tk.Frame(self.root, bg=LINE)
        outer.pack(fill="both", expand=True)
        self.body = tk.Frame(outer, bg=BG)
        self.body.pack(fill="both", expand=True, padx=1, pady=1)

        self._build_title(self.body)

        self.content = tk.Frame(self.body, bg=BG)
        self.content.pack(fill="both", expand=True)

        # 先摆底栏再摆聊天区：pack 是先到先得，反过来聊天区会把输入框挤成 1 像素
        self._build_footer(self.content)
        self._build_text(self.content)
        self._build_grip(self.body)

    def _build_title(self, parent: tk.Frame) -> None:
        s = getattr(self, "s", 1.0)
        # 标题栏给个极淡的底色：完全透明的话点上去会穿透，按钮点不到
        bar = tk.Frame(parent, bg=TITLE_BG)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        bar.configure(height=int(TITLE_H * s))
        self.titlebar = bar

        self.dot = tk.Canvas(bar, width=10, height=10, bg=TITLE_BG, highlightthickness=0)
        self.dot.create_oval(1, 1, 9, 9, fill="#3f4a5a", outline="")
        self.dot.pack(side="left", padx=(int(8 * s), 0), pady=int(8 * s))

        title = tk.Label(bar, text="战争雷霆 · 聊天翻译", bg=TITLE_BG, fg=BTN,
                         font=(FONT, 9), anchor="w")
        title.pack(side="left", padx=6)

        for text, cmd, hover in (("✕", self.quit, "#ff6b6b"),
                                 ("—", self.toggle_collapse, "#ffd166"),
                                 ("⚙", self.open_settings, "#8fc6ff")):
            lbl = tk.Label(bar, text=text, bg=TITLE_BG, fg=DIM, font=(FONT, 9),
                           width=3, cursor="hand2")
            lbl.pack(side="right", padx=1)
            lbl.bind("<ButtonPress-1>", lambda _e, c=cmd: c())
            lbl.bind("<Enter>", lambda _e, w=lbl, h=hover: w.configure(fg=h, bg="#1e2836"))
            lbl.bind("<Leave>", lambda _e, w=lbl: w.configure(fg=DIM, bg=TITLE_BG))

        # 拖动只绑在"标题文字"和"状态点"两个空白区，不绑 bar 本身。
        # 否则 bar 作为按钮(✕ — ⚙)的父容器，点击按钮时 bar 的 <Button-1>
        # 会同时触发 _drag_start，把按钮点击搅乱（表现就是点按钮没反应、只能拖）。
        for w in (title, self.dot):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.bind("<ButtonPress-3>", self._popup_menu)
        # bar 空白处也支持拖动（但不跟按钮冲突，因为按钮是 bar 的子控件、事件先被子控件吃）
        bar.bind("<Button-1>", self._drag_start)
        bar.bind("<B1-Motion>", self._drag_move)
        bar.bind("<ButtonRelease-1>", self._drag_end)
        bar.bind("<ButtonPress-3>", self._popup_menu)

    def _build_text(self, parent: tk.Frame) -> None:
        size = int(self.cfg.get("font_size", 10))
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="both", expand=True, padx=0, pady=0)
        self.text = tk.Text(wrap, bg=BG, fg=TXT, bd=0, highlightthickness=0,
                            wrap="word", padx=9, pady=5, cursor="arrow",
                            insertwidth=0, font=(FONT, size), spacing1=2,
                            selectbackground="#2b3b52")
        self.text.pack(fill="both", expand=True)
        self.text.configure(state="disabled")
        self.text.configure(cursor="arrow")

        self._style_text(size)

        self.text.bind("<MouseWheel>", self._on_wheel)
        self.text.bind("<ButtonPress-3>", self._popup_menu)

    def _style_text(self, size: int) -> None:
        """字号 / 文字不透明度改动时统一重刷。文字半透明用混色模拟。"""
        op = float(self.cfg.get("text_opacity", 1.0))
        op = min(1.0, max(0.05, op))
        orig = _mix(TXT, BG, op)
        trans = _mix(TRANS, BG, op)
        name = _mix(NAME, BG, min(1.0, op * 1.1))
        name_enemy = _mix(NAME_ENEMY, BG, min(1.0, op * 1.1))
        dim = _mix(DIM, BG, op)

        self.text.configure(font=(FONT, size))
        self.text.tag_configure("time", foreground=dim, font=(FONT, max(7, size - 2)))
        self.text.tag_configure("name", foreground=name, font=(FONT, max(7, size - 1), "bold"))
        self.text.tag_configure("name_enemy", foreground=name_enemy, font=(FONT, max(7, size - 1), "bold"))
        self.text.tag_configure("mode", foreground=dim, font=(FONT, max(7, size - 2)))
        self.text.tag_configure("mode_enemy", foreground=_mix("#b06a6a", BG, op), font=(FONT, max(7, size - 2)))
        self.text.tag_configure("orig", foreground=orig, font=(FONT, size), lmargin1=2, lmargin2=2)
        self.text.tag_configure("trans", foreground=trans, font=(FONT, size), lmargin1=2, lmargin2=2, spacing3=5)
        self.text.tag_configure("pending", foreground=_mix(PENDING, BG, op), font=(FONT, size), lmargin1=2, lmargin2=2, spacing3=5)
        self.text.tag_configure("sep", foreground=dim, font=(FONT, max(7, size - 2)), justify="center",
                                spacing1=6, spacing3=6)
        if hasattr(self, "entry"):
            self.entry.configure(font=(FONT, max(8, size)))

    def _build_footer(self, parent: tk.Frame) -> None:
        foot = tk.Frame(parent, bg=TITLE_BG)
        foot.pack(fill="x", side="bottom")

        row = tk.Frame(foot, bg=TITLE_BG)
        row.pack(fill="x", padx=6, pady=(5, 4))

        # 注意：bg 必须用 FIELD 而不是 KEY。抠色窗口里 KEY 像素连鼠标事件都会穿透，
        # 设成 KEY 的话这里根本点不进去（表现为"只能拖窗口，打不了字"）。
        self.entry = tk.Entry(row, bg="#0c1016", fg="#e8eef6", insertbackground="#e8eef6",
                              bd=0, highlightthickness=1, highlightbackground=LINE,
                              highlightcolor="#3d5a80", font=(FONT, 10),
                              cursor="xterm")
        self.entry.pack(side="left", fill="x", expand=True, ipady=3, padx=(0, 5))
        self.entry.bind("<Return>", self._do_reply)
        # 点输入框 = 想打字。窗口默认 NOACTIVATE 点不出焦点，所以这里主动进输入模式
        self.entry.bind("<Button-1>", lambda _e: self.enter_input_mode(from_click=True))
        self._placeholder()

        # 按钮同理：文字底下必须有实色，否则只有笔画能点中
        box = tk.Frame(row, bg="#3f6f9e")
        box.pack(side="right")
        send = tk.Label(box, text="译→英", bg="#1d3550", fg="#8fd0ff", font=(FONT, 9),
                        padx=9, pady=3, cursor="hand2")
        send.pack(padx=1, pady=1)
        send.bind("<Button-1>", self._do_reply)
        send.bind("<Enter>", lambda _e: (box.configure(bg="#5a9bd4"), send.configure(bg="#2a4d70", fg="#ffffff")))
        send.bind("<Leave>", lambda _e: (box.configure(bg="#3f6f9e"), send.configure(bg="#1d3550", fg="#8fd0ff")))

        self.status = tk.Label(foot, text="启动中…", bg=TITLE_BG, fg=DIM, font=(FONT, 8), anchor="w")
        self.status.pack(fill="x", padx=8, pady=(0, 4))

    def _build_grip(self, parent: tk.Frame) -> None:
        s = getattr(self, "s", 1.0)
        size = int(14 * s)
        # 背景给实色，不然这个角也是穿透的，拖不到
        grip = tk.Canvas(parent, bg=TITLE_BG, highlightthickness=0, width=size, height=size,
                         cursor="size_nw_se")
        grip.place(relx=1.0, rely=1.0, anchor="se")
        for i in (3, 6, 9):
            d = int(i * s)
            grip.create_line(size - d, size - 3, size - 3, size - d,
                             fill="#54667d", width=max(1, int(s)))
        grip.bind("<Button-1>", self._resize_start)
        grip.bind("<B1-Motion>", self._resize_move)

    def _placeholder_text(self) -> str:
        key = str((self.cfg.get("hotkeys") or {}).get("focus_input", "F9")).strip() or "F9"
        return f"按 {key} 打字，回车发送"

    def _placeholder(self) -> None:
        self._ph = self._placeholder_text()
        if not self.entry.get():
            self.entry.insert(0, self._ph)
            self.entry.configure(fg=PENDING)
        self.entry.bind("<FocusIn>", self._ph_in)
        self.entry.bind("<FocusOut>", self._ph_out)
        self.entry.bind("<Escape>", self._on_escape)

    def _refresh_placeholder(self) -> None:
        """改完热键后，输入框里那句"按 F9 打字"得跟着换。"""
        new = self._placeholder_text()
        if new == getattr(self, "_ph", None):
            return
        old = getattr(self, "_ph", "")
        cur = self.entry.get()
        self._ph = new
        if not cur or cur == old:
            self.entry.delete(0, "end")
            self.entry.insert(0, new)
            self.entry.configure(fg=PENDING)

    def _ph_in(self, _e=None) -> None:
        if self.entry.get() == self._ph:
            self.entry.delete(0, "end")
            self.entry.configure(fg="#e8eef6")

    def _ph_out(self, _e=None) -> None:
        if not self.entry.get():
            self.entry.insert(0, self._ph)
            self.entry.configure(fg=PENDING)

    # ================= 窗口动作 =================
    def _move_to(self, x: int, y: int) -> None:
        self.root.geometry(f"+{x}+{y}")

    def _size_to(self, w: int, h: int) -> None:
        self.root.geometry(f"{w}x{h}")

    def _drag_start(self, e) -> None:
        # 右键菜单弹着的时候，别让左键误触发拖动
        if getattr(self, "_menu", None) is not None:
            return
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _drag_move(self, e) -> None:
        if getattr(self, "_drag", None):
            self._move_to(e.x_root - self._drag[0], e.y_root - self._drag[1])

    def _drag_end(self, _e=None) -> None:
        self._drag = None

    def _resize_start(self, e) -> None:
        self._resize = (self.root.winfo_width(), self.root.winfo_height(), e.x_root, e.y_root)

    def _resize_move(self, e) -> None:
        if not self._resize:
            return
        w0, h0, x0, y0 = self._resize
        s = getattr(self, "s", 1.0)
        w = max(int(260 * s), w0 + (e.x_root - x0))
        h = max(int(120 * s), h0 + (e.y_root - y0))
        self._size_to(w, h)

    def toggle_collapse(self) -> None:
        th = int(TITLE_H * getattr(self, "s", 1.0)) + 2
        if self._collapsed:
            self.content.pack(fill="both", expand=True)
            self._size_to(self._saved_w, self._saved_h)
            self._collapsed = False
        else:
            self._saved_w = self.root.winfo_width()
            self._saved_h = self.root.winfo_height()
            self.content.pack_forget()
            self._size_to(self._saved_w, th)
            self._collapsed = True

    def toggle_visible(self) -> None:
        if self.root.state() == "withdrawn" or not self.root.winfo_viewable():
            self.root.deiconify()
            self.root.attributes("-topmost", True)
        else:
            self.root.withdraw()

    def open_config(self) -> None:
        try:
            os.startfile(wtconfig.CONFIG_PATH)
        except Exception:
            pass

    # ---------- 更新检查 ----------
    def _check_update(self) -> None:
        """后台查一次 GitHub Releases。"""
        self.updater.check(self._on_update_result)

    def _on_update_result(self, info: UpdateInfo) -> None:
        self._update_info = info
        if info.new and info.latest:
            self._set_status(f"发现新版本 {info.latest}（当前 {info.current}），右键打开下载页", hold=30)
        elif info.error:
            # 静默：网络问题不该刷屏，右键菜单里能看到详情
            pass

    def open_releases(self) -> None:
        """打开 GitHub Releases 页面。"""
        url = self.updater.releases_url
        if url:
            try:
                os.startfile(url)
            except Exception:
                pass

    def check_update_now(self) -> None:
        self._set_status("检查更新中…")
        self._check_update()

    def open_settings(self) -> None:
        win = getattr(self, "_settings", None)
        if win is not None and win.winfo_exists():
            win.lift()
            return
        self._settings = SettingsDialog(self)

    # ---------- 设置生效 ----------
    def apply_alpha(self, value: float) -> None:
        """背景不透明度：调整个窗口的 alpha（背景跟着变）。文字不受此影响。"""
        value = min(1.0, max(0.0, float(value)))
        try:
            self.root.attributes("-alpha", value)
        except tk.TclError:
            pass
        self.cfg["window"]["bg_alpha"] = round(value, 2)

    def apply_text_alpha(self, value: float) -> None:
        """文字不透明度：用混色模拟（文字颜色往背景色靠），独立于窗口 alpha。"""
        value = min(1.0, max(0.05, float(value)))
        self.cfg["text_opacity"] = round(value, 2)
        self._style_text(int(self.cfg.get("font_size", 10)))

    def apply_text_opacity(self, value: float) -> None:
        """兼容老接口：文字透明度用混色实现。"""
        self.apply_text_alpha(value)

    def apply_font(self, size: int) -> None:
        self._style_text(int(size))
        self.cfg["font_size"] = int(size)

    def apply_settings(self, cfg: dict, save: bool = True) -> None:
        """设置窗口改一项就调这里：立刻生效，顺手存盘。"""
        old = self.cfg
        # 必须在 old 被覆盖前比完，否则 old 和 cfg 是同一个对象，永远相等
        need_tr = (cfg.get("providers") != old.get("providers")
                   or cfg.get("glossary") != old.get("glossary")
                   or cfg.get("llm") != old.get("llm")
                   or int(cfg.get("cache_size", 800)) != int(old.get("cache_size", 800)))
        need_hk = (dict(cfg.get("hotkeys") or {}) != dict(old.get("hotkeys") or {})
                   or str(cfg.get("hotkey_mode", "register")) != str(old.get("hotkey_mode", "register")))
        win_new = cfg.get("window", {})
        bg_alpha = win_new.get("bg_alpha", win_new.get("alpha"))
        text_opacity = cfg.get("text_opacity")
        font = cfg.get("font_size")

        old.clear()
        old.update(cfg)

        if need_tr:
            self.tr = wttr.Translator(self.cfg["providers"], self.cfg.get("glossary", True),
                                      self.cfg.get("cache_size", 800), llm_cfg=self.cfg.get("llm"))
            for m in self.messages:                      # 换引擎了就重翻一遍
                if m.get("sep") or m.get("me"):
                    continue
                m["trans"] = None
                self.jobs.put(("msg", m, m["orig"]))
        if bg_alpha is not None:
            self.apply_alpha(bg_alpha)
        if text_opacity is not None:
            self.apply_text_opacity(text_opacity)
        if font:
            self.apply_font(font)
        if need_hk:
            self._reregister_hotkeys()
            self._refresh_placeholder()      # 输入框里的"按 F9 打字"跟着换
        self.render()
        if save:
            self._save_later()

    def _save_later(self) -> None:
        if getattr(self, "_save_job", None):
            try:
                self.root.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.root.after(600, self._save_now)

    def _save_now(self) -> None:
        self._save_job = None
        try:
            wtconfig.save(self.cfg)
        except Exception:
            pass

    def _keep_on_top(self) -> None:
        """周期性重申置顶。

        绝对不能用 Tk 的 attributes('-topmost')：它在 Windows 上走 SetWindowPos(HWND_TOPMOST)，
        有概率把窗口激活，于是每隔几秒就把焦点从游戏抢走——表现就是游戏里按键没反应、
        聊天框打不开。必须用 SWP_NOACTIVATE，只改层级不碰焦点。
        """
        flags = 0x0001 | 0x0002 | 0x0010 | 0x0200  # NOSIZE|NOMOVE|NOACTIVATE|NOOWNERZORDER
        hr = _top_hwnd(self.root)
        try:
            if hr:
                _user32.SetWindowPos(hr, -1, 0, 0, 0, 0, flags)
        except Exception:
            pass
        self.root.after(5000, self._keep_on_top)

    def _on_wheel(self, e) -> None:
        # Ctrl + 滚轮 = 直接调背景透明度，打游戏时不用开设置窗
        if e.state & 0x0004:
            cur = float(self.cfg["window"].get("bg_alpha", 0.86))
            step = 0.05 if e.delta > 0 else -0.05
            self.apply_alpha(round(cur + step, 2))
            self._set_status(f"背景透明度 {int(self.cfg['window']['bg_alpha'] * 100)}%")
            self._save_later()
            return "break"
        self.text.yview_scroll(int(-e.delta / 120), "units")

    def _popup_menu(self, e) -> None:
        # 双窗口 + 抠色穿透，右键事件可能被触发两次，这里做防抖：
        # 300ms 内只弹一次菜单，避免重叠两个菜单出来。
        now = time.monotonic()
        if now - getattr(self, "_menu_last", 0) < 0.3:
            return "break"
        self._menu_last = now

        # 不用 tk.Menu + tk_popup：那套在 NOACTIVATE 窗口上会 grab 焦点，
        # 菜单关闭后 grab 状态残留，导致之后所有鼠标点击被吞、只剩拖动。
        # 改用自绘的无边框小窗口当菜单，不 grab、不改变窗口激活状态。
        self._close_menu()

        items = [
            ("复制最后一条译文", lambda: self._copy_last("trans")),
            ("复制最后一条原文", lambda: self._copy_last("orig")),
            ("---", None),
            (("隐藏原文" if self.cfg.get("show_original", True) else "显示原文"), self._toggle_original),
            ("---", None),
            ("清空记录", self.clear),
            ("打开配置文件", self.open_config),
            ("---", None),
        ]
        if self._update_info and self._update_info.new:
            items.append((f"下载新版本 {self._update_info.latest}", self.open_releases))
        else:
            items.append(("检查更新", self.check_update_now))
        if self.updater.releases_url:
            items.append(("打开 GitHub 页面", self.open_releases))

        s = getattr(self, "s", 1.0)
        menu = tk.Toplevel(self.root)
        menu.overrideredirect(True)
        menu.configure(bg="#1b2230")
        menu.attributes("-topmost", True)
        # 菜单窗口自己不抢焦点（NOACTIVATE），点菜单项不影响游戏
        menu.update_idletasks()
        _set_noactivate(menu, True)

        for text, cmd in items:
            if text == "---":
                tk.Frame(menu, bg="#2a3646", height=max(1, int(1 * s))).pack(fill="x", padx=0, pady=0)
                continue
            lbl = tk.Label(menu, text=text, bg="#1b2230", fg="#cfd8e3",
                           font=(FONT, max(8, int(self.cfg.get("font_size", 10)))),
                           padx=int(14 * s), pady=int(6 * s), anchor="w", cursor="hand2")
            lbl.pack(fill="x")
            lbl.bind("<Button-1>", lambda _e, c=cmd: (self._close_menu(), c()))
            lbl.bind("<Enter>", lambda _e, w=lbl: w.configure(bg="#2b3a52", fg="#ffffff"))
            lbl.bind("<Leave>", lambda _e, w=lbl: w.configure(bg="#1b2230", fg="#cfd8e3"))

        menu.update_idletasks()
        w = menu.winfo_reqwidth()
        h = menu.winfo_reqheight()
        x = min(e.x_root, self.root.winfo_screenwidth() - w - 5)
        y = min(e.y_root, self.root.winfo_screenheight() - h - 5)
        menu.geometry(f"+{x}+{y}")

        self._menu = menu
        # 点菜单外任意处关闭
        self._menu_grab_owner = None
        menu.bind("<FocusOut>", lambda _e: self._close_menu())
        menu.bind("<Button-3>", lambda _e: self._close_menu())
        # 用全局绑定：鼠标在菜单外按下时关闭（通过轮询鼠标位置判断）
        self._menu_close_watch()
        return "break"

    def _menu_close_watch(self) -> None:
        """菜单弹着时，检测鼠标是否移到了菜单外点击，是则关闭。"""
        m = getattr(self, "_menu", None)
        if m is None or not m.winfo_exists():
            return
        try:
            import ctypes as _c
            u = _c.WinDLL("user32")
            lmb = bool(u.GetAsyncKeyState(0x01) & 0x8000)
            if lmb:
                from ctypes import wintypes as _w
                pt = _w.POINT()
                u.GetCursorPos(_c.byref(pt))
                mx, my = m.winfo_x(), m.winfo_y()
                inside = (mx <= pt.x <= mx + m.winfo_width() and
                          my <= pt.y <= my + m.winfo_height())
                if not inside:
                    self._close_menu()
                    return
        except Exception:
            pass
        self.root.after(50, self._menu_close_watch)

    def _close_menu(self) -> None:
        m = getattr(self, "_menu", None)
        if m is not None:
            try:
                m.destroy()
            except Exception:
                pass
            self._menu = None

    def _toggle_original(self) -> None:
        self.cfg["show_original"] = not self.cfg.get("show_original", True)
        wtconfig.save(self.cfg)
        self.render()
        self._set_status("只看译文" if not self.cfg["show_original"] else "原文+译文")

    def _copy_last(self, field: str) -> None:
        for m in reversed(self.messages):
            if m.get(field):
                ok = winput.set_clipboard(m[field])
                self._set_status("已复制" if ok else "复制失败")
                return
        self._set_status("没有可复制的内容")

    # ================= 数据流 =================
    def _poll_chat(self) -> None:
        if self.demo:
            self.api_ok = True
            self.in_battle = True
        else:
            try:
                msgs = self.api.gamechat(0)
                self.api_ok = True
            except Exception:
                msgs = None
                self.api_ok = False
            if msgs is not None:
                self._ingest(msgs)
            # 聊天能发出去的前提是"真在对局里"，机库和结算界面都不行
            self.battle_state = self.api.battle_state() if self.api_ok else "hangar"
            self.in_battle = self.battle_state == "battle"
            # 上一局结束后 id 会从 1 重新数，但 seen 里还留着旧 id，
            # 不主动清掉的话新对局的消息会被当成"看过了"全丢掉——
            # 表现就是"只有第一次进游戏正常，之后都不显示"。
            if self.battle_state == "battle" and getattr(self, "_prev_state", "") != "battle":
                self._reset_match_buffer()
            self._prev_state = self.battle_state
        self._update_status()
        self.root.after(max(200, int(self.cfg["poll_interval"] * 1000)), self._poll_chat)

    def _reset_match_buffer(self) -> None:
        """新对局开始：清掉上一局的消息缓存，否则 id 撞车会导致新消息全被过滤掉。"""
        self.seen.clear()
        self.last_max_id = 0
        self.messages = [{"sep": True, "ts": time.strftime("%H:%M")}]

    def _ingest(self, msgs: list[dict]) -> None:
        if not msgs:
            return
        max_id = max(m["id"] for m in msgs)
        # 同一个对局里 id 万一回退（服务端清理旧消息），也当作新一轮
        if self.last_max_id >= 5 and max_id < self.last_max_id - 1:
            self._reset_match_buffer()
            self.messages = [{"sep": True, "ts": time.strftime("%H:%M")}]
        self.last_max_id = max(self.last_max_id, max_id)

        fresh = 0
        for m in msgs:
            if m["id"] in self.seen:
                continue
            self.seen.add(m["id"])
            text = wtapi.clean((m.get("text") or "")).strip()
            if not text:                     # 空消息直接丢，别占着一行
                continue
            if text in self._sent:           # 自己发的那条，已经在本地显示过了
                continue
            fresh += 1
            item = {
                "id": m["id"],
                "ts": time.strftime("%H:%M"),
                "sender": m["sender"],
                "mode": m["mode"],
                "enemy": m["enemy"],
                "orig": text,
                "trans": False,
            }
            if self.tr.should_translate(text, self.cfg["target_lang"], self.cfg["translate_chinese"]):
                item["trans"] = None
                self.jobs.put(("msg", item, text))
            self.messages.append(item)

        if fresh:
            self._trim()
            self.render()

    def _trim(self) -> None:
        limit = int(self.cfg.get("max_messages", 40))
        if len(self.messages) > limit:
            self.messages = self.messages[-limit:]

    def _start_worker(self) -> None:
        # 开三个，聊天密集时不用排队；翻译按条独立，顺序不重要
        for _ in range(3):
            threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                return
            kind = job[0]
            try:
                if kind == "msg":
                    _, item, text = job
                    out = self.tr.translate(text, self.cfg["target_lang"])
                    self.results.put(("msg", item, out))
                elif kind == "reply":
                    _, text = job
                    self.results.put(("reply", text, self._translate_reply(text)))
            except Exception:
                pass

    def _translate_reply(self, text: str) -> str | None:
        target = self.cfg["reply_lang"]
        if target.startswith("en") and not wttr.has_cjk(text):
            return text  # 本来就是英文，不用翻
        return self.tr.translate(text, target)

    def _drain_results(self) -> None:
        changed = False
        try:
            while True:
                res = self.results.get_nowait()
                if res[0] == "msg":
                    _, item, out = res
                    item["trans"] = out or False
                    changed = True
                elif res[0] == "reply":
                    self._finish_reply(res[1], res[2])
        except queue.Empty:
            pass
        if changed:
            self.render()
        self.root.after(150, self._drain_results)

    def _finish_reply(self, text: str, out: str | None) -> None:
        # 先把焦点还给游戏，否则待会儿模拟按键时窗口还攥着键盘
        self.exit_input_mode()
        final = out or text
        self._sent.append(final)
        self._sent = self._sent[-5:]
        self.messages.append({
            "id": -1,
            "ts": time.strftime("%H:%M"),
            "sender": "我",
            "mode": "",
            "enemy": False,
            "me": True,
            "orig": text,
            "trans": final if final != text else False,
        })
        self._trim()
        self.render()

        if out is None:
            winput.set_clipboard(final)
            self._set_status("翻译失败，已复制原文")
            return

        if not self.cfg.get("auto_send_reply", False):
            winput.set_clipboard(final)
            self._set_status("已复制到剪贴板（回车发到游戏没开）")
            return

        # 不在对局里就发不出聊天，先挡一下，别白按键盘
        if not getattr(self, "in_battle", False):
            winput.set_clipboard(final)
            self._set_status("不在对局中，已复制到剪贴板")
            return

        sent, why = winput.paste_into_chat(
            final, True, self.cfg.get("send_delay", 0.15),
            mode=self.cfg.get("send_mode", "paste"))
        if not sent:
            self._set_status(why)
            return
        self._set_status("已按键，正在确认…")
        self.root.after(1500, lambda: self._confirm_sent(final))

    def _confirm_sent(self, text: str) -> None:
        """发完回读接口：真发出去了才算成功，不猜。"""
        try:
            msgs = self.api.gamechat(0)
        except Exception:
            self._set_status("发出去了但读不到聊天，自己瞄一眼游戏")
            return
        hit = any(text.strip() and text.strip() in m.get("text", "") for m in msgs)
        if hit:
            self._set_status("已发送到游戏 ✓")
            self._sent.append(text)
            self._sent = self._sent[-5:]
        else:
            self._set_status("没发出去：地图/菜单开着的状态下发不了，关掉再试；已复制")

    # ================= 渲染 =================
    def _at_bottom(self) -> bool:
        try:
            return self.text.yview()[1] > 0.995
        except tk.TclError:
            return True

    def render(self) -> None:
        pinned = self._at_bottom()
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        for m in self.messages:
            if m.get("sep"):
                t.insert("end", f"—— 新对局 {m['ts']} ——\n", "sep")
                continue
            t.insert("end", f"{m['ts']}  ", "time")
            t.insert("end", m["sender"], "name_enemy" if m["enemy"] else "name")
            if m.get("mode"):
                t.insert("end", f"  {m['mode']}", "mode_enemy" if m["enemy"] else "mode")
            t.insert("end", "\n")
            if self.cfg.get("show_original", True) or m.get("me"):
                t.insert("end", m["orig"] + "\n", "orig")
            if m.get("trans") is None:
                t.insert("end", "翻译中…\n", "pending")
            elif m.get("trans"):
                t.insert("end", m["trans"] + "\n", "trans")
        t.configure(state="disabled")
        if pinned:
            t.see("end")

    def _set_status(self, msg: str, hold: float = 6.0) -> None:
        self._status_text = msg
        self._status_until = time.time() + hold
        self._update_status()

    def _update_status(self) -> None:
        state = getattr(self, "battle_state", "hangar")
        if self.api_ok is None:
            link = "连接中…"
            color = "#3f4a5a"
        elif self.api_ok and state == "battle":
            link = "对局中"
            color = "#3ddc84"
        elif self.api_ok and state == "ended":
            link = "对局已结束（结算界面发不了聊天）"
            color = "#e0b64a"
        elif self.api_ok:
            link = "机库里（进对局才有聊天）"
            color = "#8b93a1"
        else:
            link = "没连上游戏"
            color = "#ff8b5e"
        self.dot.itemconfigure(1, fill=color)
        if self.api_ok and state == "battle" and not self.messages:
            text = "对局中 · 有人说话才会出现"
        else:
            text = link
        if time.time() < getattr(self, "_status_until", 0):
            text = f"{link} · {getattr(self, '_status_text', '')}"
        self.status.configure(text=text)

    # ================= 热键 / 回话 =================
    def _register_hotkeys(self) -> None:
        hk = self.cfg.get("hotkeys", {})
        self.hotkeys.add(hk.get("toggle", "F8"), "toggle")
        self.hotkeys.add(hk.get("focus_input", "F9"), "focus")
        self.hotkeys.add(hk.get("clear", "F10"), "clear")
        self.hotkeys.start()
        # 启动就自检一遍，别等用户按了才发现某个键根本没注册上
        self.root.after(400, self._check_hotkey_status)

    def _check_hotkey_status(self) -> None:
        fails = []
        try:
            while True:
                ev = self.hotkeys.events.get_nowait()
                if ev.startswith("__fail__:"):
                    fails.append(ev.split(":", 1)[1])
                else:
                    self.hotkeys.events.put(ev)      # 不是失败信息，塞回去给轮询处理
                    break
        except queue.Empty:
            pass
        if fails:
            names = {"toggle": "显示/隐藏", "focus": "输入框", "clear": "清屏"}
            bad = "、".join(f"{names.get(f, f)}" for f in fails)
            alt = self._suggest_hotkeys()
            msg = f"{bad} 的快捷键被别的程序占用"
            if alt:
                msg += f"，建议改成 {alt}"
            self._set_status(msg, hold=30)
        self._hotkey_fails = fails

    def _suggest_hotkeys(self) -> str:
        """找一个真的注册得上的键，给用户提个醒。"""
        import ctypes as _c
        u = _c.WinDLL("user32", use_last_error=True)
        MOD_NOREPEAT = 0x4000
        for vk, label in ((0x75, "F6"), (0x76, "F7"), (0x74, "F5"), (0x73, "F4"),
                          (0x71, "F2"), (0x70, "F1")):
            hid = 8000 + vk
            if u.RegisterHotKey(None, hid, MOD_NOREPEAT, vk):
                u.UnregisterHotKey(None, hid)
                return label
            _c.set_last_error(0)
        return ""

    def _reregister_hotkeys(self) -> None:
        """改键位 / 换热键实现方式后重新注册，不用重启。"""
        try:
            self.hotkeys.stop()
        except Exception:
            pass
        self.hotkeys = make_manager(self.cfg.get("hotkey_mode", "register"))
        self._register_hotkeys()

    def _poll_hotkeys(self) -> None:
        try:
            while True:
                name = self.hotkeys.events.get_nowait()
                if name.startswith("__fail__:"):
                    continue                     # 启动自检已经报过了
                elif name == "toggle":
                    self.toggle_visible()
                elif name == "focus":
                    self.enter_input_mode()
                elif name == "clear":
                    self.clear()
        except queue.Empty:
            pass
        self.root.after(120, self._poll_hotkeys)

    # ---------- 打字模式 ----------
    def enter_input_mode(self, from_click: bool = False) -> None:
        """进入能打字的模式。

        平时窗口是 NOACTIVATE（点它不抢游戏焦点），所以点输入框拿不到键盘，
        只能靠 F9。但用户直觉就是"点一下就能打字"，所以点输入框时也走这里：
        临时取消 NOACTIVATE、把焦点给输入框，离开时再还回去。
        """
        self.root.deiconify()
        _set_noactivate(self.root, False)
        self.root.attributes("-topmost", True)
        self.root.focus_force()
        self.entry.focus_force()
        self._ph_in()
        self._input_mode = True
        if not from_click:
            self._set_status("输入中 · Esc 退出，回车发送")

    def exit_input_mode(self) -> None:
        """退出打字模式：窗口回到不抢焦点状态，游戏该拿回键盘了。"""
        if not getattr(self, "_input_mode", False):
            return
        self._input_mode = False
        self._ph_out()
        _set_noactivate(self.root, True)
        g = winput.find_game_window()
        if g:
            winput.focus_window(g)

    def _on_escape(self, _e=None) -> str:
        self.exit_input_mode()
        return "break"

    def _do_reply(self, _e=None) -> str:
        text = self.entry.get().strip()
        if not text or text == self._ph:
            return "break"
        self.entry.delete(0, "end")
        self._set_status("翻译中…")
        self.jobs.put(("reply", text))
        return "break"

    def clear(self) -> None:
        self.messages = [m for m in self.messages if m.get("sep")]
        self.render()
        self._set_status("已清空")

    # ================= 演示 =================
    def _demo_feed(self) -> None:
        samples = [
            ("Ivan_1977", "enemy heavy at grid E4, watch the right side", False, "友军"),
            ("xX_Sniper_Xx", "gg wp", False, "友军"),
            ("Dmitry", "чек чек, один танк остался", True, "全体"),
            ("CoolGuy", "anyone cover me while I cap bravo?", False, "友军"),
            ("老王", "我这边有两个人", False, "友军"),
        ]
        for i, (sender, text, enemy, mode) in enumerate(samples):
            item = {"id": i + 1, "ts": time.strftime("%H:%M"), "sender": sender,
                    "mode": mode, "enemy": enemy, "orig": text, "trans": None}
            self.messages.append(item)
            if self.tr.should_translate(text, self.cfg["target_lang"], self.cfg["translate_chinese"]):
                self.jobs.put(("msg", item, text))
            else:
                item["trans"] = False
        self.render()

    # ================= 收尾 =================
    def quit(self) -> None:
        try:
            s = getattr(self, "s", 1.0) or 1.0
            win = self.cfg["window"]
            # 存的是逻辑像素，下次启动再乘 DPI 倍数，不然每启动一次就大一圈
            win["x"] = int(self.root.winfo_x() / s)
            win["y"] = int(self.root.winfo_y() / s)
            if not self._collapsed:
                win["w"] = int(self.root.winfo_width() / s)
                win["h"] = int(self.root.winfo_height() / s)
            wtconfig.save(self.cfg)
        except Exception:
            pass
        self.hotkeys.stop()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        self.render()
        self.root.mainloop()
