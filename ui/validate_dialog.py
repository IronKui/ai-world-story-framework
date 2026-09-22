"""世界观一致性校验测试窗口。

这是一个诊断工具，不是游戏流程的一部分。它把阶段 6 的校验链路
单独暴露出来，方便观察：
  · 什么样的内容会被判为冲突
  · 冲突后重试是否真的能修正
  · 重试到上限时报什么错

游戏里真正的校验会在每次 AI 生成后自动触发（阶段 7 起）。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.api_client import DeepSeekClient
from core.config import AppConfig
from core.prompts import context_block
from core.savegame import HistoryLog, PlayerState
from core.models import WorldState
from core.validator import ValidationExhausted
from core.world import DEFAULT_CONTEXT_BUDGET, WorldDocument
from ui import styles
from ui.workers import ValidateThread

MODE_VALIDATE = "validate"
MODE_GENERATE = "generate"

#: 「生成并校验」模式下的任务指令。只是一段普通的场景描写，
#: 用来暴露校验链路，不代表阶段 9 的正式生成提示词。
DEMO_INSTRUCTION = (
    "基于以上世界观，生成一段约 150 字的场景描写，"
    "描写玩家此刻所处的环境。要具体、有细节，符合该世界的规则与氛围。"
)


class ValidateDialog(QDialog):
    """校验测试。"""

    def __init__(
        self,
        config: AppConfig,
        world: WorldDocument | None,
        player: PlayerState | None = None,
        state: WorldState | None = None,
        history: HistoryLog | None = None,
        tracker=None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("世界观一致性校验")
        self.setModal(True)
        self.resize(980, 720)

        self._config = config
        #: 校验和重试都会消耗 token，必须计入统计
        self._tracker = tracker
        self._world = world
        self._player = player or PlayerState()
        self._state = state or WorldState()
        self._history = history or HistoryLog()
        self._thread: ValidateThread | None = None

        self._build_ui()
        self._refresh_header()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(11)

        self.header = QLabel()
        self.header.setTextFormat(Qt.TextFormat.RichText)
        self.header.setWordWrap(True)
        root.addWidget(self.header)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        # ---------- 上：待校验内容 ----------
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(7)

        input_header = QLabel("待校验内容")
        input_header.setObjectName("PanelHint")
        top_layout.addWidget(input_header)

        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText(
            "直接粘贴一段内容来校验它是否违背世界观；\n"
            "或者点下面的「让 AI 生成并校验」，跑完整的生成 → 校验 → 重试流程。"
        )
        top_layout.addWidget(self.input_edit, 1)

        splitter.addWidget(top)

        # ---------- 下：结果 ----------
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(7)

        self.result_header = QLabel("校验结果")
        self.result_header.setObjectName("PanelHint")
        bottom_layout.addWidget(self.result_header)

        self.result_view = QTextBrowser()
        self.result_view.setObjectName("StoryView")
        bottom_layout.addWidget(self.result_view, 1)

        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([260, 400])

        root.addWidget(splitter, 1)

        # ---------- 按钮 ----------
        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        self.generate_button = QPushButton("让 AI 生成并校验")
        self.generate_button.setObjectName("PrimaryButton")
        self.generate_button.setToolTip(
            "完整走一遍：生成 → 校验 → 冲突则带着原因重试（最多 3 次）"
        )
        self.generate_button.clicked.connect(
            lambda: self._start(MODE_GENERATE)
        )
        buttons.addWidget(self.generate_button)

        self.validate_button = QPushButton("只校验上面的内容")
        self.validate_button.setToolTip("把输入框里的文本交给校验模型判断")
        self.validate_button.clicked.connect(lambda: self._start(MODE_VALIDATE))
        buttons.addWidget(self.validate_button)

        self.clear_button = QPushButton("清空")
        self.clear_button.clicked.connect(self._on_clear)
        buttons.addWidget(self.clear_button)

        buttons.addStretch(1)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._on_cancel)
        buttons.addWidget(self.cancel_button)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)

        root.addLayout(buttons)

    def _refresh_header(self) -> None:
        c = styles.COLORS
        if self._world is None:
            self.header.setText(
                f'<span style="color:{c["warning"]};">当前没有生效的世界观文档。'
                "校验会把「世界观缺失」视为无冲突，建议先导入一份再测。</span>"
            )
            return

        text, compressed = self._world.context_text(DEFAULT_CONTEXT_BUDGET)
        note = "已压缩" if compressed else "完整"
        self.header.setText(
            f'<span style="color:{c["text"]};">当前世界观：'
            f'<b>{self._world.name}</b></span>'
            f'<span style="color:{c["text_faint"]};">'
            f"　原文 {self._world.char_count} 字，送入校验 {len(text)} 字（{note}）"
            f"　重试上限 {self._config.max_validate_retries} 次</span>"
        )

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    def _start(self, mode: str) -> None:
        if self._thread is not None and self._thread.isRunning():
            return

        if not self._config.has_api_key:
            QMessageBox.warning(
                self,
                "尚未配置 API Key",
                "校验需要调用 DeepSeek 接口，请先在「设置 → API 设置」中填入 Key。",
            )
            return

        if mode == MODE_VALIDATE and not self.input_edit.toPlainText().strip():
            QMessageBox.information(self, "内容为空", "请先在上方输入要校验的内容。")
            return

        client = DeepSeekClient(
            api_key=self._config.api_key,
            base_url=self._config.normalized_base_url(),
            model=self._config.model,
            timeout=self._config.timeout,
        )

        self.result_view.clear()
        self._set_busy(True)

        self._thread = ValidateThread(
            client,
            self._world,
            mode=mode,
            content=self.input_edit.toPlainText().strip(),
            instruction=DEMO_INSTRUCTION,
            kind="场景描写",
            max_retries=self._config.max_validate_retries,
            context_text=context_block(
                self._world, self._player, self._state, self._history
            ),
            parent=self,
        )
        self._thread.progress.connect(self._on_progress)
        self._thread.delta.connect(self._on_delta)
        self._thread.attempt_done.connect(self._on_attempt)
        self._thread.done.connect(self._on_done)
        self._thread.failed.connect(self._on_failed)
        self._thread.usage_ready.connect(self._on_usage_ready)
        self._thread.finished.connect(self._on_finished)
        self._thread.start()

    def _on_usage_ready(self, usage: dict, model: str, reason: str) -> None:
        """在**主线程**里记账，避免跨线程改统计对象。"""
        if self._tracker is None:
            return
        self._tracker.record(
            model=model,
            usage=usage,
            reason=reason,
            peak=self._config.forced_peak(),
        )

    def _set_busy(self, busy: bool) -> None:
        self.generate_button.setEnabled(not busy)
        self.validate_button.setEnabled(not busy)
        self.clear_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

        self.generate_button.setText("生成中…" if busy else "让 AI 生成并校验")

    def _on_cancel(self) -> None:
        if self._thread is not None:
            self._thread.cancel()
            self.result_header.setText("校验结果（正在取消…）")

    def _on_finished(self) -> None:
        self._thread = None

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------

    def _on_progress(self, message: str) -> None:
        self.result_header.setText(f"校验结果　—　{message}")
        if "生成" in message:
            # 新一轮生成开始：清空预览。
            # 结果区只放判定结论，流式正文写到输入框里 ——
            # 否则同一段内容会在两个地方各显示一遍。
            self.input_edit.clear()

    def _on_delta(self, piece: str) -> None:
        """生成阶段逐字写入输入框，既能看进度，也方便之后手动改。"""
        cursor = self.input_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(piece)
        self.input_edit.setTextCursor(cursor)

    def _on_attempt(self, attempt) -> None:
        c = styles.COLORS
        verdict = attempt.verdict

        if verdict.error:
            color, mark = c["warning"], "?"
            title = f"校验未完成，已放行　{verdict.error}"
        elif verdict.passed:
            color, mark = c["success"], "✓"
            title = "与世界观一致"
        else:
            color, mark = c["danger"], "✕"
            title = verdict.reason or "与世界观存在冲突"

        lines = "".join(
            f'<div style="color:{c["text_dim"]};font-size:13px;">· {line}</div>'
            for line in verdict.detail_lines()
        )

        # 正文已在输入框里完整展示，这里只留一小段摘录，
        # 用于多次尝试之间做对比
        body = attempt.content
        if len(body) > 160:
            body = body[:160] + "……"

        self.result_view.append(
            f'<div style="margin-top:14px;">'
            f'<div style="color:{color};font-weight:600;font-size:13.5px;">'
            f"{mark} 第 {attempt.index} 次　{title}</div>"
            f'<div style="color:{c["text"]};font-size:13px;line-height:165%;'
            f'margin-top:6px;">{_escape(body)}</div>'
            f"{lines}</div>"
        )
        self._scroll_bottom()

    def _on_done(self, guarded) -> None:
        self._set_busy(False)
        c = styles.COLORS

        if guarded.had_conflicts:
            self.result_header.setText(
                f"校验结果　—　经过 {guarded.retries_used} 次重试后通过"
            )
        else:
            self.result_header.setText("校验结果　—　一次通过")

        tail = (
            f'<div style="margin-top:16px;padding-top:10px;'
            f'border-top:1px solid {c["border_soft"]};'
            f'color:{c["text_faint"]};font-size:12px;">'
            f"共尝试 {len(guarded.attempts)} 次，重试 {guarded.retries_used} 次"
        )
        if guarded.final_verdict.error:
            tail += "　（最后一次校验未有效完成，内容已放行）"
        tail += "</div>"

        self.result_view.append(tail)
        self._scroll_bottom()

        # 把生成结果回填到输入框，方便直接改一改再单独校验
        if guarded.content:
            self.input_edit.setPlainText(guarded.content)

    def _on_failed(self, error) -> None:
        self._set_busy(False)
        c = styles.COLORS

        if isinstance(error, ValidationExhausted):
            self.result_header.setText(
                f"校验结果　—　连续 {error.max_retries} 次失败，已放弃"
            )
            reason_html = "".join(
                f'<div style="color:{c["text_dim"]};font-size:13px;">· {_escape(r)}</div>'
                for r in error.reasons[:8]
            )
            self.result_view.append(
                f'<div style="margin-top:16px;color:{c["danger"]};'
                f'font-size:13.5px;font-weight:600;">✕ 连续 {error.max_retries} 次'
                f"生成均与世界观冲突</div>"
                f'<div style="margin-top:8px;color:{c["text_faint"]};font-size:12.5px;">'
                f"累计冲突点：</div>{reason_html}"
                f'<div style="margin-top:10px;color:{c["text_dim"]};font-size:12.5px;">'
                f"游戏里遇到这种情况会弹窗提示玩家换一种操作方式。</div>"
            )
            self._scroll_bottom()

            QMessageBox.warning(
                self,
                "校验失败",
                f"当前 AI 无法生成符合世界观的内容。\n\n{error}\n\n"
                "（游戏流程中会在此处弹窗提示玩家重新操作）",
            )
        else:
            self.result_header.setText("校验结果　—　调用失败")
            self.result_view.append(
                f'<div style="margin-top:16px;color:{c["danger"]};font-weight:600;">'
                f"✕ {_escape(getattr(error, 'message', str(error)))}</div>"
            )
            self._scroll_bottom()
            QMessageBox.warning(
                self,
                "调用失败",
                getattr(error, "message", str(error)),
            )

    def _scroll_bottom(self) -> None:
        bar = self.result_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ------------------------------------------------------------------
    # 其它
    # ------------------------------------------------------------------

    def _on_clear(self) -> None:
        self.input_edit.clear()
        self.result_view.clear()
        self.result_header.setText("校验结果")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_thread()
        super().closeEvent(event)

    def reject(self) -> None:
        self._stop_thread()
        super().reject()

    def _stop_thread(self) -> None:
        if self._thread is None or not self._thread.isRunning():
            return

        self._thread.cancel()
        for signal in (
            self._thread.progress,
            self._thread.delta,
            self._thread.attempt_done,
            self._thread.done,
            self._thread.failed,
            self._thread.usage_ready,
        ):
            try:
                signal.disconnect()
            except TypeError:
                pass
        self._thread.wait(3000)


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
