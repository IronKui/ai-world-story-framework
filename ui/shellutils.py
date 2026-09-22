"""调用系统文件管理器 / 浏览器。

单独抽出来是因为打包成 exe 后，数据目录在 %LOCALAPPDATA% 下，
普通用户根本找不到 —— 程序必须提供「打开目录」的入口。
"""

from __future__ import annotations

import subprocess
import sys
import webbrowser
from pathlib import Path


def open_folder(path: Path) -> tuple[bool, str]:
    """在系统文件管理器里打开目录。返回 (是否成功, 失败原因)。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 目录建不出来（权限等）也继续尝试打开，可能本来就在
        pass

    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        return False, str(exc)

    return True, ""


def reveal_file(path: Path) -> tuple[bool, str]:
    """打开文件所在目录并选中该文件。"""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            return open_folder(path.parent)
    except OSError as exc:
        return False, str(exc)

    return True, ""


def open_url(url: str) -> bool:
    """用默认浏览器打开链接。"""
    try:
        return webbrowser.open(url)
    except Exception:  # noqa: BLE001 - 打不开链接不是致命错误
        return False
