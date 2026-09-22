"""统一管理本地目录与文件位置。

需求硬性要求：API Key 不能和存档、世界观文件放在一起。
所以 config.json 直接放 data/ 根下，存档和世界观各走独立子目录。
"""

from pathlib import Path

#: 项目根目录（core/ 的上一级）
ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "data"

#: API 配置（含 Key），与下面的业务数据物理隔离
CONFIG_FILE = DATA_DIR / "config.json"

SAVES_DIR = DATA_DIR / "saves"
WORLDS_DIR = DATA_DIR / "worlds"
LOGS_DIR = DATA_DIR / "logs"


def ensure_dirs() -> None:
    """按需创建所有运行期目录。幂等。"""
    for directory in (DATA_DIR, SAVES_DIR, WORLDS_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
