"""调试日志。

默认关闭 —— 日志里会包含发给 AI 的 prompt 与模型返回的原文，
可能含有玩家的游玩内容，不该在用户没主动开启时悄悄落盘。

结构上分两层：
  · 内存环形缓冲：供界面即时查看最近若干条，不依赖磁盘
  · 文件：开启后追加写入 data/logs/debug.log

阶段 10 会在此基础上补充网络异常捕获、完整的 prompt/返回记录，
以及日志查看窗口。
"""

from __future__ import annotations

import threading
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.paths import LOGS_DIR, ensure_dirs

#: 内存里保留多少条
MEMORY_LIMIT = 500

#: 单个字段写入文件时的截断长度，避免一次异常把日志撑爆
MAX_FIELD_CHARS = 4000


@dataclass
class LogEntry:
    at: str
    level: str          # info / warn / error
    tag: str            # 来源，例如「校验」「生成」「网络」
    message: str
    detail: str = ""

    def to_line(self) -> str:
        stamp = self.at.replace("T", " ")
        line = f"[{stamp}] [{self.level.upper():<5}] [{self.tag}] {self.message}"
        if self.detail:
            line += f"\n{self.detail}"
        return line


class DebugLog:
    """进程级单例风格的日志器。写入是线程安全的。

    只是把 enabled 关掉就完全不落盘，但内存缓冲仍然记录 ——
    这样用户中途开启日志时，之前发生的冲突也还看得到。
    """

    def __init__(self, path: Path | None = None, enabled: bool = False):
        self.path = path or (LOGS_DIR / "debug.log")
        self.enabled = enabled
        self._entries: deque[LogEntry] = deque(maxlen=MEMORY_LIMIT)
        self._lock = threading.Lock()

    # ---------- 开关 ----------

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self.enabled = bool(enabled)
        if enabled:
            self.info("日志", f"调试日志已开启，写入 {self.path}")

    # ---------- 写入 ----------

    def info(self, tag: str, message: str, detail: str = "") -> LogEntry:
        return self._write("info", tag, message, detail)

    def warn(self, tag: str, message: str, detail: str = "") -> LogEntry:
        return self._write("warn", tag, message, detail)

    def error(self, tag: str, message: str, detail: str = "") -> LogEntry:
        return self._write("error", tag, message, detail)

    def exception(self, tag: str, message: str, exc: BaseException) -> LogEntry:
        """记录异常，带上完整调用栈。"""
        detail = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        return self._write("error", tag, message, detail)

    def _write(self, level: str, tag: str, message: str, detail: str) -> LogEntry:
        entry = LogEntry(
            at=datetime.now().isoformat(timespec="seconds"),
            level=level,
            tag=tag,
            message=_clip(message),
            detail=_clip(detail),
        )

        with self._lock:
            self._entries.append(entry)
            should_flush = self.enabled

        if should_flush:
            self._append_to_file(entry)

        return entry

    def _append_to_file(self, entry: LogEntry) -> None:
        try:
            ensure_dirs()
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(entry.to_line() + "\n")
        except OSError:
            # 日志写不进去绝不能影响正常游玩，静默降级
            pass

    # ---------- 读取 ----------

    def entries(self) -> list[LogEntry]:
        with self._lock:
            return list(self._entries)

    def recent(self, count: int = 100) -> list[LogEntry]:
        with self._lock:
            items = list(self._entries)
        return items[-count:]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def clear_file(self) -> bool:
        try:
            self.path.unlink(missing_ok=True)
            return True
        except OSError:
            return False


def _clip(text: str) -> str:
    text = str(text)
    if len(text) <= MAX_FIELD_CHARS:
        return text
    return text[:MAX_FIELD_CHARS] + f"…（已截断，原文 {len(text)} 字）"


#: 全局日志器。各模块直接 import 这个用，避免到处传递引用。
LOG = DebugLog()
