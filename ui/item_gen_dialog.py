"""AI 道具生成窗口（阶段 7）。

选择触发场景 → AI 现场生成 → 通过解析与世界观双重校验 → 收入背包。

道具会先显示在预览区（含名称、类型、稀有度、描述、背景、效果），
玩家确认后才放进背包 —— 避免生成出玩家不想要的东西直接塞满背包。
阶段 9 的主循环会直接调用同一套生成逻辑，只是不再弹这个窗口。
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QLineEdit,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.api_client import DeepSeekClient
from core.config import AppConfig
from core.items import (
    ALL_TRIGGERS,
    MAX_ITEMS_PER_CALL,
    TRIGGER_HINTS,
    ItemRequest,
)
from core.models import Item, WorldState
from core.savegame import HistoryLog, PlayerState
from core.validator import ValidationExhausted
from core.world import WorldDocument
from ui import styles
from ui.workers import ItemGenThread


class ItemGenDialog(QDialog):
    """道具生成窗口。"""

    def __init__(
        self,
        config: AppConfig,
        world: WorldDocument | None,
        *,
        player: PlayerState | None = None,
        state: WorldState | None = None,
        inventory: list[Item] | None = None,
        history: HistoryLog | None = None,
        tracker=None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("获取道具")
        self.setModal(True)
        self.resize(760, 700)

        self._config = config
        self._world = world
        self._player = player or PlayerState()
        self._state = state or WorldState()
        self._inventory = list(inventory or [])
        self._history = history or HistoryLog()
        self._tracker = tracker
        self._thread: ItemGenThread | None = None

        #: 玩家确认收入背包的道具
        self.accepted_items: list[Item] = []
        self._pending: list[Item] = []

        self._build_ui()
        self._update_ready_state()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        c = styles.COLORS
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(11)

        header = QLabel(
            "道具没有预设库，全部由 AI 依据世界观、当前局势、"
            "玩家位置与背包现场生成，生成后会经过世界观一致性校验。"
        )
        header.setWordWrap(True)
        header.setStyleSheet(
            f"color:{c['text_faint']}; font-size:12.5px; line-height:150%;"
        )
        root.addWidget(header)

        # ---------- 参数 ----------
        params = QHBoxLayout()
        params.setSpacing(8)

        params.addWidget(_hint("触发场景"))
        self.trigger_combo = QComboBox()
        for trigger in ALL_TRIGGERS:
            self.trigger_combo.addItem(trigger, trigger)
        self.trigger_combo.setToolTip("\n".join(
            f"{t}：{TRIGGER_HINTS[t]}" for t in ALL_TRIGGERS
        ))
        self.trigger_combo.currentIndexChanged.connect(self._on_trigger_changed)
        self.trigger_combo.setFixedWidth(120)
        params.addWidget(self.trigger_combo)

        params.addWidget(_hint("数量"))
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, MAX_ITEMS_PER_CALL)
        # 宽度要容下数字，再给左右内边距和原生上下按钮留位
        self.count_spin.setFixedWidth(94)
        params.addWidget(self.count_spin)

        params.addWidget(_hint("稀有度倾向"))
        self.rarity_combo = QComboBox()
        self.rarity_combo.addItem("不限", "")
        for level in ("普通", "精良", "稀有", "史诗", "传说"):
            self.rarity_combo.addItem(level, level)
        self.rarity_combo.setFixedWidth(110)
        params.addWidget(self.rarity_combo)

        params.addStretch(1)
        root.addLayout(params)

        # 用 QLineEdit 而不是可编辑的 QComboBox：
        # 后者会带一个没有意义的下拉箭头
        self.scene_edit = QLineEdit()
        self.scene_edit.setPlaceholderText(
            "可选：补充具体情境，例如「在码头帮的仓库夹层里」"
        )
        self.scene_edit.setClearButtonEnabled(True)
        root.addWidget(self.scene_edit)

        self.trigger_note = QLabel()
        self.trigger_note.setStyleSheet(
            f"color:{c['text_faint']}; font-size:12px;"
        )
        root.addWidget(self.trigger_note)

        # ---------- 结果 ----------
        result_header = QLabel("生成结果")
        result_header.setObjectName("PanelHint")
        root.addWidget(result_header)

        self.result_view = QTextBrowser()
        self.result_view.setObjectName("StoryView")
        root.addWidget(self.result_view, 1)

        # ---------- 按钮 ----------
        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        self.generate_button = QPushButton("生成")
        self.generate_button.setObjectName("PrimaryButton")
        self.generate_button.clicked.connect(self._on_generate)
        buttons.addWidget(self.generate_button)

        self.accept_button = QPushButton("收入背包")
        self.accept_button.setEnabled(False)
        self.accept_button.clicked.connect(self._on_accept)
        buttons.addWidget(self.accept_button)

        buttons.addStretch(1)

        self.cancel_button = QPushButton("取消生成")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._on_cancel)
        buttons.addWidget(self.cancel_button)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)

        root.addLayout(buttons)

        self._on_trigger_changed()

    def _on_trigger_changed(self) -> None:
        trigger = self.trigger_combo.currentData()
        self.trigger_note.setText(TRIGGER_HINTS.get(trigger, ""))

    def _update_ready_state(self) -> None:
        if self._world is None:
            self.generate_button.setEnabled(False)
            self.result_view.setHtml(
                f'<div style="color:{styles.COLORS["warning"]};padding:6px;">'
                "还没有生效的世界观文档。\n\n"
                "道具必须依据世界观生成，请先在「游戏 → 世界观文档」中导入一份。"
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
    # 生成
    # ------------------------------------------------------------------

    def _on_generate(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return

        client = DeepSeekClient(
            api_key=self._config.api_key,
            base_url=self._config.normalized_base_url(),
            model=self._config.model,
            timeout=self._config.timeout,
        )

        request = ItemRequest(
            trigger=self.trigger_combo.currentData() or "探索",
            count=self.count_spin.value(),
            scene=self.scene_edit.text().strip(),
            rarity_hint=self.rarity_combo.currentData() or "",
        )

        self._pending = []
        self.accepted_items = []
        self.accept_button.setEnabled(False)
        self.result_view.clear()
        self._set_busy(True)

        self._thread = ItemGenThread(
            client,
            self._world,
            request,
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
        """主线程记账。一次生成会来两条：道具生成 + 世界观校验。"""
        if self._tracker is None:
            return
        self._tracker.record(
            model=model, usage=usage, reason=reason, peak=self._config.forced_peak()
        )

    def _set_busy(self, busy: bool) -> None:
        self.generate_button.setEnabled(not busy and self._world is not None)
        self.generate_button.setText("生成中…" if busy else "生成")
        self.cancel_button.setEnabled(busy)
        self.trigger_combo.setEnabled(not busy)
        self.count_spin.setEnabled(not busy)
        self.rarity_combo.setEnabled(not busy)
        self.scene_edit.setEnabled(not busy)

    def _on_cancel(self) -> None:
        if self._thread is not None:
            self._thread.cancel()

    def _on_finished(self) -> None:
        self._thread = None

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------

    def _on_progress(self, message: str) -> None:
        self.setWindowTitle(f"获取道具　—　{message}")

    def _on_attempt(self, attempt) -> None:
        """失败尝试也要让玩家看到，否则会以为程序卡住了。"""
        c = styles.COLORS
        if attempt.parse_error:
            note = f"输出格式有误，已重试（{attempt.parse_error}）"
            color, mark = c["warning"], "↻"
        elif attempt.verdict.passed:
            return  # 成功的那次由 _on_done 统一渲染
        else:
            note = attempt.verdict.reason or "与世界观冲突"
            color, mark = c["danger"], "✕"

        self.result_view.append(
            f'<div style="color:{color};font-size:12.5px;margin-top:6px;">'
            f"{mark} 第 {attempt.index} 次尝试未通过：{_escape(note)}</div>"
        )
        self._scroll_bottom()

    def _on_done(self, result) -> None:
        self._set_busy(False)
        self.setWindowTitle("获取道具")

        self._pending = list(result.items)
        c = styles.COLORS

        if not self._pending:
            self.result_view.append(
                f'<div style="color:{c["warning"]};padding:6px;">'
                "本次没有生成出可用道具，可以换个触发场景再试。</div>"
            )
            return

        blocks = []
        for item in self._pending:
            rarity_color = styles.rarity_color(item.rarity)
            blocks.append(
                f'<div style="margin-top:14px;padding:12px 14px;'
                f"background:{c['bg_elev']};border:1px solid {c['border_soft']};"
                f'border-radius:8px;">'
                f'<div style="font-size:15px;font-weight:600;color:{rarity_color};">'
                f"{_escape(item.name)}</div>"
                f'<div style="margin:5px 0 9px 0;font-size:12px;">'
                f'<span style="color:{rarity_color};">◆ {item.rarity}</span>'
                f'<span style="color:{c["text_faint"]};">'
                f"　{item.category or '未分类'}</span></div>"
                f'<div style="color:{c["text"]};font-size:13.5px;line-height:165%;">'
                f"{_escape(item.description or '（无描述）')}</div>"
                f'<div style="margin-top:9px;color:{c["text_dim"]};font-size:12.5px;'
                f'line-height:160%;">'
                f'<b style="color:{c["text_faint"]};">效果</b>　'
                f"{_escape(item.effect or '—')}</div>"
                f'<div style="margin-top:6px;color:{c["text_dim"]};font-size:12.5px;'
                f'line-height:160%;">'
                f'<b style="color:{c["text_faint"]};">背景</b>　'
                f"{_escape(item.lore or '—')}</div>"
                "</div>"
            )

        footer = (
            f'<div style="margin-top:12px;color:{c["text_faint"]};font-size:12px;">'
            f"共 {len(self._pending)} 件"
        )
        if result.had_conflicts:
            footer += f"，经过 {result.retries_used} 次重试"
        footer += "　·　点击「收入背包」后才会放进背包</div>"

        self.result_view.setHtml("".join(blocks) + footer)
        self._scroll_bottom()

        self.accept_button.setEnabled(True)

    def _on_failed(self, error) -> None:
        self._set_busy(False)
        self.setWindowTitle("获取道具")
        c = styles.COLORS

        message = getattr(error, "message", str(error))
        title = (
            "无法生成符合世界观的道具"
            if isinstance(error, ValidationExhausted)
            else "生成失败"
        )

        self.result_view.setHtml(
            f'<div style="padding:6px;">'
            f'<div style="color:{c["danger"]};font-weight:600;font-size:13.5px;">'
            f"✕ {title}</div>"
            f'<div style="color:{c["text_dim"]};font-size:13px;line-height:165%;'
            f'margin-top:8px;white-space:pre-wrap;">{_escape(message)}</div></div>'
        )

        QMessageBox.warning(self, title, message)

    def _scroll_bottom(self) -> None:
        bar = self.result_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ------------------------------------------------------------------
    # 收入背包
    # ------------------------------------------------------------------

    def _on_accept(self) -> None:
        self.accepted_items = list(self._pending)
        self.accept()

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
