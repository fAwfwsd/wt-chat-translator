"""读取战争雷霆本地 HTTP 接口里的聊天记录。

游戏跑起来后本机 8111 端口有个只读接口，其中：
    GET /gamechat?lastId=N   ->  id 大于 N 的聊天消息（JSON 数组）
    GET /state              ->  对局状态，用来判断游戏在不在

消息长这样：
    {"id": 5, "msg": "...", "sender": "-HaNTeR-", "enemy": false,
     "mode": "友军", "time": 41}

坑：
  * 响应没有 charset，必须按 UTF-8 手动解码；
  * msg 里夹着 <color=#AARRGGBB> 这类富文本标签，还有一堆 U+200B 零宽空格；
  * mode 是跟着游戏客户端语言走的（"友军"/"队伍"/"Team"），所以判断阵营只看 enemy。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

_TAG_RE = re.compile(r"<[^>]{0,40}>")
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean(text: str) -> str:
    """去掉富文本标签、零宽字符和控制字符。"""
    if not text:
        return ""
    text = _TAG_RE.sub("", text)
    text = text.translate(_ZERO_WIDTH)
    text = _CTRL_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


class WtApi:
    def __init__(self, host: str = "127.0.0.1", port: int = 8111, timeout: float = 2.5):
        self.base = f"http://{host}:{port}"
        self.timeout = timeout
        self._up = None  # 上次探测结果，界面上用来点亮状态灯

    # ---------- 底层 ----------
    def _get(self, path: str) -> bytes:
        req = urllib.request.Request(self.base + path, headers={"User-Agent": "wtchat/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    @property
    def online(self) -> bool | None:
        return self._up

    def probe(self) -> bool:
        """游戏开着、且已经进入对局时才算在线。"""
        try:
            data = json.loads(self._get("/state").decode("utf-8", "replace") or "{}")
            self._up = bool(data.get("valid")) or bool(data)
        except Exception:
            self._up = False
        return bool(self._up)

    # ---------- 聊天 ----------
    def window_state(self) -> str:
        """从游戏窗口标题猜状态，作为接口数据的交叉验证。

        WT 的标题会跟着状态变：
          "War Thunder - 战斗中"          -> 对局中
          "War Thunder - 等待游戏开始"     -> 排队/读图，接口这时候是**上一局的残留**
          "War Thunder"                   -> 机库
        这个信号最直接，但它依赖窗口标题，所以只当辅助判据用。
        """
        try:
            import ctypes
            from ctypes import wintypes

            _u = ctypes.WinDLL("user32", use_last_error=True)
            found = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def _cb(hwnd, _l):
                if not _u.IsWindowVisible(hwnd):
                    return True
                n = _u.GetWindowTextLengthW(hwnd)
                if n <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(n + 1)
                _u.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value.startswith("War Thunder"):
                    found.append(buf.value)
                return True

            _u.EnumWindows(_cb, 0)
            title = found[0] if found else ""
        except Exception:
            return "unknown"

        # WT 连窗口标题里都掺零宽字符（"战\u200b斗\u200b中"），
        # 不清掉的话 "战斗中" 永远匹配不上，整个对局判断全废。
        title = title.translate(_ZERO_WIDTH)

        if "等待游戏开始" in title:
            return "queue"
        if "战斗中" in title:
            return "battle"
        return "hangar"

    def battle_state(self) -> str:
        """当前处于哪种状态：battle / ended / hangar。

        最大的坑：**这些接口在机库/排队时不会清空，会一直返回上一局的残留数据**。
        实测在"等待游戏开始"界面问接口，它照样给你上局的飞机姿态、任务目标、
        击杀记录，看起来跟真在对局里一模一样。

        所以光看"有没有数据"不行，得同时看：
          1. 窗口标题（最直接，排除排队/机库）
          2. 数据是不是还在动（真对局里速度高度一直在变）
        """
        win = self.window_state()
        if win == "queue":
            return "hangar"      # 等待开局，接口是上一局的残留
        if win == "hangar":
            return "hangar"

        try:
            mis = json.loads(self._get("/mission.json").decode("utf-8", "replace") or "{}")
        except Exception:
            return "hangar"

        status = str(mis.get("status", "")).lower()
        if status in ("success", "failed", "finished"):
            return "ended"
        if status != "running":
            return "hangar"

        # 取一个能反映"这局还活着"的指纹。
        # 注意：/state 里飞机和坦克的字段完全不同——坦克没有 TAS/H，只有 speed/rpm 之类，
        # 而且历史坦克模式连 state.valid 都是 false。所以指纹要把两套字段都兜住，
        # 否则坦克局里指纹恒定不变，会被误判成"冻结的残留数据"。
        try:
            st = json.loads(self._get("/state").decode("utf-8", "replace") or "{}")
        except Exception:
            st = {}
        try:
            ind = json.loads(self._get("/indicators").decode("utf-8", "replace") or "{}")
        except Exception:
            ind = {}
        try:
            hud = json.loads(self._get("/hudmsg?lastEvt=0&lastDmg=0").decode("utf-8", "replace") or "{}")
        except Exception:
            hud = {}

        def _num(d, *keys):
            for k in keys:
                v = d.get(k)
                if isinstance(v, (int, float)):
                    return round(float(v), 1)
            return 0.0

        dmg = hud.get("damage") or []
        last_dmg = dmg[-1].get("id") if dmg else 0
        fingerprint = (
            # 飞机：速度 / 高度 / 油门
            _num(st, "TAS, km/h", "IAS, km/h"),
            _num(st, "H, m"),
            _num(st, "throttle 1, %", "throttle 1"),
            # 坦克：速度 / 转速 / 档位 / 炮塔角度
            _num(ind, "speed"),
            _num(ind, "rpm"),
            _num(ind, "gear"),
            _num(ind, "gear_neutral"),
            # 战斗记录
            len(dmg),
            last_dmg,
            len(hud.get("events") or []),
        )

        now = time.monotonic()
        prev = getattr(self, "_fp", None)
        prev_t = getattr(self, "_fp_t", 0.0)
        last = getattr(self, "_last_state", "hangar")

        if prev is None or fingerprint != prev:
            self._fp, self._fp_t = fingerprint, now
            state = "battle" if st.get("valid") or mis.get("objectives") or dmg else "hangar"
            self._last_state = state
            return state

        # 指纹没变。但标题明确写着"战斗中"时不能翻脸——坦克停着不动、
        # 或者开镜蹲点时本来就没有数据变化，这时候它是真对局。
        if win == "battle":
            self._last_state = "battle"
            return "battle"

        # 标题不是明确的"战斗中"，数据又连续 3 秒不动 -> 当作残留
        if now - prev_t >= 3.0:
            self._last_state = "hangar"
            return "hangar"
        return last

    def in_battle(self) -> bool:
        """能不能正常收发聊天。"""
        return self.battle_state() == "battle"

    def gamechat(self, last_id: int = 0) -> list[dict]:
        """拉取 id > last_id 的消息，按 id 升序返回。"""
        raw = self._get(f"/gamechat?lastId={int(last_id)}")
        try:
            data = json.loads(raw.decode("utf-8", "replace") or "[]")
        except json.JSONDecodeError:
            # 偶尔会读到半截，忽略这次
            return []
        if not isinstance(data, list):
            return []

        out = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                mid = int(item.get("id", 0))
            except (TypeError, ValueError):
                continue
            msg = clean(str(item.get("msg", "")))
            if not msg:
                continue
            out.append({
                "id": mid,
                "text": msg,
                "sender": clean(str(item.get("sender", ""))).strip() or "?",
                "enemy": bool(item.get("enemy")),
                "mode": clean(str(item.get("mode", ""))),
                "t": item.get("time"),
            })
        out.sort(key=lambda m: m["id"])
        return out
