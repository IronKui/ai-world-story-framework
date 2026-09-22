"""用量与花费统计窗口。

展示本次运行与历史累计的 token、调用次数与估算花费。
这里显示的都是估算值 —— 实际账单请以 DeepSeek 平台为准。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.pricing import MODEL_PRICING, is_peak_now
from core.usage import UsageTracker
from ui import styles


class UsageDialog(QDialog):
    """用量统计。"""

    def __init__(
        self,
        tracker: UsageTracker,
        usd_to_cny: float = 7.1,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._tracker = tracker
        self._rate = usd_to_cny

        self.setWindowTitle("用量与花费")
        self.setModal(True)
        self.resize(760, 600)

        self._build_ui()
        self._render()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        # ---------- 概览卡片 ----------
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(
            f"background:{styles.COLORS['bg_input']};"
            f"border:1px solid {styles.COLORS['border_soft']};"
            "border-radius:8px; padding:14px 16px;"
        )
        root.addWidget(self.summary)

        # ---------- 按模型 ----------
        model_header = QLabel("按模型统计")
        model_header.setObjectName("PanelHint")
        root.addWidget(model_header)

        self.model_table = QTableWidget(0, 5)
        self.model_table.setHorizontalHeaderLabels(
            ["模型", "调用次数", "输入 token", "输出 token", "花费"]
        )
        self._setup_table(self.model_table)
        self.model_table.setMinimumHeight(110)
        root.addWidget(self.model_table, 1)

        # ---------- 最近调用 ----------
        recent_header = QLabel("最近调用")
        recent_header.setObjectName("PanelHint")
        root.addWidget(recent_header)

        self.recent_table = QTableWidget(0, 5)
        self.recent_table.setHorizontalHeaderLabels(
            ["时间", "用途", "模型", "token", "花费"]
        )
        self._setup_table(self.recent_table)
        root.addWidget(self.recent_table, 2)

        # ---------- 底部 ----------
        footer_note = QLabel(
            "花费按官方公开价格估算，峰谷由本地时间判断，"
            "中国法定节假日无法自动识别。实际金额以 DeepSeek 平台账单为准。"
        )
        footer_note.setWordWrap(True)
        footer_note.setStyleSheet(
            f"color:{styles.COLORS['text_faint']}; font-size:12px; line-height:150%;"
        )
        root.addWidget(footer_note)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        self.reset_button = QPushButton("重置历史累计")
        self.reset_button.setObjectName("DangerButton")
        self.reset_button.setToolTip("只清空历史总计，本次运行的统计保留")
        self.reset_button.clicked.connect(self._on_reset)
        buttons.addWidget(self.reset_button)

        buttons.addStretch(1)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)

        root.addLayout(buttons)

    @staticmethod
    def _setup_table(table: QTableWidget) -> None:
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setAlternatingRowColors(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        # 其余列必须显式设成按内容自适应，否则保持默认的 100px，
        # 模型名这类较长的内容会被截成「deepseek…」
        for column in range(1, table.columnCount()):
            header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        header.setHighlightSections(False)

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------

    def _render(self) -> None:
        c = styles.COLORS
        session = self._tracker.session
        lifetime = self._tracker.lifetime
        peak = is_peak_now()

        self.summary.setText(
            f'<table style="font-size:13px;" cellspacing="0" cellpadding="3">'
            f"{self._summary_row('本次运行', session, c, 'text')}"
            f"{self._summary_row('历史累计', lifetime, c, 'accent')}"
            f"</table>"
            f'<div style="margin-top:10px;color:{c["text_faint"]};font-size:12px;">'
            f"当前时段：<b style='color:{c['text_dim']};'>"
            f"{'高峰' if peak else '低谷'}</b>"
            f"　（高峰 = UTC 周一至周五 01:00-04:00、06:00-10:00）"
            f"　汇率按 1$ = ¥{self._rate:.2f} 换算"
            f"</div>"
        )

        self._render_models()
        self._render_recent()

    def _summary_row(self, label: str, totals, c, color_key: str) -> str:
        return (
            f"<tr>"
            f'<td style="color:{c["text_faint"]};padding-right:18px;">{label}</td>'
            f'<td style="color:{c[color_key]};font-weight:600;">'
            f"{totals.calls} 次调用</td>"
            f'<td style="color:{c["text_dim"]};padding-left:16px;">'
            f"{totals.total_tokens:,} token</td>"
            f'<td style="color:{c[color_key]};padding-left:16px;font-weight:600;">'
            f"${totals.cost_usd:.6f}"
            f'<span style="color:{c["text_faint"]};font-weight:400;">'
            f"　≈ ¥{totals.cny(self._rate):.4f}</span></td>"
            "</tr>"
        )

    def _render_models(self) -> None:
        entries = sorted(
            self._tracker.by_model.items(),
            key=lambda pair: pair[1].cost_usd,
            reverse=True,
        )
        self.model_table.setRowCount(len(entries))

        for row, (name, totals) in enumerate(entries):
            known = name in MODEL_PRICING
            self._set_cell(
                self.model_table, row, 0,
                name + ("" if known else "（按 Flash 估算）"),
                styles.COLORS["text"],
            )
            self._set_cell(self.model_table, row, 1, str(totals.calls), None, center=True)
            self._set_cell(
                self.model_table, row, 2, f"{totals.prompt_tokens:,}", None, center=True
            )
            self._set_cell(
                self.model_table, row, 3,
                f"{totals.completion_tokens:,}", None, center=True,
            )
            self._set_cell(
                self.model_table, row, 4,
                f"${totals.cost_usd:.6f}",
                styles.COLORS["accent"], center=True,
            )

        if not entries:
            self._show_empty(self.model_table, 5, "尚无调用记录")

    def _render_recent(self) -> None:
        records = list(reversed(self._tracker.recent))
        self.recent_table.setRowCount(len(records))

        for row, record in enumerate(records):
            when = record.at.replace("T", " ")[5:] if record.at else "-"
            self._set_cell(self.recent_table, row, 0, when, styles.COLORS["text_dim"])
            self._set_cell(
                self.recent_table, row, 1, record.reason or "-", styles.COLORS["text"]
            )
            self._set_cell(
                self.recent_table, row, 2, record.model, styles.COLORS["text_dim"]
            )
            self._set_cell(
                self.recent_table, row, 3,
                f"{record.prompt_tokens}+{record.completion_tokens}",
                None, center=True,
            )
            self._set_cell(
                self.recent_table, row, 4,
                f"${record.cost_usd:.6f}", styles.COLORS["text_dim"], center=True,
            )

        if not records:
            self._show_empty(self.recent_table, 5, "尚无调用记录")

    @staticmethod
    def _set_cell(table, row, col, text, color, center=False) -> None:
        from PyQt6.QtGui import QColor

        item = QTableWidgetItem(str(text))
        if color:
            item.setForeground(QColor(color))
        if center:
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
            )
        table.setItem(row, col, item)

    @staticmethod
    def _show_empty(table, columns: int, text: str) -> None:
        from PyQt6.QtGui import QColor

        table.setRowCount(1)
        item = QTableWidgetItem(text)
        item.setForeground(QColor(styles.COLORS["text_faint"]))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        table.setItem(0, 0, item)
        for col in range(1, columns):
            table.setItem(0, col, QTableWidgetItem(""))

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------

    def _on_reset(self) -> None:
        answer = QMessageBox.question(
            self,
            "重置历史累计",
            "这将清空历史累计的 token 数与花费统计（不影响本次运行的统计，"
            "也不会影响任何存档）。\n\n确定重置吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._tracker.reset_lifetime()
        self._render()
