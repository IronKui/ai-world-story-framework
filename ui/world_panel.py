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
from core.savegame import PlayerState
from ui import styles
from ui.panel_base import Panel


class WorldPanel(Panel):
    """世界状态简览。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("世界状态", parent)

        c = styles.COLORS

        # ---------- 玩家 ----------
        player_header = QLabel("玩家")
        player_header.setObjectName("PanelHint")
        self.body.addWidget(player_header)

        self.player_name_label = QLabel()
        self.player_name_label.setStyleSheet(
            f"color:{c['text']}; font-size:14.5px; font-weight:600;"
        )
        self.body.addWidget(self.player_name_label)

        self.player_attr_label = QLabel()
        self.player_attr_label.setWordWrap(True)
        self.body.addWidget(self.player_attr_label)

        self.player_status_label = QLabel()
        self.player_status_label.setWordWrap(True)
        self.body.addWidget(self.player_status_label)

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

        self.update_player(PlayerState())
        self.update_world(WorldState())

    # ---------- 对外接口 ----------

    def update_world(self, state: WorldState) -> None:
        """整体刷新世界状态。"""
        self.location_value.setText(state.location or "未知")
        self.time_value.setText(state.time or "未知")
        self._render_factions(state.factions)
        self._render_flags(state.flags)

    def update_player(self, player: PlayerState) -> None:
        """刷新玩家信息。属性表由 AI 动态维护，这里只负责渲染。"""
        c = styles.COLORS
        self.player_name_label.setText(player.name or "无名者")

        if player.attributes:
            chips = "　".join(
                f'<span style="color:{c["text_dim"]};">{key}</span>'
                f'<span style="color:{c["accent"]};"> {value}</span>'
                for key, value in player.attributes.items()
            )
            self.player_attr_label.setText(chips)
            self.player_attr_label.setTextFormat(Qt.TextFormat.RichText)
        else:
            self.player_attr_label.setText(
                f'<span style="color:{c["text_faint"]};">属性尚未确定</span>'
            )
            self.player_attr_label.setTextFormat(Qt.TextFormat.RichText)

        if player.status:
            self.player_status_label.setText(
                f'<span style="color:{c["warning"]};">'
                + "　".join(f"◈ {s}" for s in player.status)
                + "</span>"
            )
        else:
            self.player_status_label.setText(
                f'<span style="color:{c["text_faint"]};">无异常状态</span>'
            )
        self.player_status_label.setTextFormat(Qt.TextFormat.RichText)

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
