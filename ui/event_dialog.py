"""AI 事件生成窗口（阶段 8）。

这是一个诊断窗口，用来观察支撑「禁止随机触发世界毁灭」的那套机制：
  · 毁灭进度如何限制本回合允许写到的程度
  · AI 提议的推进会被框架复核（没理由的直接丢弃）
  · 进度不足时，越界内容会被校验拦下并重试

阶段 9 的主循环会直接调用同一套生成逻辑，不弹这个窗口。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
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
from core.doom import DOOM_MAX, DoomState, ceiling_for, level_name
from core.events import apply_state_changes
from core.models import WorldState
from core.savegame import HistoryLog, PlayerState
from core.validator import ValidationExhausted
from core.world import WorldDocument
from ui import styles
from ui.workers import EventThread


class EventDialog(QDialog):
    """事件生成诊断窗口。"""

    def __init__(
        self,
        config: AppConfig,
        world: WorldDocument | None,
        *,
        player: PlayerState | None = None,
        state: WorldState | None = None,
        inventory: list | None = None,
        history: HistoryLog | None = None,
        tracker=None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("事件生成")
        self.setModal(True)
        self.resize(1000, 760)

        self._config = config
        self._world = world
        self._player = player or PlayerState()
        self._state = state or WorldState()
        self._inventory = list(inventory or [])
        self._history = history or HistoryLog()
        self._tracker = tracker
        self._thread: EventThread | None = None

        #: 生成后已应用的状态变更说明，供调用方展示
        self.change_notes: list[str] = []

        self._build_ui()
        self._refresh_doom()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        c = styles.COLORS
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        # ---------- 毁灭进度 ----------
        self.doom_label = QLabel()
        self.doom_label.setTextFormat(Qt.TextFormat.RichText)
        self.doom_label.setWordWrap(True)
        self.doom_label.setStyleSheet(
            f"background:{c['bg_input']};"
            f"border:1px solid {c['border_soft']};"
            "border-radius:8px; padding:12px 14px;"
        )
        root.addWidget(self.doom_label)

        # ---------- 模拟控制 ----------
        sim_row = QHBoxLayout()
        sim_row.setSpacing(6)
        sim_row.addWidget(_hint("模拟进度"))

        for level in range(DOOM_MAX + 1):
            button = QPushButton(f"{level} {level_name(level)}")
            button.setToolTip(ceiling_for(level))
            button.setStyleSheet("padding: 3px 10px; font-size: 12px;")
            button.clicked.connect(lambda _=False, lv=level: self._set_doom(lv))
            sim_row.addWidget(button)

        sim_row.addStretch(1)

        reset_button = QPushButton("清空履历")
        reset_button.setStyleSheet("padding: 3px 10px; font-size: 12px;")
        reset_button.clicked.connect(self._reset_doom)
        sim_row.addWidget(reset_button)

        root.addLayout(sim_row)

        # ---------- 玩家操作 ----------
        action_header = QLabel("玩家操作（将被结算成事件）")
        action_header.setObjectName("PanelHint")
        root.addWidget(action_header)

        self.action_edit = QPlainTextEdit()
        self.action_edit.setPlaceholderText(
            "例如：我沿着铁索阶梯往下走，想找码头帮的仓库"
        )
        self.action_edit.setFixedHeight(72)
        root.addWidget(self.action_edit)

        # ---------- 结果 ----------
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        self.result_view = QTextBrowser()
        self.result_view.setObjectName("StoryView")
        splitter.addWidget(self.result_view)

        self.log_view = QTextBrowser()
        self.log_view.setObjectName("StoryView")
        self.log_view.setPlaceholderText("生成过程与状态变更会显示在这里")
        splitter.addWidget(self.log_view)
        splitter.setSizes([520, 180])

        root.addWidget(splitter, 1)

        # ---------- 按钮 ----------
        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        self.generate_button = QPushButton("生成事件")
        self.generate_button.setObjectName("PrimaryButton")
        self.generate_button.clicked.connect(self._on_generate)
        buttons.addWidget(self.generate_button)

        buttons.addStretch(1)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._on_cancel)
        buttons.addWidget(self.cancel_button)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)

        root.addLayout(buttons)

    def _refresh_doom(self) -> None:
        c = styles.COLORS
        doom: DoomState = self._state.doom

        # 进度条：用方块表示，一眼能看出还剩多少
        bar = "".join(
            f'<span style="color:{c["danger"] if i < doom.level else c["border"]};">'
            f"{'■' if i < doom.level else '□'}</span>"
            for i in range(DOOM_MAX)
        )

        evidence_html = ""
        if doom.evidence:
            items = "".join(
                f'<div style="color:{c["text_dim"]};font-size:12.5px;">'
                f"　{i}. {_escape(e)}</div>"
                for i, e in enumerate(doom.evidence, 1)
            )
            evidence_html = (
                f'<div style="margin-top:8px;color:{c["text_faint"]};'
                f'font-size:12px;">推动毁灭的选择履历：</div>{items}'
            )
        else:
            evidence_html = (
                f'<div style="margin-top:8px;color:{c["text_faint"]};font-size:12px;">'
                "尚无推动毁灭的选择 —— 这个状态下，任何世界毁灭的描写都会被拦下。"
                "</div>"
            )

        self.doom_label.setText(
            f'<div style="font-size:13.5px;color:{c["text"]};">'
            f"毁灭进度　{bar}　"
            f'<b style="color:{c["text"]};">{doom.progress_text()}</b></div>'
            f'<div style="margin-top:7px;color:{c["text_faint"]};font-size:12px;">'
            f"本回合描写上限：{_escape(doom.ceiling)}</div>"
            f"{evidence_html}"
        )
        self._refresh_ready_state()

    def _refresh_ready_state(self) -> None:
        if self._world is None:
            self.generate_button.setEnabled(False)
            self.result_view.setHtml(
                f'<div style="color:{styles.COLORS["warning"]};padding:6px;">'
                "还没有生效的世界观文档。请先在「游戏 → 世界观文档」中导入一份。"
                "</div>"
            )
            return
        if not self._config.has_api_key:
            self.generate_button.setEnabled(False)
            self.result_view.setHtml(
                f'<div style="color:{styles.COLORS["warning"]};padding:6px;">'
                "尚未配置 DeepSeek API Key，请先在「设置 → API 设置」中填入。"
                "</div>"
            )
            return
        self.generate_button.setEnabled(True)

    # ------------------------------------------------------------------
    # 模拟控制
    # ------------------------------------------------------------------

    def _set_doom(self, level: int) -> None:
        doom = self._state.doom
        doom.level = max(0, min(level, DOOM_MAX))
        # 模拟时补几条履历，让提示词里的因果链是完整的
        if doom.level > len(doom.evidence):
            doom.evidence = [
                f"模拟履历 {i}：玩家做出了第 {i} 次重大且不可逆的选择"
                for i in range(1, doom.level + 1)
            ]
        elif doom.level < len(doom.evidence):
            doom.evidence = doom.evidence[: doom.level]

        self._refresh_doom()

    def _reset_doom(self) -> None:
        self._state.doom.reset()
        self._refresh_doom()

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------

    def _on_generate(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return

        action = self.action_edit.toPlainText().strip()
        if not action:
            QMessageBox.information(self, "请先填写操作", "在上方写下玩家做了什么。")
            return

        client = DeepSeekClient(
            api_key=self._config.api_key,
            base_url=self._config.normalized_base_url(),
            model=self._config.model,
            timeout=self._config.timeout,
        )

        self.result_view.clear()
        self.log_view.clear()
        self.change_notes = []
        self._set_busy(True)

        self._thread = EventThread(
            client,
            self._world,
            action,
            player=self._player,
            state=self._state,
            inventory=self._inventory,
            history=self._history,
            max_retries=self._config.max_validate_retries,
            parent=self,
        )
        self._thread.progress.connect(self._on_progress)
        self._thread.attempt_done.connect(self._on_attempt)
        self._thread.done.connect(self._on_done)
        self._thread.failed.connect(self._on_failed)
        self._thread.usage_ready.connect(self._on_usage)
        self._thread.finished.connect(self._on_finished)
        self._thread.start()

    def _on_usage(self, model: str, usage: dict, reason: str) -> None:
        if self._tracker is None:
            return
        self._tracker.record(
            model=model, usage=usage, reason=reason, peak=self._config.forced_peak()
        )

    def _set_busy(self, busy: bool) -> None:
        self.generate_button.setEnabled(not busy and self._world is not None)
        self.generate_button.setText("生成中…" if busy else "生成事件")
        self.cancel_button.setEnabled(busy)

    def _on_cancel(self) -> None:
        if self._thread is not None:
            self._thread.cancel()

    def _on_finished(self) -> None:
        self._thread = None

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------

    def _on_progress(self, message: str) -> None:
        self._log(f'<span style="color:{styles.COLORS["text_dim"]};">{_escape(message)}</span>')

    def _on_attempt(self, attempt) -> None:
        c = styles.COLORS
        if attempt.parse_error:
            color, text = c["warning"], f"输出格式有误：{attempt.parse_error}"
        elif attempt.verdict.passed:
            return
        else:
            color, text = c["danger"], attempt.verdict.reason or "与世界观冲突"

        self._log(f'<span style="color:{color};">✕ 第 {attempt.index} 次被拦下：{_escape(text)}</span>')

    def _on_done(self, result) -> None:
        self._set_busy(False)
        c = styles.COLORS
        event = result.event

        # ---- 事件正文 ----
        blocks = [
            f'<div style="color:{c["text"]};font-size:14.5px;line-height:185%;">'
            f"{_escape(event.narrative)}</div>"
        ]

        if event.has_npc():
            dialog = (
                f'<div style="margin-top:6px;color:{c["text"]};font-size:14px;">'
                f"{_escape(event.npc_dialog)}</div>"
                if event.npc_dialog
                else ""
            )
            blocks.append(
                f'<div style="margin-top:14px;">'
                f'<span style="color:{c["warning"]};font-weight:600;">'
                f"{_escape(event.npc)}</span>{dialog}</div>"
            )

        if event.options:
            items = "".join(
                f'<div style="margin-top:6px;color:{c["text_dim"]};'
                f'font-size:13px;">{i}. {_escape(opt)}</div>'
                for i, opt in enumerate(event.options, 1)
            )
            blocks.append(
                f'<div style="margin-top:16px;color:{c["text_faint"]};'
                f'font-size:12px;">行动选项</div>{items}'
            )

        self.result_view.setHtml("".join(blocks))
        self._scroll(self.result_view)

        # ---- 状态变更 ----
        self.change_notes = apply_state_changes(event.changes, self._state, self._player)
        self._refresh_doom()

        if self.change_notes:
            self._log(
                f'<span style="color:{c["accent"]};">状态变更：</span>'
                + "　".join(_escape(n) for n in self.change_notes)
            )
        else:
            self._log(
                f'<span style="color:{c["text_faint"]};">本回合世界状态没有变化</span>'
            )

        # ---- 毁灭增量：AI 提议 vs 框架裁定 ----
        self._log_doom_verdict(result)

        if result.had_conflicts:
            self._log(
                f'<span style="color:{c["text_faint"]};">'
                f"经过 {result.retries_used} 次重试后通过</span>"
            )

    def _log_doom_verdict(self, result) -> None:
        """把「AI 提议了多少」和「框架接受了多少」分开显示。

        这是这阶段最该被看见的部分：提议与裁定是两回事。
        """
        c = styles.COLORS
        event = result.event

        if event.doom_delta <= 0:
            self._log(
                f'<span style="color:{c["text_faint"]};">'
                f"毁灭增量：AI 未提议推进（本回合进度不变）</span>"
            )
            return

        proposed = (
            f'<span style="color:{c["warning"]};">'
            f"AI 提议推进 {event.doom_delta} 级</span>"
            f'<span style="color:{c["text_faint"]};">'
            f"　理由：{_escape(event.doom_reason or '（未给出）')}</span>"
        )

        if result.applied_doom_delta > 0:
            verdict = (
                f'<span style="color:{c["danger"]};font-weight:600;">'
                f"　→ 框架接受，{_escape(result.doom_note)}</span>"
            )
        else:
            verdict = (
                f'<span style="color:{c["success"]};font-weight:600;">'
                f"　→ 框架驳回：{_escape(result.doom_note)}</span>"
            )

        self._log(proposed + verdict)

    def _on_failed(self, error) -> None:
        self._set_busy(False)
        c = styles.COLORS

        message = getattr(error, "message", str(error))
        title = (
            "连续多次生成都未通过校验"
            if isinstance(error, ValidationExhausted)
            else "生成失败"
        )

        self.result_view.setHtml(
            f'<div style="padding:6px;">'
            f'<div style="color:{c["danger"]};font-weight:600;">✕ {title}</div>'
            f'<div style="color:{c["text_dim"]};font-size:13px;line-height:165%;'
            f'margin-top:8px;white-space:pre-wrap;">{_escape(message)}</div></div>'
        )
        QMessageBox.warning(self, title, message)

    def _log(self, html: str) -> None:
        self.log_view.append(f'<div style="font-size:12.5px;">{html}</div>')
        self._scroll(self.log_view)

    @staticmethod
    def _scroll(view) -> None:
        bar = view.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_thread()
        super().closeEvent(event)

    def reject(self) -> None:
        self._stop_thread()
        super().reject()

    def accept(self) -> None:
        self._stop_thread()
        super().accept()

    def _stop_thread(self) -> None:
        if self._thread is None or not self._thread.isRunning():
            return

        self._thread.cancel()
        for signal in (
            self._thread.progress,
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


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(
        f"color:{styles.COLORS['text_faint']}; font-size:12.5px;"
    )
    return label


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
