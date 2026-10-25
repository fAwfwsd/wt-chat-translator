"""翻译引擎：免费接口多路兜底 + 游戏黑话预处理 + 可选大模型。

顺序：llm（配了才用） → google → youdao → mymemory，谁挂就冷却一会儿换下一个。
免费机器翻译对游戏黑话一塌糊涂（arty -> 艺术气息，cap -> 盖帽），所以先做一遍
"黑话展开"，把俚语改成正经英语再丢进去，效果能好一大截。
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_LANG = {
    "zh-CN": {"youdao": "zh-CHS", "mymemory": "zh-CN", "google": "zh-CN", "llm": "简体中文"},
    "en": {"youdao": "en", "mymemory": "en-US", "google": "en", "llm": "English"},
}

# ---------- 整条命中就直接出中文，不浪费请求 ----------
GLOSSARY = {
    "gg": "打得不错", "ggwp": "打得不错，干得好", "ggwp": "打得不错，干得好",
    "ggs": "打得不错", "n1": "漂亮", "ns": "好活", "gj": "干得好", "wp": "干得漂亮",
    "nice": "漂亮", "ty": "谢谢", "thx": "谢谢", "thanks": "谢谢", "np": "不客气",
    "sry": "抱歉", "sorry": "抱歉", "afk": "暂时离开", "brb": "马上回来",
    "gl": "祝好运", "hf": "玩得开心", "glhf": "祝好运，玩得开心",
    "lol": "哈哈", "lmao": "笑死", "wtf": "什么鬼", "omg": "天呐",
    "ez": "太简单了", "ezclap": "轻松拿下",
    "noob": "菜鸟", "nub": "菜鸟", "kid": "小屁孩",
    "kb": "好死", "kys": "（别理他）",
    "ok": "好的", "okay": "好的", "yes": "是", "no": "不", "roger": "收到", "copy": "收到",
    "wait": "等一下", "u2": "你也是", "1v1": "单挑",
    "help": "来帮忙", "needhelp": "需要支援", "onme": "看我这", "enemyhere": "这里有敌人",
    "watchout": "小心", "behindyou": "你背后", "goodjob": "干得好", "wellplayed": "打得好",
}

# ---------- 黑话展开：把俚语改写成正经英语再翻译 ----------
_NATO = {"alpha": "A", "bravo": "B", "charlie": "C", "delta": "D",
         "echo": "E", "foxtrot": "F", "golf": "G", "hotel": "H"}

PRE_RULES: list[tuple[str, str]] = [
    (r"\b(alpha|bravo|charlie|delta|echo|foxtrot|golf|hotel)\b", "nato"),
    (r"\bcap(ping|ped)?\b", "capture the point"),
    (r"\barty\b", "artillery"),
    (r"\bspaa\b", "anti-air vehicle"),
    (r"\bspawn ?camp(ing|er|ed)?\b", "camping at the spawn"),
    (r"\bspawn\b", "spawn point"),
    (r"\btd\b", "tank destroyer"),
    (r"\bop\b", "overpowered"),
    (r"\bbr\b", "battle rating"),
    (r"\bnub\b", "noob"),
    (r"\bpus(h|hing)\b", "advancing"),
    (r"\brush\b", "rush"),
    (r"\bflank(ing|ed)?\b", "going around the side"),
    (r"\bwatch out\b", "be careful"),
]

# ---------- 机器翻译的坑，翻完再擦一遍 ----------
POST_FIX: list[tuple[str, str]] = [
    (r"产卵点?|出生点", "重生点"),
    (r"一罐(坦克|车)?", lambda m: "一辆" + (m.group(1) or "车")),
    (r"盖帽", "占点"),
    (r"艺术气息", "炮火"),
    (r"三角洲", "D点"),
    (r"检查，", "注意，"),
    (r"备份", "支援"),
    (r"露营|野营", "蹲守"),
    (r"艺术", "炮火"),
]

_CTRL = re.compile(r"[\x00-\x1f]")
WORDY = re.compile(r"[A-Za-z\u0400-\u04FF\u4e00-\u9fff]")
LATIN = re.compile(r"^[\x00-\x7F\s]*$")


def has_cjk(text: str) -> bool:
    if not text:
        return False
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk >= max(1, len(text.strip()) * 0.2)


def _http(url: str, data: bytes | None = None, headers: dict | None = None, timeout: float = 8.0) -> bytes:
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ================= 各路引擎 =================

def _google(text: str, target: str) -> str:
    url = ("https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=auto&tl="
           + _LANG[target]["google"] + "&q=" + urllib.parse.quote(text, safe=""))
    data = json.loads(_http(url).decode("utf-8", "replace"))
    if isinstance(data, list) and data and isinstance(data[0], list):
        out = "".join(str(part[0]) for part in data)
    else:
        out = str(data)
    if not out.strip():
        raise RuntimeError("google empty")
    return out


def _youdao(text: str, target: str) -> str:
    url = ("https://aidemo.youdao.com/trans?q=" + urllib.parse.quote(text, safe="")
           + "&from=Auto&to=" + _LANG[target]["youdao"])
    data = json.loads(_http(url).decode("utf-8", "replace"))
    code = str(data.get("errorCode"))
    if code not in ("0", "None"):
        raise RuntimeError(f"youdao {code}")
    out = "".join(str(t) for t in (data.get("translation") or []))
    if not out.strip():
        raise RuntimeError("youdao empty")
    return out


def _mymemory(text: str, target: str) -> str:
    src = "zh-CN" if has_cjk(text) else "en"
    tgt = _LANG[target]["mymemory"]
    if src == tgt:
        raise RuntimeError("same language")
    url = ("https://api.mymemory.translated.net/get?q=" + urllib.parse.quote(text, safe="")
           + f"&langpair={src}|{tgt}")
    data = json.loads(_http(url).decode("utf-8", "replace"))
    out = str((data.get("responseData") or {}).get("translatedText", "")).strip()
    if not out or "MYMEMORY WARNING" in out.upper() or out.lower() == text.lower():
        raise RuntimeError("mymemory empty")
    return out


# 名字 -> 函数，兜底链和设置里的逐个测试都靠这张表
PROVIDERS = {
    "google": _google,
    "youdao": _youdao,
    "mymemory": _mymemory,
}


class _LLM:
    """OpenAI 兼容接口（DeepSeek / 通义 / Kimi / 本地 ollama 都行）。"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 20.0):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key = api_key
        self.model = model
        self.timeout = timeout

    def __call__(self, text: str, target: str) -> str:
        if target.startswith("zh"):
            sys_prompt = ("你是《战争雷霆》游戏内聊天的翻译。把玩家消息翻成简体中文，"
                          "保留游戏术语（占点、重生点、侧翼、截击、俯冲等），"
                          "口语化、简短，不要解释，不要加引号，只输出译文。")
        else:
            sys_prompt = ("Translate War Thunder in-game chat into casual English. "
                          "Keep game terms (cap, spawn, flank). Output the translation only.")
        body = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": text},
            ],
        }
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        out = data["choices"][0]["message"]["content"].strip()
        if not out:
            raise RuntimeError("llm empty")
        return out.splitlines()[0].strip().strip('"')


# ================= 翻译器 =================

class Translator:
    def __init__(self, providers=None, glossary: bool = True, cache_size: int = 800,
                 min_interval: float = 0.25, llm_cfg: dict | None = None):
        self.glossary = glossary
        self.cache_size = max(64, int(cache_size))
        self._cache: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()
        self._min_interval = min_interval
        self._last_call = 0.0
        self._cooldown: dict[str, float] = {}

        self.llm = None
        # 只要填了 key 就默认用大模型；enabled=False 才是显式关掉
        if llm_cfg and llm_cfg.get("api_key") and llm_cfg.get("enabled", True):
            self.llm = _LLM(llm_cfg.get("base_url", "https://api.deepseek.com/v1"),
                            llm_cfg["api_key"], llm_cfg.get("model", "deepseek-chat"))

        order = list(providers or ["google", "youdao", "mymemory"])
        if self.llm and "llm" not in order:
            order.insert(0, "llm")
        self.chain = [p for p in order if p in ("google", "youdao", "mymemory", "llm")]
        self.chain = [p for p in self.chain if p != "llm" or self.llm]
        if not self.chain:
            self.chain = ["google"]

    # ---- 对外 ----
    def translate(self, text: str, target: str = "zh-CN") -> str | None:
        text = (text or "").strip()
        if not text:
            return None
        key = (target, text)
        with self._lock:
            if key in self._cache:
                return self._cache[key]

        out = None
        if self.glossary:
            out = self._glossary_lookup(text)
        if not out:
            out = self._call_chain(self._prepare(text), target)
            out = self._post_fix(out) if out else None

        if out:
            with self._lock:
                if len(self._cache) >= self.cache_size:
                    self._cache.clear()
                self._cache[key] = out
        return out

    def describe(self) -> str:
        """当前实际生效的引擎链，给设置界面显示用。"""
        label = {"google": "Google", "youdao": "有道", "mymemory": "MyMemory"}
        rest = [label.get(n, n) for n in self.chain if n != "llm"]
        if self.llm:
            tail = ("，失败转 " + " → ".join(rest)) if rest else ""
            return f"大模型 {self.llm.model}{tail}"
        return " → ".join(rest) or "未配置"

    def should_translate(self, text: str, target: str, translate_chinese: bool) -> bool:
        if not WORDY.search(text or ""):
            return False
        if target.startswith("zh") and has_cjk(text) and not translate_chinese:
            return False
        return True

    # ---- 预处理 ----
    def _prepare(self, text: str) -> str:
        """黑话展开。中文和俄文原样放过，只动拉丁文本。"""
        if has_cjk(text) or not LATIN.match(text):
            return text
        out = text
        for pattern, repl in PRE_RULES:
            if repl == "nato":
                out = re.sub(pattern, lambda m: _NATO[m.group(1).lower()], out, flags=re.I)
            else:
                out = re.sub(pattern, repl, out, flags=re.I)
        return out

    def _post_fix(self, text: str) -> str:
        for pattern, repl in POST_FIX:
            text = re.sub(pattern, repl, text)   # repl 可以是字符串，也可以是函数
        return text

    def _glossary_lookup(self, text: str) -> str | None:
        norm = re.sub(r"[\s!.,?~\-]+", " ", text.lower()).strip()
        norm = re.sub(r"^[\W_]+|[\W_]+$", "", norm)
        return GLOSSARY.get(norm) or GLOSSARY.get(norm.replace(" ", ""))

    # ---- 调用链 ----
    def _throttle(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._last_call + self._min_interval - now
            self._last_call = max(now, self._last_call + self._min_interval)
        if wait > 0:
            time.sleep(min(wait, 2.0))

    def _call_chain(self, text: str, target: str) -> str | None:
        for name in self.chain:
            if self._cooldown.get(name, 0) > time.monotonic():
                continue
            fn = self.llm if name == "llm" else {"google": _google, "youdao": _youdao,
                                                 "mymemory": _mymemory}.get(name)
            if not fn:
                continue
            # 只有免费接口需要限速（会被 411/429），大模型是自己家的 key，放开跑
            if name != "llm":
                self._throttle()
            try:
                out = _CTRL.sub("", str(fn(text, target))).strip()
                if out:
                    return out
                raise RuntimeError("empty")
            except Exception:
                # 挂了就冷却一会儿，别每条消息都去撞同一面墙。
                # 大模型 key 填错时冷却久一点，不然每条消息都要白等几秒。
                cool = 300 if name == "llm" else 30
                self._cooldown[name] = time.monotonic() + cool
                continue
        return None
