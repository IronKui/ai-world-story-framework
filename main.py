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
from core.paths import ensure_dirs  # noqa: E402
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


def run_selftest() -> int:
    """自检：打印环境与路径信息，写完就退出。

    存在的意义有两个：
      · 打包后的程序排查问题 —— 用户截图发过来就能看出数据写到哪了、
        示例文档在不在、Python 和 Qt 是什么版本
      · 让打包产物可被自动验证 —— 窗口程序没有控制台，
        必须有这么一条能拿到输出的路径

    用法：AIWorldStoryFramework.exe --selftest
    """
    from core.paths import (
        ASSETS_DIR,
        CONFIG_FILE,
        DATA_DIR,
        RESOURCE_DIR,
        ROOT,
        ensure_dirs,
        is_portable,
    )

    lines = [
        f"{APP_NAME} v{APP_VERSION}",
        "=" * 56,
        f"运行形态      : {'打包运行' if is_portable() else '源码运行'}",
        f"Python        : {sys.version.split()[0]}",
        f"可执行文件目录 : {ROOT}",
        f"资源目录      : {RESOURCE_DIR}",
        f"数据目录      : {DATA_DIR}",
        f"配置文件      : {CONFIG_FILE}",
        f"示例文档目录  : {ASSETS_DIR}",
        "",
    ]

    # ---- 可写性 ----
    try:
        ensure_dirs()
        probe = DATA_DIR / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        lines.append("数据目录可写  : 是")
    except OSError as exc:
        lines.append(f"数据目录可写  : 否 —— {exc}")

    # ---- 随包资源 ----
    lines.append("")
    lines.append("随包资源：")
    if ASSETS_DIR.is_dir():
        for item in sorted(ASSETS_DIR.iterdir()):
            size = item.stat().st_size / 1024
            lines.append(f"  · {item.name}（{size:.0f} KB）")
    else:
        lines.append(f"  !! 资源目录不存在：{ASSETS_DIR}")

    # ---- Qt ----
    try:
        from PyQt6.QtCore import QT_VERSION_STR

        lines.append("")
        lines.append(f"Qt 版本       : {QT_VERSION_STR}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"Qt 版本       : 读取失败 —— {exc}")

    report = "\n".join(lines)

    # 窗口程序没有控制台，print 到不了任何地方，所以同时写文件
    try:
        ensure_dirs()
        (DATA_DIR / "selftest.txt").write_text(report, encoding="utf-8")
    except OSError:
        pass

    print(report)
    return 0


def main() -> int:
    # 自检要在建 QApplication 之前处理，也不需要图形界面
    if "--selftest" in sys.argv:
        return run_selftest()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    # 首次运行就把数据目录建出来，用户才能通过「工具 → 打开数据目录」找到它。
    # 等到真要写东西时才建的话，用户在此之前根本看不到这个目录。
    ensure_dirs()

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
