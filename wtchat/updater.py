"""检查更新：访问 GitHub Releases 的 latest 接口，对比本地版本号。

纯标准库实现，不引第三方依赖。放在后台线程里跑，绝不阻塞界面。

用法：
    u = Updater(repo="你的名字/仓库名", current="1.0.0")
    u.check(on_result)     # on_result(UpdateInfo | None)

结果里 new 表示确实有新版本；出错时 result.error 有说明。
"""

from __future__ import annotations

import json
import threading
import urllib.request


class UpdateInfo:
    def __init__(self, latest: str, current: str, url: str, body: str = "",
                 new: bool = True, error: str = ""):
        self.latest = latest
        self.current = current
        self.url = url
        self.body = body
        self.new = new
        self.error = error


def _parse_version(v: str) -> tuple[int, ...]:
    """把 'v1.2.3' / '1.2.3' / '1.2' 解析成可比较的元组。"""
    v = (v or "").strip().lstrip("vV")
    parts = []
    for p in v.replace("-", ".").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        if digits:
            parts.append(int(digits))
        else:
            break
    return tuple(parts)


def _newer(latest: str, current: str) -> bool:
    """latest 是否严格大于 current。"""
    a = _parse_version(latest)
    b = _parse_version(current)
    if not a:
        return False
    if not b:
        return True
    return a > b


class Updater:
    def __init__(self, repo: str, current: str, timeout: float = 8.0):
        # repo 形如 "owner/repo"，用户还没上传就填占位，检查会静默失败
        self.repo = (repo or "").strip().strip("/")
        self.current = current
        self.timeout = timeout
        self._thread: threading.Thread | None = None

    @property
    def api_url(self) -> str:
        if not self.repo or "/" not in self.repo:
            return ""
        return f"https://api.github.com/repos/{self.repo}/releases/latest"

    @property
    def releases_url(self) -> str:
        if not self.repo or "/" not in self.repo:
            return ""
        return f"https://github.com/{self.repo}/releases"

    def check(self, on_result) -> None:
        """异步检查，结果通过 on_result(UpdateInfo) 回调。"""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, args=(on_result,), daemon=True)
        self._thread.start()

    def _run(self, on_result) -> None:
        info = self._fetch()
        try:
            on_result(info)
        except Exception:
            pass

    def _fetch(self) -> UpdateInfo:
        url = self.api_url
        if not url:
            return UpdateInfo("", self.current, "", new=False, error="没配置 GitHub 仓库")

        req = urllib.request.Request(url, headers={
            "User-Agent": "wt-chat-translator",
            "Accept": "application/vnd.github+json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        except Exception as exc:  # noqa: BLE001
            # 网络不通 / 仓库不存在 / 被限流都归到 error，不让它打扰用户
            msg = str(exc)
            if "404" in msg:
                msg = "仓库还没发布 Release（或地址不对）"
            elif "403" in msg:
                msg = "被 GitHub 限流了，稍后再试"
            elif "timed out" in msg or "Timeout" in msg:
                msg = "网络超时"
            else:
                msg = "检查失败"
            return UpdateInfo("", self.current, self.releases_url, new=False, error=msg)

        tag = str(data.get("tag_name") or data.get("name") or "")
        html = str(data.get("html_url") or self.releases_url)
        body = str(data.get("body") or "")[:400]
        return UpdateInfo(tag, self.current, html, body, new=_newer(tag, self.current))
