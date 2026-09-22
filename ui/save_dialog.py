"""存档 / 读档对话框：多槽位管理，同一个窗口复用于保存和读取。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.savegame import GameSave, SaveFormatError, SaveStore
from ui import styles

#: 对话框的两种用途
MODE_SAVE = "save"
MODE_LOAD = "load"


class SaveDialog(QDialog):
    """槽位列表 + 元信息。保存模式下写盘，读取模式下只返回选择的槽位。"""

    def __init__(
        self,
        store: SaveStore,
        mode: str = MODE_LOAD,
        snapshot: GameSave | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._store = store
        self._mode = mode
        self._snapshot = snapshot          # 保存模式下待写入的快照
        self._selected_slot: int | None = None
        #: 读取模式下被载入的存档
        self.loaded_save: GameSave | None = None

        self.setModal(True)
        # 宽度要能单行放下「槽位 + 世界·地点 + 回合 + 道具数 + 时间」，
        # 否则列表底部会冒出横向滚动条
        self.resize(780, 520)
        self.setWindowTitle("保存存档" if mode == MODE_SAVE else "读取存档")

        self._build_ui()
        self._reload()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(11)

        hint = QLabel(
            "选择一个槽位保存当前进度。存档只记录玩家信息、背包、世界状态、"
            "世界观副本与历史摘要，不包含 API Key。"
            if self._mode == MODE_SAVE
            else "选择一个存档继续游玩。读取会覆盖当前进度，未保存的变化将丢失。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"color:{styles.COLORS['text_faint']}; font-size:12.5px; line-height:150%;"
        )
        root.addWidget(hint)

        self.slot_list = QListWidget()
        self.slot_list.setObjectName("SlotList")
        self.slot_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.slot_list.itemDoubleClicked.connect(self._on_double_clicked)
        root.addWidget(self.slot_list, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        self.primary_button = QPushButton(
            "保存到此槽位" if self._mode == MODE_SAVE else "读取此存档"
        )
        self.primary_button.setObjectName("PrimaryButton")
        self.primary_button.clicked.connect(self._on_primary)
        buttons.addWidget(self.primary_button)

        self.delete_button = QPushButton("删除存档")
        self.delete_button.setObjectName("DangerButton")
        self.delete_button.clicked.connect(self._on_delete)
        buttons.addWidget(self.delete_button)

        buttons.addStretch(1)

        close_button = QPushButton("取消")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)

        root.addLayout(buttons)

    # ------------------------------------------------------------------
    # 列表
    # ------------------------------------------------------------------

    def _reload(self, keep_slot: int | None = None) -> None:
        self.slot_list.blockSignals(True)
        self.slot_list.clear()

        target = keep_slot or self._selected_slot
        target_row = -1

        for index, info in enumerate(self._store.list_slots()):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, info.index)
            item.setText(self._slot_text(info))
            item.setForeground(self._slot_color(info))
            if info.exists and not info.corrupted:
                item.setToolTip(
                    f"世界：{info.world_name or '（无）'}\n"
                    f"玩家：{info.player_name or '（无）'}\n"
                    f"地点：{info.location or '（无）'}\n"
                    f"回合：{info.turns}　道具：{info.item_count} 件\n"
                    f"保存时间：{info.saved_at or '（未知）'}"
                )
            self.slot_list.addItem(item)
            if target is not None and info.index == target:
                target_row = index

        self.slot_list.blockSignals(False)

        if target_row >= 0:
            self.slot_list.setCurrentRow(target_row)
        elif self.slot_list.count():
            self.slot_list.setCurrentRow(0)

        self._on_selection_changed()

    def _slot_text(self, info) -> str:
        label = info.label
        if info.corrupted:
            return f"{label}　　⚠ 文件损坏，无法读取"
        if not info.exists:
            return f"{label}　　（空）"

        when = info.saved_at.replace("T", " ") if info.saved_at else "未知时间"
        return (
            f"{label}　　{info.headline}"
            f"　　回合 {info.turns}　{info.item_count} 件道具"
            f"　　{when}"
        )

    def _slot_color(self, info):
        if info.corrupted:
            return QColor(styles.COLORS["danger"])
        if not info.exists:
            return QColor(styles.COLORS["text_faint"])
        return QColor(styles.COLORS["text"])

    def _current_slot(self) -> int | None:
        item = self.slot_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_selection_changed(self) -> None:
        slot = self._current_slot()
        self._selected_slot = slot

        info = self._store.slot_info(slot) if slot is not None else None
        occupied = info is not None and info.exists and not info.corrupted

        if self._mode == MODE_SAVE:
            self.primary_button.setEnabled(slot is not None)
            self.primary_button.setText(
                "覆盖此槽位" if occupied else "保存到此槽位"
            )
        else:
            self.primary_button.setEnabled(occupied)

        self.delete_button.setEnabled(occupied)

    def _on_double_clicked(self, _item) -> None:
        if self.primary_button.isEnabled():
            self._on_primary()

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------

    def _on_primary(self) -> None:
        if self._mode == MODE_SAVE:
            self._do_save()
        else:
            self._do_load()

    def _do_save(self) -> None:
        slot = self._selected_slot
        if slot is None or self._snapshot is None:
            return

        info = self._store.slot_info(slot)
        if info.exists and not info.corrupted:
            answer = QMessageBox.question(
                self,
                "覆盖存档",
                f"存档 {slot} 已存在：\n{info.headline}（回合 {info.turns}）\n\n"
                "确定用当前进度覆盖它吗？此操作不可撤销。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self._snapshot.slot = slot
        try:
            self._store.save(self._snapshot)
        except (OSError, SaveFormatError) as exc:
            QMessageBox.warning(self, "保存失败", f"无法写入存档：\n{exc}")
            return

        self._selected_slot = slot
        self.accept()

    def _do_load(self) -> None:
        slot = self._selected_slot
        if slot is None:
            return

        try:
            save = self._store.load(slot)
        except SaveFormatError as exc:
            QMessageBox.warning(self, "读取失败", str(exc))
            self._reload()
            return
        except OSError as exc:
            QMessageBox.warning(self, "读取失败", f"读取存档时出错：\n{exc}")
            return

        self.loaded_save = save
        self.accept()

    def _on_delete(self) -> None:
        slot = self._selected_slot
        if slot is None:
            return

        info = self._store.slot_info(slot)
        answer = QMessageBox.question(
            self,
            "删除存档",
            f"确定删除存档 {slot} 吗？\n{info.headline}\n\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        if not self._store.delete(slot):
            QMessageBox.warning(self, "删除失败", "无法删除该存档文件，请检查文件权限。")

        self._reload(keep_slot=slot)

    # ------------------------------------------------------------------
    # 结果
    # ------------------------------------------------------------------

    def selected_slot(self) -> int | None:
        return self._selected_slot
