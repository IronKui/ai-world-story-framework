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

#: 发给 AI 的 prompt / 返回内容的截断长度。
#: 比普通字段宽得多 —— prompt 里含世界观原文 + 世界状态 + 历史摘要，
#: 截太短就失去了「记录每一次发给 AI 的 prompt」的意义
MAX_LLM_CHARS = 20000

#: 日志文件大小上限，超过就轮转成 debug.log.1（只保留一代）
MAX_FILE_BYTES = 4 * 1024 * 1024


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

    # ---------- AI 收发记录 ----------

    def llm_request(
        self,
        url: str,
        model: str,
        messages: list[dict],
        *,
        stream: bool = False,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> LogEntry | None:
        """记录一次完整的请求。

        刻意只在日志开启时才构造内容 —— prompt 动辄上万字，
        没开日志还每次拼一遍纯属浪费。
        """
        if not self.enabled:
            return None

        body = "\n\n".join(
            f"--- {message.get('role', '?')} ---\n{message.get('content', '')}"
            for message in messages
        )
        header = (
            f"{url}　model={model}　流式={stream}　json={json_mode}"
            f"　max_tokens={max_tokens}"
        )
        return self._write("info", "发送", header, body, limit=MAX_LLM_CHARS)

    def llm_response(
        self,
        model: str,
        content: str,
        usage: dict | None = None,
        *,
        latency_ms: int = 0,
        finish_reason: str = "",
        streamed: bool = False,
    ) -> LogEntry | None:
        """记录一次返回。"""
        if not self.enabled:
            return None

        usage = usage or {}
        header = (
            f"model={model}　{'流式' if streamed else '一次性'}　耗时={latency_ms}ms"
            f"　finish={finish_reason or '-'}"
            f"　tokens={usage.get('prompt_tokens', '?')}"
            f"+{usage.get('completion_tokens', '?')}"
        )
        return self._write("info", "返回", header, content, limit=MAX_LLM_CHARS)

    # ---------- 底层写入 ----------

    def _write(
        self, level: str, tag: str, message: str, detail: str, limit: int | None = None
    ) -> LogEntry:
        limit = limit or MAX_FIELD_CHARS
        entry = LogEntry(
            at=datetime.now().isoformat(timespec="seconds"),
            level=level,
            tag=tag,
            message=_clip(message, limit),
            detail=_clip(detail, limit),
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
            self._rotate_if_needed()
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(entry.to_line() + "\n")
        except OSError:
            # 日志写不进去绝不能影响正常游玩，静默降级
            pass

    def _rotate_if_needed(self) -> None:
        """文件超过上限就轮转。

        不轮转的话，开着日志长时间游玩会把磁盘写满 ——
        每一轮都要记录完整的 prompt 与返回。
        """
        try:
            if self.path.stat().st_size <= MAX_FILE_BYTES:
                return
        except OSError:
            return  # 文件还不存在

        backup = self.path.with_name(self.path.name + ".1")
        try:
            backup.unlink(missing_ok=True)
            self.path.rename(backup)
        except OSError:
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


def _clip(text: str, limit: int = MAX_FIELD_CHARS) -> str:
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"…（已截断，原文 {len(text)} 字）"


#: 全局日志器。各模块直接 import 这个用，避免到处传递引用。
LOG = DebugLog()
