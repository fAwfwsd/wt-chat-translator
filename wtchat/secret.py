"""配置里的密钥做本地加密，别让 config.json 里躺着明文 key。

用 Windows DPAPI（CryptProtectData）：密文只能被**当前 Windows 用户**在本机解开，
就算把 config.json 拷到别的机器、或者被别人看到文件内容，也拿不到明文。
不需要自己管密码，系统帮你保管密钥。

有个兜底：万一 DPAPI 不可用（极少数精简系统），退化成明文存，但会标记出来。
"""

from __future__ import annotations

import base64
import ctypes
import json
from ctypes import wintypes

CRYPTPROTECT_UI_FORBIDDEN = 0x01
PREFIX = "dpapi:"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _blob(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _blob_bytes(blob: _DATA_BLOB) -> bytes:
    if not blob.pbData or not blob.cbData:
        return b""
    return ctypes.string_at(blob.pbData, blob.cbData)


def encrypt(plain: str) -> str:
    """明文 -> 'dpapi:base64'。失败就原样返回（调用方会知道没加密）。"""
    if not plain:
        return ""
    try:
        src = _blob(plain.encode("utf-8"))
        out = _DATA_BLOB()
        ok = _crypt32.CryptProtectData(
            ctypes.byref(src), "wtchat", None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            return plain
        try:
            return PREFIX + base64.b64encode(_blob_bytes(out)).decode("ascii")
        finally:
            _kernel32.LocalFree(out.pbData)
    except Exception:
        return plain


def decrypt(stored: str) -> str:
    """'dpapi:base64' -> 明文；不是这个格式就当成明文原样返回。"""
    if not stored or not stored.startswith(PREFIX):
        return stored
    try:
        raw = base64.b64decode(stored[len(PREFIX):])
        src = _blob(raw)
        out = _DATA_BLOB()
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(src), None, None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            return ""
        try:
            return _blob_bytes(out).decode("utf-8", "replace")
        finally:
            _kernel32.LocalFree(out.pbData)
    except Exception:
        return ""


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(PREFIX)
