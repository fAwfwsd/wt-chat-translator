"""配置读写。config.json 放在项目根目录，跟代码分开，方便你直接改。"""

from __future__ import annotations

import json
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")

DEFAULTS = {
    # --- 数据来源 ---
    "api_host": "127.0.0.1",
    "api_port": 8111,
    "poll_interval": 1.0,          # 秒，拉取聊天记录的间隔
    # --- 翻译 ---
    "target_lang": "zh-CN",        # 别人说的话翻成什么
    "reply_lang": "en",            # 我要说的话翻成什么
    "providers": ["google", "youdao", "mymemory"],
    # 有 API key 的话挂在这里，质量比免费接口好一大截（留空就是纯免费模式）
    "llm": {
        "enabled": True,
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-flash",
    },
    "translate_chinese": False,    # 对方本来就说中文，要不要也翻（默认不翻）
    "glossary": True,              # 游戏黑话词典（gg / n1 / afk ...）
    "cache_size": 800,
    # --- 界面 ---
    "font_size": 10,
    "max_messages": 40,            # 列表里最多留多少条
    "show_original": True,         # 同时显示原文
    "frameless": True,             # 无边框；输入法候选框不跟随时改成 false
    "window": {
        "x": 40, "y": 420, "w": 430, "h": 320,
        "bg_alpha": 0.86,      # 背板不透明度（只影响背景）
        "text_alpha": 1.0,     # 文字层不透明度（只影响文字）
    },
    "hotkeys": {"toggle": "F8", "focus_input": "F9", "clear": "F10"},
    # 热键实现方式：
    #   register = Windows 官方 API，安全干净，但中文输入法激活时符号键会被吃掉
    #   hook     = 低级键盘钩子，能穿透输入法（中文下按 ; [ ] 也算），
    #              但全局钩子这手段跟外挂接近，带 EAC 的游戏可能不喜欢
    "hotkey_mode": "register",
    # --- 发送回游戏 ---
    "auto_send_reply": True,       # 回车直接发进游戏（关掉就只复制到剪贴板）
    "send_mode": "paste",          # paste = Ctrl+V；type = 逐字符打字（粘贴不灵就换这个）
    "send_delay": 0.15,            # 模拟按键之间的间隔（秒）
    # --- 更新检查 ---
    "update_check": True,          # 启动时自动检查 GitHub 新版本
    "update_repo": "",             # GitHub 仓库，形如 "owner/repo"，留空则不检查
}

# 允许的顶层键，防止脏配置写乱
_KEYS = set(DEFAULTS)


def _merge(base: dict, over: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in (over or {}).items():
        if k not in _KEYS:
            continue
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k].update(v)
        elif isinstance(v, list) and isinstance(out.get(k), list):
            out[k] = v
        elif v is not None:
            out[k] = v
    return out


def load() -> dict:
    if not os.path.exists(CONFIG_PATH):
        save(DEFAULTS)
        return json.loads(json.dumps(DEFAULTS))
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return json.loads(json.dumps(DEFAULTS))
    cfg = _merge(DEFAULTS, raw)
    # 落盘的是密文，读进内存时换成明文给程序用
    try:
        import wtchat.secret as secret
        llm = cfg.get("llm") or {}
        if secret.is_encrypted(str(llm.get("api_key", ""))):
            llm["api_key"] = secret.decrypt(str(llm["api_key"]))
    except Exception:
        pass
    return cfg


def save(cfg: dict) -> None:
    import wtchat.secret as secret

    out = json.loads(json.dumps(cfg))
    llm = out.get("llm") or {}
    key = str(llm.get("api_key", ""))
    if key and not secret.is_encrypted(key):
        llm["api_key"] = secret.encrypt(key)     # 只加密，不碰明文

    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
