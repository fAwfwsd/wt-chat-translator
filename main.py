"""战争雷霆聊天翻译器 —— 入口。

用法：
    pythonw main.py           正常启动（无控制台窗口）
    python  main.py --demo    演示模式，塞几条假消息看界面
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wtchat.config import load  # noqa: E402
from wtchat.ui import WtChatApp  # noqa: E402


def main() -> None:
    cfg = load()
    app = WtChatApp(cfg, demo="--demo" in sys.argv)
    try:
        app.run()
    except KeyboardInterrupt:
        app.quit()


if __name__ == "__main__":
    main()
