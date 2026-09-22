"""世界状态简览面板：当前位置、世界时间、势力关系、累积印记。

这里展示的每一项都是喂给 AI 的上下文的一部分，
阶段 9 主循环每回合结束后调用 update_world() 刷新。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFormLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QWidget,
)

from core.models import WorldState, relation_color
from ui import styles
from ui.panel_base import Panel


class WorldPanel(Panel):
    """世界状态简览。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("世界状态", parent)

        # ---------- 位置 / 时间 ----------
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(7)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.location_value = self._make_value_label()
        self.time_value = self._make_value_label()

        form.addRow(self._make_key_label("当前地点"), self.location_value)
        form.addRow(self._make_key_label("世界时间"), self.time_value)
        self.body.addLayout(form)

        # ---------- 势力关系 ----------
        factions_header = QLabel("势力关系")
        factions_header.setObjectName("PanelHint")
        self.body.addWidget(factions_header)

        self.faction_list = QListWidget()
        self.faction_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.faction_list.setMinimumHeight(80)
        self.body.addWidget(self.faction_list, 1)

        # ---------- 累积印记 ----------
        flags_header = QLabel("世界印记")
        flags_header.setObjectName("PanelHint")
        self.body.addWidget(flags_header)

        self.flags_label = QLabel()
        self.flags_label.setWordWrap(True)
        self.flags_label.setMinimumHeight(40)
        self.flags_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.body.addWidget(self.flags_label)

        self.update_world(WorldState())

    # ---------- 对外接口 ----------

    def update_world(self, state: WorldState) -> None:
        """整体刷新世界状态。"""
        self.location_value.setText(state.location or "未知")
        self.time_value.setText(state.time or "未知")
        self._render_factions(state.factions)
        self._render_flags(state.flags)

    def set_location(self, location: str) -> None:
        self.location_value.setText(location or "未知")

    def set_time(self, time_text: str) -> None:
        self.time_value.setText(time_text or "未知")

    # ---------- 内部实现 ----------

    def _render_factions(self, factions: list[dict]) -> None:
        self.faction_list.clear()

        if not factions:
            placeholder = QListWidgetItem("（尚未接触到任何势力）")
            placeholder.setForeground(QColor(styles.COLORS["text_faint"]))
            self.faction_list.addItem(placeholder)
            return

        for faction in factions:
            name = faction.get("name", "未知势力")
            relation = faction.get("relation", "中立")

            item = QListWidgetItem(f"{name}　·　{relation}")
            item.setForeground(QColor(relation_color(relation)))
            item.setToolTip(faction.get("note", "") or f"{name}（{relation}）")
            self.faction_list.addItem(item)

    def _render_flags(self, flags: list[str]) -> None:
        if flags:
            self.flags_label.setText("　".join(f"「{f}」" for f in flags))
            self.flags_label.setStyleSheet(
                f"color: {styles.COLORS['text_dim']}; font-size: 12.5px;"
            )
        else:
            self.flags_label.setText("（尚无累积影响）")
            self.flags_label.setStyleSheet(
                f"color: {styles.COLORS['text_faint']}; font-size: 12.5px;"
            )

    @staticmethod
    def _make_key_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"color: {styles.COLORS['text_faint']}; font-size: 12.5px;"
        )
        return label

    @staticmethod
    def _make_value_label() -> QLabel:
        label = QLabel("未知")
        label.setWordWrap(True)
        label.setStyleSheet(
            f"color: {styles.COLORS['text']}; font-size: 13.5px; font-weight: 600;"
        )
        return label
