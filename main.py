"""动态世界观文字游戏框架 —— 程序入口。

运行：
    .venv/Scripts/python.exe main.py
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

# 允许从项目根目录直接运行，保证 core / ui 包可导入
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core.config import ConfigStore  # noqa: E402
from core.debuglog import LOG  # noqa: E402
from core.version import APP_NAME, APP_VERSION  # noqa: E402
from ui import styles  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def install_excepthook() -> None:
    """安装全局异常兜底。

    这一步不是锦上添花，而是必需的 —— 实测确认：

      · 不装：PyQt6 在槽函数抛未捕获异常时会直接 abort 进程
        （退出码 127，无堆栈、无输出），用户看到的是窗口凭空消失
      · 装了：异常交到这里处理，进程存活，报错可读、可查日志

    所以任何时候都不要移除这个安装。
    """

    def handler(exc_type, exc_value, exc_tb):
        # Ctrl+C 走默认路径，不要弹窗
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

        LOG.exception(
            "崩溃",
            f"未捕获的异常：{exc_type.__name__}: {exc_value}",
            exc_value,
        )

        # 弹窗本身也可能失败（例如 Qt 已经处于异常状态），不能让它再抛
        try:
            QMessageBox.critical(
                None,
                "程序遇到问题",
                f"出现了未预期的错误，本次操作已中断，但程序仍在运行。\n\n"
                f"{exc_type.__name__}: {exc_value}\n\n"
                f"可以继续使用，也可以到「工具 → 调试日志」查看完整调用栈。",
            )
        except BaseException:  # noqa: BLE001
            pass

    sys.excepthook = handler

    def thread_handler(args: threading.ExceptHookArgs) -> None:
        """非 Qt 线程里的未捕获异常。

        正常路径下各工作线程都会自己兜住异常，
        走到这里说明漏了一处，至少要留下记录。
        """
        if args.exc_type is SystemExit:
            return
        LOG.exception(
            f"线程:{args.thread.name if args.thread else '?'}",
            f"线程内未捕获的异常：{args.exc_type.__name__}: {args.exc_value}",
            args.exc_value,
        )

    threading.excepthook = thread_handler


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    # 主题必须在建窗口之前应用：各面板在构造时就会读取颜色值，
    # 晚一步的话窗口里会混着两套配色
    config = ConfigStore().load()
    styles.set_theme(config.theme)
    styles.set_background(config.background_image)
    app.setStyleSheet(styles.stylesheet())

    install_excepthook()

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
