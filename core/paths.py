"""统一管理本地目录与文件位置。

分两种运行形态，目录完全不同：

  · **源码运行**（开发时）：数据放在项目根的 data/ 下，
    和现在一样，方便调试和查看。
  · **打包运行**（PyInstaller 打出的 exe）：数据必须放到用户目录。
    原因很关键 —— 打包后 `__file__` 指向临时解压目录（_MEIPASS），
    该目录在程序退出时会被清空，配置、存档、日志会全部丢失。

需求硬性要求：API Key 不能和存档、世界观文件放在一起。
所以 config.json 直接放数据根下，存档和世界观各走独立子目录。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _is_frozen() -> bool:
    """是否运行在 PyInstaller 打出的可执行文件里。"""
    return bool(getattr(sys, "frozen", False))


def _app_data_dir() -> Path:
    """Windows 上应用数据的标准位置。

    优先 LOCALAPPDATA（本机数据，不进漫游配置），
    非 Windows 或取不到时退回用户主目录下的隐藏目录。
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "AIWorldStoryFramework"
    return Path.home() / ".ai-world-story-framework"


if _is_frozen():
    #: 打包运行：exe 所在目录（只读资源会解压到 _MEIPASS）
    ROOT = Path(sys.executable).resolve().parent
    #: 随包发布的只读资源（示例文档等）解压在这里
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", ROOT))
    #: 可写数据目录
    DATA_DIR = _app_data_dir()
else:
    #: 源码运行：项目根目录
    ROOT = Path(__file__).resolve().parent.parent
    RESOURCE_DIR = ROOT
    DATA_DIR = ROOT / "data"

#: 随包发布的资源（示例世界观、提示词文档、图标）
ASSETS_DIR = RESOURCE_DIR / "assets"

#: API 配置（含 Key），与下面的业务数据物理隔离
CONFIG_FILE = DATA_DIR / "config.json"

#: 界面主题设置
THEME_FILE = DATA_DIR / "theme.json"

SAVES_DIR = DATA_DIR / "saves"
WORLDS_DIR = DATA_DIR / "worlds"
LOGS_DIR = DATA_DIR / "logs"


def ensure_dirs() -> None:
    """按需创建所有运行期目录。幂等。"""
    for directory in (DATA_DIR, SAVES_DIR, WORLDS_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def is_portable() -> bool:
    """当前是否运行在打包好的程序里。"""
    return _is_frozen()
