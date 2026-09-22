#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""控制台输出的编码安全网。

**为什么需要这个文件**：Windows 中文版的控制台代码页是 cp936（GBK）。
脚本输出**被重定向或走管道**时（`python x.py > log.txt`、
`subprocess.run(capture_output=True)`），Python 不再走 WriteConsoleW，
而是按 cp936 编码 —— 这时 print 一个 GBK 里没有的字符（`✓` `✗` `⚠` `✅` …）
会直接抛 `UnicodeEncodeError`，**把整段输出打断**。

实测：`doctor.py` 用 subprocess 捕获 `zcode_patcher.py` 的输出时，
汇总表在 `✓ 用量页去截断补丁` 那一行崩掉，用户看到的是半张表 + traceback，
还会误以为是补丁本身失败。交互式控制台走 WriteConsoleW，所以这个坑**只在管道里露头**。

两条防线：
  * `safe_stdio()` —— 把 stdout/stderr 设成「编不出的字符替换掉」，**永不崩**；
  * `ok_mark()` / `bad_mark()` / `glyph()` —— 先问编码能不能表示，不能就换 ASCII 备选，
    输出依然可读（`√`/`×` 在 cp936 里是有的，所以中文 Windows 上本来就能正常显示）。
"""

from __future__ import annotations

import sys

#: cp936 里没有、但项目里用到的符号 → ASCII 备选
_ASCII_FALLBACK = {
    "✓": "v", "✗": "x", "⚠": "!", "✅": "+", "❌": "x",
    "↻": "~", "↺": "~", "▾": "v", "⧗": "~", "▬": "-",
    "⇔": "<=>", "✦": "*", "◌": "o", "✕": "x",
}


def safe_stdio() -> None:
    """让 stdout/stderr 永远不会因编码问题抛异常。幂等，可重复调用。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def _encodable(ch: str) -> bool:
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        ch.encode(enc)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def glyph(uni: str, ascii_alt: str | None = None) -> str:
    """返回当前 stdout 编得出来的符号；编不出来就用 ASCII 备选。"""
    if _encodable(uni):
        return uni
    if ascii_alt is not None:
        return ascii_alt
    return _ASCII_FALLBACK.get(uni, "?")


def ok_mark() -> str:
    """成功标记（cp936 可编码，中文 Windows 上能正常显示）。"""
    return glyph("√", "v")


def bad_mark() -> str:
    """失败标记。"""
    return glyph("×", "x")


def warn_mark() -> str:
    """警示标记（⚠ 在 cp936 里没有，会自动降级成 `!`）。"""
    return glyph("⚠", "!")
