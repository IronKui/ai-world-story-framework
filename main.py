"""动态世界观文字游戏框架 —— 程序入口。

运行：
    .venv/Scripts/python.exe main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许从项目根目录直接运行，保证 core / ui 包可导入
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui import styles  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("动态世界观文字游戏框架")
    app.setApplicationDisplayName("动态世界观文字游戏框架")
    app.setStyleSheet(styles.stylesheet())

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
