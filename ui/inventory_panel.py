"""背包面板：道具列表 + 详情 + 使用 / 丢弃 / 生成。

道具全部由 AI 动态生成，框架不含任何预设道具表。
面板本身不碰网络，生成请求通过 generate_requested 抛给主窗口处理。
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QWidget,
)

from core.models import Item
from ui import styles
from ui.panel_base import Panel


class InventoryPanel(Panel):
    """玩家背包。道具全部来自 AI 生成，无硬编码道具表。"""

    #: 请求使用道具，携带 item.id
    use_requested = pyqtSignal(str)
    #: 请求丢弃道具，携带 item.id
    drop_requested = pyqtSignal(str)
    #: 请求生成新道具（阶段 7）
    generate_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("背包", parent)

        self._items: list[Item] = []
        self._selected_id: str | None = None
        self._actions_enabled = True

        # ---------- 道具列表 ----------
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["道具", "稀有度"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setHighlightSections(False)

        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.setMinimumHeight(120)
        self.body.addWidget(self.table, 2)

        # ---------- 详情 ----------
        self.detail = QTextBrowser()
        self.detail.setObjectName("StoryView")
        self.detail.setOpenLinks(False)
        self.detail.setMinimumHeight(120)
        self.body.addWidget(self.detail, 3)

        # ---------- 操作按钮 ----------
        actions = QHBoxLayout()
        actions.setSpacing(8)

        self.use_button = QPushButton("使用")
        self.use_button.setObjectName("PrimaryButton")
        self.use_button.clicked.connect(self._emit_use)
        actions.addWidget(self.use_button)

        self.drop_button = QPushButton("丢弃")
        self.drop_button.setObjectName("DangerButton")
        self.drop_button.clicked.connect(self._emit_drop)
        actions.addWidget(self.drop_button)

        self.body.addLayout(actions)

        # 标题栏右侧：持有数量 + 生成入口
        self.count_label = QLabel("0 件")
        self.count_label.setStyleSheet(
            f"color: {styles.COLORS['text_faint']}; font-size: 12px;"
        )
        self.header_slot.addWidget(self.count_label)

        self.generate_button = QPushButton("生成")
        self.generate_button.setToolTip(
            "让 AI 依据世界观与当前局势现场生成道具"
        )
        self.generate_button.setStyleSheet(
            "padding: 2px 12px; font-size: 12.5px;"
        )
        self.generate_button.clicked.connect(self.generate_requested.emit)
        self.add_header_widget(self.generate_button)

        self._apply_document_style()
        self.set_items([])

    # ---------- 对外接口 ----------

    def set_items(self, items: list[Item]) -> None:
        """整体替换背包内容（读档时用）。"""
        self._items = list(items)
        self._rebuild_table()

    def upsert_item(self, item: Item) -> None:
        """新增一件道具；同 id 已存在则替换。"""
        for index, existing in enumerate(self._items):
            if existing.id == item.id:
                self._items[index] = item
                self._rebuild_table()
                self._select_by_id(item.id)
                return
        self._items.append(item)
        self._rebuild_table()
        self._select_by_id(item.id)

    def remove_item(self, item_id: str) -> None:
        """移除一件道具（丢弃 / 使用消耗完）。"""
        before = len(self._items)
        self._items = [i for i in self._items if i.id != item_id]
        if len(self._items) != before:
            self._rebuild_table()

    def items(self) -> list[Item]:
        return list(self._items)

    def selected_item(self) -> Item | None:
        return self._find(self._selected_id)

    def set_actions_enabled(self, enabled: bool) -> None:
        """AI 忙碌时禁用使用 / 丢弃，避免状态竞争。"""
        self._actions_enabled = enabled
        self._refresh_buttons()

    # ---------- 内部实现 ----------

    def _find(self, item_id: str | None) -> Item | None:
        if item_id is None:
            return None
        return next((i for i in self._items if i.id == item_id), None)

    def _rebuild_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._items))

        for row, item in enumerate(self._items):
            name_cell = QTableWidgetItem(
                item.name + (f" ×{item.quantity}" if item.quantity > 1 else "")
            )
            name_cell.setData(Qt.ItemDataRole.UserRole, item.id)
            name_cell.setForeground(QColor(styles.COLORS["text"]))
            self.table.setItem(row, 0, name_cell)

            rarity_cell = QTableWidgetItem(item.rarity)
            rarity_cell.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
            )
            rarity_cell.setForeground(QColor(styles.rarity_color(item.rarity)))
            self.table.setItem(row, 1, rarity_cell)

        self.table.blockSignals(False)

        self.count_label.setText(f"{len(self._items)} 件")

        # 选中项如果没了，退回第一行
        if self._find(self._selected_id) is None:
            self._selected_id = self._items[0].id if self._items else None
            if self._items:
                self.table.selectRow(0)

        self._render_detail()
        self._refresh_buttons()

    def _select_by_id(self, item_id: str) -> None:
        for row, item in enumerate(self._items):
            if item.id == item_id:
                self.table.selectRow(row)
                return

    def _on_selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            cell = self.table.item(rows[0].row(), 0)
            self._selected_id = cell.data(Qt.ItemDataRole.UserRole) if cell else None
        else:
            self._selected_id = None
        self._render_detail()
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        has_selection = self.selected_item() is not None
        enabled = has_selection and self._actions_enabled
        self.use_button.setEnabled(enabled)
        self.drop_button.setEnabled(enabled)

    def _render_detail(self) -> None:
        item = self.selected_item()
        if item is None:
            self.detail.setHtml(
                f'<div style="color:{styles.COLORS["text_faint"]};padding:6px;">'
                "背包是空的。<br>探索、开箱、战斗奖励或交易时，AI 会为你生成道具。"
                "</div>"
            )
            return

        c = styles.COLORS
        rc = styles.rarity_color(item.rarity)

        rows = [
            ("类型", item.category or "—"),
            ("效果", item.effect or "—"),
        ]

        fields_html = "".join(
            f'<div style="margin-bottom:8px;">'
            f'<span style="color:{c["text_faint"]};font-size:12.5px;">{label}</span><br>'
            f'<span style="color:{c["text"]};font-size:13.5px;">{value}</span></div>'
            for label, value in rows
        )

        lore_html = ""
        if item.lore:
            lore_html = (
                f'<div style="margin-top:10px;padding-top:10px;'
                f'border-top:1px solid {c["border_soft"]};">'
                f'<span style="color:{c["text_faint"]};font-size:12.5px;">背景</span><br>'
                f'<span style="color:{c["text_dim"]};font-size:13px;">{item.lore}</span></div>'
            )

        self.detail.setHtml(
            f"""
            <div style="padding:4px 2px;">
              <div style="font-size:16px;font-weight:600;color:{rc};">
                {item.name}
              </div>
              <div style="margin:4px 0 12px 0;">
                <span style="color:{rc};font-size:12px;">◆ {item.rarity}</span>
                <span style="color:{c['text_faint']};font-size:12px;">
                  &nbsp;·&nbsp;持有 {item.quantity}
                </span>
              </div>
              <div style="color:{c['text_dim']};font-size:13.5px;line-height:160%;
                          margin-bottom:12px;">{item.description or "（无描述）"}</div>
              {fields_html}
              {lore_html}
            </div>
            """
        )

    def _emit_use(self) -> None:
        item = self.selected_item()
        if item is not None:
            self.use_requested.emit(item.id)

    def _emit_drop(self) -> None:
        item = self.selected_item()
        if item is not None:
            self.drop_requested.emit(item.id)

    def _apply_document_style(self) -> None:
        self.detail.document().setDefaultStyleSheet(
            f"body {{ color: {styles.COLORS['text']}; }}"
        )
