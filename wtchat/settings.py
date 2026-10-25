"""设置窗口。改完立刻生效，同时写回 config.json，不用再手改文本。"""

from __future__ import annotations

import copy
import json
import threading
import time
import tkinter as tk

BG = "#161d27"
CARD = "#1b222d"
LINE = "#2a3646"
TXT = "#cfd8e3"
DIM = "#7b8a9d"
ACCENT = "#3d7ebd"
ENTRY_BG = "#0c1016"
FONT = "Microsoft YaHei UI"


class SettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.cfg = copy.deepcopy(app.cfg)
        self.s = getattr(app, "s", 1.0)
        self._loading = True
        self._hk_prev: dict[int, str] = {}

        self.title("设置 · 战争雷霆聊天翻译")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.close)

        # 内容多了可能比屏幕还高（尤其 1080p），套一层可滚动容器兜底
        shell = tk.Frame(self, bg=BG)
        shell.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(shell, bg=BG, highlightthickness=0, bd=0)
        self._vbar = tk.Scrollbar(shell, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vbar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._vbar.pack(side="right", fill="y")
        self._vbar.pack_forget()          # 放得下就不显示滚动条

        body = tk.Frame(self._canvas, bg=BG)
        self._body = body
        self._body_id = self._canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", self._on_body_configure)
        # 宽度要跟着 canvas 走。注意必须绑 canvas 的 Configure，
        # 而且只在 canvas 真的有了宽度之后才设，否则会把 body 压成 1px（整个界面空白）。
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        self._section_look(body)
        self._section_window(body)
        self._section_reply(body)
        self._section_engines(body)
        self._section_llm(body)
        self._section_hotkeys(body)
        self._footer(body)

        self._loading = False
        self._refresh_chain()
        self._fit_to_screen()
        self.grab_set()

    def _on_body_configure(self, _e=None) -> None:
        try:
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        except Exception:
            pass

    def _on_canvas_configure(self, e) -> None:
        """canvas 尺寸变了才去同步 body 宽度，别在宽度还没算出来时动手。"""
        try:
            w = int(e.width) if e is not None else self._canvas.winfo_width()
            if w > 1:
                self._canvas.itemconfigure(self._body_id, width=w)
        except Exception:
            pass

    def _fit_to_screen(self) -> None:
        """按内容尺寸决定窗口大小；比屏幕高了就限高 + 出滚动条。"""
        self.update_idletasks()
        want_w = self._body.winfo_reqwidth()
        want_h = self._body.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        max_h = int(sh * 0.86)
        h = min(want_h, max_h)
        w = want_w
        if want_h > max_h:
            self._vbar.pack(side="right", fill="y")
            self._canvas.configure(yscrollcommand=self._vbar.set)
        self.geometry(f"{w}x{h}+{max(0,(sw-w)//2)}+{max(0,(sh-h)//3)}")
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)
        # 布局算完后再同步一次宽度，否则 Canvas 还没拿到真实宽度时 body 会被压扁
        self.after(30, self._sync_width)

    def _sync_width(self) -> None:
        try:
            w = self._canvas.winfo_width()
            if w > 1:
                self._canvas.itemconfigure(self._body_id, width=w)
                self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        except Exception:
            pass

    def _on_wheel(self, e) -> None:
        try:
            if self._body.winfo_reqheight() > self._canvas.winfo_height():
                self._canvas.yview_scroll(int(-e.delta / 120), "units")
        except Exception:
            pass

    # ---------- 小工具 ----------
    def _px(self, v: float) -> int:
        return int(v * self.s)

    def _head(self, parent, text: str) -> None:
        tk.Label(parent, text=text, bg=BG, fg=DIM, font=(FONT, 8),
                 anchor="w").pack(fill="x", padx=self._px(16), pady=(self._px(12), self._px(2)))

    def _row(self, parent) -> tk.Frame:
        f = tk.Frame(parent, bg=BG)
        f.pack(fill="x", padx=self._px(16), pady=self._px(1))
        return f

    def _slider(self, parent, label: str, lo: float, hi: float, res: float,
                value: float, fmt, on_change) -> tk.Scale:
        row = self._row(parent)
        tk.Label(row, text=label, bg=BG, fg=TXT, font=(FONT, 9), width=8,
                 anchor="w").pack(side="left")
        val = tk.Label(row, text=fmt(value), bg=BG, fg="#8fc6ff", font=(FONT, 9),
                       width=6, anchor="e")
        val.pack(side="right")

        def moved(v):
            val.configure(text=fmt(float(v)))
            if not self._loading:
                on_change(float(v))

        sc = tk.Scale(row, from_=lo, to=hi, resolution=res, orient="horizontal",
                      showvalue=0, bg=BG, fg=TXT, troughcolor="#25303f",
                      highlightthickness=0, bd=0, sliderrelief="flat",
                      activebackground="#4a6b8f", sliderlength=self._px(14),
                      width=self._px(8), command=moved)
        sc.set(value)
        sc.pack(side="left", fill="x", expand=True, padx=self._px(6))
        return sc

    def _check(self, parent, text: str, var: tk.BooleanVar, on_change) -> None:
        cb = tk.Checkbutton(parent, text=text, variable=var, bg=BG, fg=TXT,
                            selectcolor=ENTRY_BG, activebackground=BG,
                            activeforeground="#ffffff", bd=0, highlightthickness=0,
                            font=(FONT, 9), anchor="w",
                            command=lambda: (not self._loading) and on_change())
        cb.pack(fill="x", padx=self._px(16), pady=self._px(1))

    def _entry(self, parent, width: int = 34) -> tk.Entry:
        e = tk.Entry(parent, bg=ENTRY_BG, fg="#e6eef8", insertbackground="#e6eef8",
                     bd=0, highlightthickness=1, highlightbackground=LINE,
                     highlightcolor=ACCENT, font=(FONT, 9), width=width)
        return e

    def _button(self, parent, text: str, cmd, primary: bool = False) -> tk.Label:
        b = tk.Label(parent, text=text, font=(FONT, 9),
                     bg=ACCENT if primary else CARD, fg="#ffffff" if primary else TXT,
                     padx=self._px(14), pady=self._px(5), cursor="hand2")
        b.bind("<Button-1>", lambda _e: cmd())
        hover = "#4a8fd6" if primary else "#232d3b"
        b.bind("<Enter>", lambda _e: b.configure(bg=hover))
        b.bind("<Leave>", lambda _e: b.configure(bg=ACCENT if primary else CARD))
        return b

    # ---------- 各段 ----------
    def _section_look(self, parent) -> None:
        self._head(parent, "外观（背景和文字是两层，各调各的）")
        w = self.cfg["window"]
        self._slider(parent, "背景透明", 0.00, 1.00, 0.01, float(w.get("bg_alpha", 0.86)),
                     lambda v: f"{int(round(v * 100))}%", self._on_alpha)
        self._slider(parent, "文字透明", 0.15, 1.00, 0.01, float(w.get("text_alpha", 1.0)),
                     lambda v: f"{int(round(v * 100))}%", self._on_text_alpha)
        self._slider(parent, "字号", 8, 20, 1, int(self.cfg.get("font_size", 10)),
                     lambda v: f"{int(v)}", self._on_font)

    def _section_window(self, parent) -> None:
        self._head(parent, "行为")
        self._slider(parent, "刷新间隔", 0.4, 3.0, 0.1, float(self.cfg.get("poll_interval", 1.0)),
                     lambda v: f"{v:.1f}s", self._on_interval)
        self.var_orig = tk.BooleanVar(value=bool(self.cfg.get("show_original", True)))
        self._check(parent, "同时显示原文", self.var_orig, self._commit)
        self.var_cn = tk.BooleanVar(value=bool(self.cfg.get("translate_chinese", False)))
        self._check(parent, "中文消息也翻一遍", self.var_cn, self._commit)
        self.var_gloss = tk.BooleanVar(value=bool(self.cfg.get("glossary", True)))
        self._check(parent, "游戏黑话词典（gg / n1 / cap …）", self.var_gloss, self._commit)

    def _section_reply(self, parent) -> None:
        self._head(parent, "回话")
        self.var_send = tk.BooleanVar(value=bool(self.cfg.get("auto_send_reply", False)))
        self._check(parent, "自动发进游戏聊天框（否则只复制）", self.var_send, self._commit)
        row = self._row(parent)
        tk.Label(row, text="按键间隔", bg=BG, fg=DIM, font=(FONT, 8),
                 width=8, anchor="w").pack(side="left")
        self.e_delay = self._entry(row, 8)
        self.e_delay.insert(0, str(self.cfg.get("send_delay", 0.15)))
        self.e_delay.pack(side="left")
        tk.Label(row, text="秒", bg=BG, fg=DIM, font=(FONT, 8)).pack(side="left", padx=self._px(4))

    def _section_engines(self, parent) -> None:
        self._head(parent, "翻译引擎（按顺序兜底，至少留一个）")
        row = self._row(parent)
        names = [("google", "Google"), ("youdao", "有道"), ("mymemory", "MyMemory")]
        cur = self.cfg.get("providers", ["google", "youdao", "mymemory"])
        self.var_engines: dict[str, tk.BooleanVar] = {}
        for key, label in names:
            var = tk.BooleanVar(value=key in cur)
            self.var_engines[key] = var
            tk.Checkbutton(row, text=label, variable=var, bg=BG, fg=TXT,
                           selectcolor=ENTRY_BG, activebackground=BG, bd=0,
                           highlightthickness=0, font=(FONT, 9),
                           command=self._commit).pack(side="left", padx=(0, self._px(10)))

        row2 = self._row(parent)
        self.btn_test = self._button(row2, "逐个测试", self._test_all)
        self.btn_test.pack(side="left")
        tk.Label(row2, text="会依次向每个引擎发同一句话，看谁真的能用", bg=BG, fg=DIM,
                 font=(FONT, 8)).pack(side="left", padx=self._px(8))

        # 每个引擎一行：状态 + 译文
        self.test_rows: dict[str, tuple[tk.Label, tk.Label]] = {}
        for key, label in (("google", "Google"), ("youdao", "有道"),
                           ("mymemory", "MyMemory"), ("llm", "大模型")):
            row3 = tk.Frame(parent, bg=BG)
            row3.pack(fill="x", padx=self._px(16), pady=0)
            tk.Label(row3, text=label, bg=BG, fg=TXT, font=(FONT, 9),
                     width=9, anchor="w").pack(side="left")
            st = tk.Label(row3, text="—", bg=BG, fg=DIM, font=(FONT, 8),
                          width=9, anchor="w")
            st.pack(side="left")
            tx = tk.Label(row3, text="", bg=BG, fg="#65e2c4", font=(FONT, 8),
                          anchor="w", justify="left")
            tx.pack(side="left", fill="x", expand=True)
            self.test_rows[key] = (st, tx)

        self.lbl_chain = tk.Label(parent, text="", bg=BG, fg=DIM, font=(FONT, 8), anchor="w")
        self.lbl_chain.pack(fill="x", padx=self._px(16), pady=(self._px(3), 0))

    def _section_llm(self, parent) -> None:
        self._head(parent, "大模型翻译（可选，填 key 后质量明显更好）")
        llm = self.cfg.get("llm", {})
        self.var_llm = tk.BooleanVar(value=bool(llm.get("enabled", True)))
        self._check(parent, "优先用大模型（填了密钥就自动生效）", self.var_llm, self._commit)

        grid = tk.Frame(parent, bg=BG)
        grid.pack(fill="x", padx=self._px(16), pady=(self._px(2), 0))
        grid.columnconfigure(1, weight=1)

        tk.Label(grid, text="接口", bg=BG, fg=DIM, font=(FONT, 8)).grid(row=0, column=0, sticky="w")
        self.e_base = self._entry(grid)
        self.e_base.insert(0, str(llm.get("base_url", "https://api.deepseek.com/v1")))
        self.e_base.grid(row=0, column=1, sticky="ew", pady=self._px(2), padx=self._px(6))

        tk.Label(grid, text="密钥", bg=BG, fg=DIM, font=(FONT, 8)).grid(row=1, column=0, sticky="w")
        self.e_key = self._entry(grid)
        self.e_key.insert(0, str(llm.get("api_key", "")))
        self.e_key.configure(show="•")
        self.e_key.grid(row=1, column=1, sticky="ew", pady=self._px(2), padx=self._px(6))
        self.show_key = False
        self.btn_eye = tk.Label(grid, text="显示", bg=BG, fg="#6fa8dc", font=(FONT, 8), cursor="hand2")
        self.btn_eye.grid(row=1, column=2, sticky="e")
        self.btn_eye.bind("<Button-1>", lambda _e: self._toggle_key())

        tk.Label(grid, text="模型", bg=BG, fg=DIM, font=(FONT, 8)).grid(row=2, column=0, sticky="w")
        self.e_model = self._entry(grid)
        self.e_model.insert(0, str(llm.get("model", "deepseek-flash")))
        self.e_model.grid(row=2, column=1, sticky="ew", pady=self._px(2), padx=self._px(6))

        tk.Label(parent, text="密钥用 Windows DPAPI 加密后存盘，只有本机当前用户能解开",
                 bg=BG, fg="#5d6b7d", font=(FONT, 8), anchor="w").pack(
            fill="x", padx=self._px(16), pady=(self._px(2), 0))

        for e in (self.e_base, self.e_key, self.e_model):
            e.bind("<FocusOut>", lambda _e: self._commit())
            e.bind("<Return>", lambda _e: self._commit())

    def _section_hotkeys(self, parent) -> None:
        self._head(parent, "快捷键（点一下框，直接按你要的键）")
        row = self._row(parent)
        hk = self.cfg.get("hotkeys", {})
        self.e_hk = {}
        self.lbl_hk_state = {}
        for key, label, default, w in (("toggle", "显示/隐藏", "F8", 9),
                                       ("focus_input", "输入框", "F9", 9),
                                       ("clear", "清屏", "F10", 9)):
            tk.Label(row, text=label, bg=BG, fg=DIM, font=(FONT, 8)).pack(side="left")
            e = self._entry(row, w)
            e.insert(0, str(hk.get(key, default)))
            e.pack(side="left", padx=(self._px(4), self._px(3)))
            st = tk.Label(row, text="", bg=BG, fg=DIM, font=(FONT, 8), width=5, anchor="w")
            st.pack(side="left", padx=(0, self._px(8)))
            self.e_hk[key] = e
            self.lbl_hk_state[key] = st
            self._hk_prev[id(e)] = str(hk.get(key, default))
            # 直接按键录入，省得手打 "Ctrl+Shift+F8" 这种字符串打错
            e.bind("<FocusIn>", lambda ev, ent=e: self._hk_capture_start(ent))
            e.bind("<Key>", lambda ev, ent=e: self._hk_capture_key(ev, ent))

        self.lbl_hk_warn = tk.Label(parent, text="", bg=BG, fg=DIM, font=(FONT, 8), anchor="w")
        self.lbl_hk_warn.pack(fill="x", padx=self._px(16), pady=(self._px(2), 0))

        # ---- 两条必须看见的提醒，用带底色的框框起来，别指望用户会读小字 ----
        notice = tk.Frame(parent, bg="#1a2430")
        notice.pack(fill="x", padx=self._px(16), pady=(self._px(6), self._px(2)))
        for i, (icon, txt) in enumerate((
            ("①", "快捷键改完可能**需要重启本程序**才真正生效。若改了按着没反应，先重启再试。"),
            ("②", "想用 ; 【 】 这类符号键当热键：**必须在英文输入状态下录入**。"
                   "录好之后，游戏里开着中文输入法也能触发（需要打开下面的钩子选项）。"),
        )):
            rowf = tk.Frame(notice, bg="#1a2430")
            rowf.pack(fill="x", padx=self._px(8), pady=(self._px(5) if i == 0 else self._px(1),
                                                       self._px(5) if i == 1 else self._px(1)))
            tk.Label(rowf, text=icon, bg="#1a2430", fg="#ffb454",
                     font=(FONT, 9, "bold")).pack(side="left", anchor="n", padx=(0, self._px(6)))
            lbl = tk.Label(rowf, text=txt.replace("**", ""), bg="#1a2430", fg="#e8c98a",
                           font=(FONT, 8), justify="left", anchor="w",
                           wraplength=self._px(400))
            lbl.pack(side="left", fill="x", expand=True)

        # 实时输入法状态
        self.lbl_ime = tk.Label(
            parent, text="", bg=BG, fg="#ffb454", font=(FONT, 8),
            justify="left", anchor="w", wraplength=self._px(430))
        self.lbl_ime.pack(fill="x", padx=self._px(16), pady=(self._px(4), 0))

        # 输入法穿透开关：中文状态下想用 ; [ ] 这类符号键就得开
        self.var_hook = tk.BooleanVar(value=self.cfg.get("hotkey_mode", "register") == "hook")
        self._check(parent, "允许中文输入法下使用符号键（低级键盘钩子，见下方说明）",
                    self.var_hook, self._on_hook_mode)

        hint = ("· 不开钩子：用 Windows 官方热键 API，安全；但中文输入法激活时，"
                "按 ; [ ] 这类符号键会被输入法吃掉，热键收不到。\n"
                "· 开钩子：能在输入法之前拿到按键，中文状态下符号键也能用；"
                "代价是用了全局键盘钩子，带反作弊的游戏可能不喜欢这种手段，自己权衡。")
        tk.Label(parent, text=hint, bg=BG, fg="#5d6b7d", font=(FONT, 8),
                 justify="left", anchor="w", wraplength=self._px(430)).pack(
            fill="x", padx=self._px(16), pady=(self._px(2), 0))
        self._refresh_ime_hint()
        self.root_after_refresh_states()

    def _ime_is_chinese(self) -> bool:
        """当前输入法是不是中文。

        查的是**前台窗口所属线程**的键盘布局，而不是本进程的——
        本进程的布局永远是自己那个，跟用户切输入法无关。
        """
        try:
            import ctypes
            u = ctypes.windll.user32
            hwnd = u.GetForegroundWindow()
            tid = u.GetWindowThreadProcessId(hwnd, None) if hwnd else 0
            hkl = u.GetKeyboardLayout(tid)
            lang = hkl & 0xFFFF
            return lang in (0x0804, 0x0404)     # 简中 / 繁中
        except Exception:
            return False

    def _refresh_ime_hint(self) -> None:
        """把"现在能不能正常录符号键"直接写在脸上。"""
        try:
            if self._ime_is_chinese():
                self.lbl_ime.configure(
                    text="⚠ 当前是中文输入法：现在按 ; [ ] 录不进去，"
                         "请按 Shift 或 Ctrl+空格 切到英文再录。",
                    fg="#ff8b5e")
            else:
                self.lbl_ime.configure(text="✓ 当前是英文输入状态，可以正常录入符号键",
                                       fg="#3ddc84")
        except Exception:
            pass
        try:
            self.after(1200, self._refresh_ime_hint)     # 跟着输入法状态实时变
        except Exception:
            pass

    def _on_hook_mode(self) -> None:
        self.cfg["hotkey_mode"] = "hook" if self.var_hook.get() else "register"
        self._commit(save=True)
        self.root_after_refresh_states(600)

    def root_after_refresh_states(self, delay: int = 300) -> None:
        try:
            self.after(delay, self._refresh_hotkey_states)
        except Exception:
            pass

    def _refresh_hotkey_states(self) -> None:
        """把每个键的真实注册结果显示出来——这比任何提示都直接。"""
        mgr = getattr(self.app, "hotkeys", None)
        if mgr is None or not hasattr(self, "lbl_hk_state"):
            return
        # 配置里的 key 和热键管理器里的功能名不一致，这里做个映射
        alias = {"toggle": "toggle", "focus_input": "focus", "clear": "clear"}
        for key, lbl in self.lbl_hk_state.items():
            name = alias.get(key, key)
            if name in getattr(mgr, "failed", set()):
                lbl.configure(text="✗ 被占用", fg="#ff8b8b")
            elif name in getattr(mgr, "registered", set()):
                lbl.configure(text="✓ 生效", fg="#3ddc84")
            else:
                lbl.configure(text="……", fg=DIM)

    def _hk_capture_start(self, entry: tk.Entry) -> None:
        self._hk_prev.setdefault(id(entry), entry.get())
        entry.select_range(0, "end")

    # Tk 的 keysym 对符号键给的是英文单词（semicolon / quotedbl / bracketleft ...），
    # 不是单字符，所以必须显式映射，否则按 ; ' [ ] 这些键根本录不进去。
    KEYSYM_MAP = {
        "semicolon": ";", "colon": ":", "quotedbl": "'", "apostrophe": "'",
        "comma": ",", "period": ".", "slash": "/", "backslash": "\\",
        "minus": "-", "equal": "=", "plus": "+", "underscore": "_",
        "bracketleft": "[", "bracketright": "]", "braceleft": "{", "braceright": "}",
        "grave": "`", "asciitilde": "~", "at": "@", "numbersign": "#",
        "dollar": "$", "percent": "%", "asciicircum": "^", "ampersand": "&",
        "asterisk": "*", "parenleft": "(", "parenright": ")", "exclam": "!",
        "question": "?", "less": "<", "greater": ">", "bar": "|",
        "space": "Space", "Return": "Enter", "BackSpace": "Backspace",
        "Prior": "PageUp", "Next": "PageDown", "Delete": "Delete",
        "Insert": "Insert", "Home": "Home", "End": "End",
        "Left": "Left", "Right": "Right", "Up": "Up", "Down": "Down",
        "Tab": "Tab",
    }

    def _hk_capture_key(self, event, entry: tk.Entry) -> str:
        """在输入框里按什么键就记什么，自动拼成 Ctrl+Shift+F8 这种格式。"""
        if event.keysym in ("Control_L", "Control_R", "Shift_L", "Shift_R",
                            "Alt_L", "Alt_R", "Super_L", "Super_R", "Escape"):
            return "break"
        parts = []
        if event.state & 0x0004:
            parts.append("Ctrl")
        if event.state & 0x0001:
            parts.append("Shift")
        if event.state & 0x0008:      # Mod1 = Alt
            parts.append("Alt")

        key = event.keysym
        if key.startswith("F") and key[1:].isdigit():
            main = key
        elif key in self.KEYSYM_MAP:
            main = self.KEYSYM_MAP[key]
        elif len(key) == 1:
            # 中文输入法下 Tk 也可能直接给全角字符，这些不是合法热键
            if key in "；、，。：！？【】（）《》“”‘’·…—～":
                self.lbl_ime.configure(
                    text=f"⚠ 收到的是全角字符「{key}」，说明输入法还是中文。"
                         f"按 Shift 切英文后重新按一次。",
                    fg="#ff8b5e")
                return "break"
            main = key.upper()
        elif key.startswith("KP_") or key.startswith("Num_Lock"):
            main = key.replace("KP_", "Numpad")
        else:
            # 中文输入法会吃掉符号键，Tk 有时只给个空 keysym。
            # 与其静默失败，不如明确告诉用户是输入法的问题。
            if self._ime_is_chinese():
                self.lbl_ime.configure(
                    text="⚠ 这个键被中文输入法吃掉了，录不进去。"
                         "按 Shift 切英文再试，或者改用 F 键。",
                    fg="#ff8b5e")
            return "break"
        text = "+".join(parts + [main])
        entry.delete(0, "end")
        entry.insert(0, text)
        self._hk_prev[id(entry)] = text

        # 只提醒，不拦。用户想用什么键是他自己的事
        warn = ""
        bare = not parts and len(main) == 1
        if bare:
            warn = f"'{main}' 是普通字符键，全局热键会拦住游戏里同键操作"
        elif text.upper() in self.RESERVED_HOTKEYS:
            warn = f"{text} 是{self.RESERVED_HOTKEYS[text.upper()]}，可能会打架"
        self.lbl_hk_warn.configure(text=warn, fg="#e0b64a" if warn else DIM)

        self._commit()
        self.after(400, self._refresh_hotkey_states)   # 等注册结果出来再刷状态
        return "break"                # 拦住，别让按键再触发别的

    # 这几个键游戏自己要用，占了会互相打架
    RESERVED_HOTKEYS = {
        "F12": "WT 默认截图键",
        "PRINT": "系统截图键",
        "ALT+F4": "关闭窗口",
        "CTRL+C": "复制",
        "CTRL+V": "粘贴",
        "CTRL+X": "剪切",
        "CTRL+A": "全选",
        "CTRL+Z": "撤销",
        "CTRL+S": "保存",
    }

    def _check_hotkeys(self) -> list[str]:
        """只做提示，不拦保存。用户想用哪个键是他自己的事。"""
        if not hasattr(self, "lbl_hk_warn"):
            return []
        problems: list[str] = []
        seen: dict[str, str] = {}
        labels = {"toggle": "显示/隐藏", "focus_input": "输入框", "clear": "清屏"}
        for key, entry in self.e_hk.items():
            val = entry.get().strip().upper()
            if not val:
                continue
            if val in seen:
                problems.append(f"{labels[seen[val]]} 和 {labels[key]} 都是 {val}")
            else:
                seen[val] = key
            if val in self.RESERVED_HOTKEYS:
                problems.append(f"{val} 是{self.RESERVED_HOTKEYS[val]}")

        if problems:
            self.lbl_hk_warn.configure(text="；".join(problems), fg="#e0b64a")
        else:
            self.lbl_hk_warn.configure(text="", fg=DIM)
        return problems

    def _footer(self, parent) -> None:
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", padx=self._px(16), pady=self._px(14))
        self._button(bar, "恢复默认", self._reset, primary=False).pack(side="left")
        self._button(bar, "完成", self.close, primary=True).pack(side="right")
        self.lbl_hint = tk.Label(bar, text="改完即时生效，已自动保存", bg=BG, fg=DIM,
                                 font=(FONT, 8))
        self.lbl_hint.pack(side="right", padx=self._px(10))

    # ---------- 交互 ----------
    def _toggle_key(self) -> None:
        self.show_key = not self.show_key
        self.e_key.configure(show="" if self.show_key else "•")
        self.btn_eye.configure(text="隐藏" if self.show_key else "显示")

    def _on_alpha(self, v: float) -> None:
        self.cfg["window"]["bg_alpha"] = round(float(v), 2)
        self.app.apply_alpha(self.cfg["window"]["bg_alpha"])
        self._commit(save=True)

    def _on_text_alpha(self, v: float) -> None:
        self.cfg["window"]["text_alpha"] = round(float(v), 2)
        self.app.apply_text_alpha(self.cfg["window"]["text_alpha"])
        self._commit(save=True)

    def _on_font(self, v: float) -> None:
        size = int(round(v))
        if size == self.cfg.get("font_size"):
            return
        self.cfg["font_size"] = size
        self.app.apply_font(size)
        self._commit(save=True)

    def _on_interval(self, v: float) -> None:
        self.cfg["poll_interval"] = round(float(v), 1)
        self._commit(save=True)

    def _collect(self) -> None:
        self.cfg["show_original"] = bool(self.var_orig.get())
        self.cfg["translate_chinese"] = bool(self.var_cn.get())
        self.cfg["glossary"] = bool(self.var_gloss.get())
        self.cfg["auto_send_reply"] = bool(self.var_send.get())
        try:
            self.cfg["send_delay"] = max(0.05, float(self.e_delay.get()))
        except ValueError:
            pass
        picked = [k for k in ("google", "youdao", "mymemory") if self.var_engines[k].get()]
        self.cfg["providers"] = picked or ["google"]
        self.cfg["llm"] = {
            "enabled": bool(self.var_llm.get()),
            "base_url": self.e_base.get().strip() or "https://api.deepseek.com/v1",
            "api_key": self.e_key.get().strip(),
            "model": self.e_model.get().strip() or "deepseek-flash",
        }
        # 只提示、不拦：用户想用哪个键都由他
        self._check_hotkeys()
        for key, entry in self.e_hk.items():
            val = entry.get().strip()
            if val:
                self.cfg.setdefault("hotkeys", {})[key] = val

    def _commit(self, save: bool = True) -> None:
        if self._loading:
            return
        self._collect()
        self.app.apply_settings(self.cfg, save=save)
        self._refresh_chain()

    def _refresh_chain(self) -> None:
        """告诉用户现在到底在用谁翻——勾了大模型但没 key 是个常见误会。"""
        try:
            desc = self.app.tr.describe()
        except Exception:
            desc = "?"
        if self.var_llm.get() and not self.e_key.get().strip():
            self.lbl_chain.configure(text=f"大模型没填密钥，不生效 · 当前用 {desc}", fg="#ff9b5e")
        elif self.var_llm.get():
            self.lbl_chain.configure(text=f"当前生效：{desc}", fg="#65e2c4")
        else:
            self.lbl_chain.configure(text=f"当前生效：{desc}", fg=DIM)

    def _test_all(self) -> None:
        """挨个测：能通就显示耗时和译文，挂了就显示原因。"""
        if getattr(self, "_testing", False):
            return
        self._testing = True
        self._test_queue: list[tuple[str, object, float, str]] = []
        self.btn_test.configure(text="测试中…", bg="#233043")
        for st, tx in self.test_rows.values():
            st.configure(text="排队", fg=DIM)
            tx.configure(text="")

        sample = "enemy heavy at B4, cover me"

        def work() -> None:
            from . import translate as T
            for key in ("google", "youdao", "mymemory"):
                t0 = time.time()
                try:
                    out = str(T.PROVIDERS[key](sample, "zh-CN")).strip()
                    self._test_queue.append((key, True, time.time() - t0, out))
                except Exception as exc:  # noqa: BLE001
                    self._test_queue.append((key, False, time.time() - t0, str(exc)[:48]))
                time.sleep(0.4)          # 别把免费接口打成限流

            t0 = time.time()
            llm = getattr(self.app.tr, "llm", None)
            if llm is None:
                self._test_queue.append(("llm", None, 0.0, "没填密钥，未启用"))
            else:
                try:
                    out = str(llm(sample, "zh-CN")).strip()
                    self._test_queue.append(("llm", True, time.time() - t0, out))
                except Exception as exc:  # noqa: BLE001
                    self._test_queue.append(("llm", False, time.time() - t0, str(exc)[:48]))
            self._test_queue.append(("__done__", None, 0.0, ""))

        threading.Thread(target=work, daemon=True).start()
        self._poll_test()

    def _poll_test(self) -> None:
        try:
            while self._test_queue:
                key, ok, dt, msg = self._test_queue.pop(0)
                if key == "__done__":
                    self._testing = False
                    self.btn_test.configure(text="逐个测试", bg=CARD)
                    return
                st, tx = self.test_rows.get(key, (None, None))
                if st is None:
                    continue
                if ok is None:
                    st.configure(text="跳过", fg=DIM)
                    tx.configure(text=msg, fg=DIM)
                elif ok:
                    st.configure(text=f"✓ {dt:.1f}s", fg="#3ddc84")
                    tx.configure(text=msg, fg="#65e2c4")
                else:
                    st.configure(text=f"✗ {dt:.1f}s", fg="#ff8b8b")
                    tx.configure(text=msg, fg="#ff9b9b")
        except Exception:
            pass
        if getattr(self, "_testing", False):
            self.after(100, self._poll_test)

    def _reset(self) -> None:
        """恢复默认。注意不能走 close()——close 里会 _commit()，把窗口里的旧值又写回去。"""
        from .config import DEFAULTS
        self.cfg = copy.deepcopy(DEFAULTS)
        self.app.apply_settings(self.cfg, save=True)
        self._loading = True          # 关掉回收，防止 destroy 前后再触发一次 _commit
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        self.after(250, self.app.open_settings)

    def _center(self) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        self.geometry(f"+{x}+{y}")

    def close(self) -> None:
        try:
            self._commit(save=True)
        except Exception:
            pass
        self.grab_release()
        self.destroy()
