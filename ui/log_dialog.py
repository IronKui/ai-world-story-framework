"""调试日志查看窗口。

开启调试日志后，每一次发给 AI 的 prompt、模型的返回、世界观校验的
冲突原因、网络异常都会记进内存环形缓冲并可选落盘。这个窗口用来查看它们。

提示：日志内容包含世界观原文与玩家游玩过程，分享日志前请注意。
"""

from __future__ import annotations

import subprocess
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.debuglog import MEMORY_LIMIT, DebugLog
from ui import styles

#: 级别 → 显示颜色
LEVEL_COLORS = {
    "info": "text_dim",
    "warn": "warning",
    "error": "danger",
}

LEVEL_LABELS = {"info": "信息", "warn": "警告", "error": "错误"}


class LogDialog(QDialog):
    """调试日志查看器。"""

    def __init__(self, log: DebugLog, parent: QWidget | None = None):
        super().__init__(parent)
        self._log = log

        self.setWindowTitle("调试日志")
        self.resize(1100, 700)

        self._build_ui()
        self.reload()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        c = styles.COLORS
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        # ---------- 工具栏 ----------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        toolbar.addWidget(_hint("级别"))
        self.level_combo = QComboBox()
        self.level_combo.addItem("全部", "")
        for level, label in LEVEL_LABELS.items():
            self.level_combo.addItem(label, level)
        self.level_combo.setFixedWidth(90)
        self.level_combo.currentIndexChanged.connect(self._apply_filter)
        toolbar.addWidget(self.level_combo)

        toolbar.addWidget(_hint("标签"))
        self.tag_combo = QComboBox()
        self.tag_combo.addItem("全部", "")
        self.tag_combo.setFixedWidth(110)
        self.tag_combo.currentIndexChanged.connect(self._apply_filter)
        toolbar.addWidget(self.tag_combo)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索内容…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.search_edit, 1)

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.reload)
        toolbar.addWidget(self.refresh_button)

        root.addLayout(toolbar)

        # ---------- 列表 + 详情 ----------
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["时间", "级别", "标签", "摘要"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setHighlightSections(False)

        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self.table)

        self.detail = QTextBrowser()
        self.detail.setObjectName("StoryView")
        self.detail.setPlaceholderText("选中一条日志查看完整内容")
        splitter.addWidget(self.detail)
        splitter.setSizes([360, 300])

        root.addWidget(splitter, 1)

        # ---------- 底部 ----------
        footer = QHBoxLayout()
        footer.setSpacing(8)

        self.status_label = QLabel()
        self.status_label.setStyleSheet(
            f"color:{c['text_faint']}; font-size:12px;"
        )
        footer.addWidget(self.status_label, 1)

        self.clear_memory_button = QPushButton("清空显示")
        self.clear_memory_button.setToolTip("只清空内存中的记录，不动日志文件")
        self.clear_memory_button.clicked.connect(self._on_clear_memory)
        footer.addWidget(self.clear_memory_button)

        self.clear_file_button = QPushButton("清空日志文件")
        self.clear_file_button.setObjectName("DangerButton")
        self.clear_file_button.clicked.connect(self._on_clear_file)
        footer.addWidget(self.clear_file_button)

        self.open_folder_button = QPushButton("打开日志所在目录")
        self.open_folder_button.clicked.connect(self._on_open_folder)
        footer.addWidget(self.open_folder_button)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        footer.addWidget(close_button)

        root.addLayout(footer)

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """重新读取日志并重建标签下拉。"""
        entries = self._log.entries()

        # 标签下拉要保留当前选择，否则每次刷新都会跳回「全部」
        current = self.tag_combo.currentData()
        tags = sorted({entry.tag for entry in entries})

        self.tag_combo.blockSignals(True)
        self.tag_combo.clear()
        self.tag_combo.addItem("全部", "")
        for tag in tags:
            self.tag_combo.addItem(tag, tag)
        index = self.tag_combo.findData(current)
        self.tag_combo.setCurrentIndex(index if index >= 0 else 0)
        self.tag_combo.blockSignals(False)

        self._apply_filter()

    def _apply_filter(self) -> None:
        level = self.level_combo.currentData() or ""
        tag = self.tag_combo.currentData() or ""
        keyword = self.search_edit.text().strip().lower()

        entries = [
            entry
            for entry in self._log.entries()
            if (not level or entry.level == level)
            and (not tag or entry.tag == tag)
            and (
                not keyword
                or keyword in entry.message.lower()
                or keyword in entry.detail.lower()
                or keyword in entry.tag.lower()
            )
        ]
        # 最新的排在最上面
        entries.reverse()

        self._render_table(entries)
        self._refresh_status(len(entries))

    def _refresh_status(self, shown: int) -> None:
        c = styles.COLORS
        total = len(self._log.entries())

        if self._log.enabled:
            state = (
                f'<span style="color:{c["success"]};">日志已开启</span>'
                f'　写入 {self._log.path}'
            )
        else:
            state = (
                f'<span style="color:{c["text_faint"]};">'
                f"日志未开启（仅保留在内存中，共 {MEMORY_LIMIT} 条上限）"
                f"</span>"
            )

        self.status_label.setText(
            f"显示 {shown} / {total} 条　·　{state}"
        )

    def _render_table(self, entries) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(entries))

        for row, entry in enumerate(entries):
            color = styles.COLORS.get(
                LEVEL_COLORS.get(entry.level, "text"), styles.COLORS["text"]
            )

            when = entry.at.replace("T", " ")[11:] if entry.at else "-"
            cells = [
                (when, styles.COLORS["text_faint"]),
                (LEVEL_LABELS.get(entry.level, entry.level), color),
                (entry.tag, styles.COLORS["text_dim"]),
                (entry.message.replace("\n", " ")[:160], styles.COLORS["text"]),
            ]
            for column, (text, cell_color) in enumerate(cells):
                item = QTableWidgetItem(str(text))
                item.setForeground(QColor(cell_color))
                item.setData(Qt.ItemDataRole.UserRole, row)
                self.table.setItem(row, column, item)

        self.table.blockSignals(False)

        if not entries:
            self.detail.setHtml(
                f'<div style="color:{styles.COLORS["text_faint"]};padding:8px;">'
                "没有匹配的日志。</div>"
            )
        else:
            self.table.selectRow(0)

    def _on_selection_changed(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return

        row = rows[0].row()
        item = self.table.item(row, 3)
        if item is None:
            return

        # 表格只显示摘要，详情从内存里按同一顺序取回
        entries = [
            entry
            for entry in self._log.entries()
            if self._matches(entry)
        ]
        entries.reverse()

        if not 0 <= row < len(entries):
            return

        entry = entries[row]
        c = styles.COLORS
        color = c.get(LEVEL_COLORS.get(entry.level, "text"), c["text"])

        body = _escape(entry.detail) if entry.detail else "（无附加内容）"
        self.detail.setHtml(
            f'<div style="padding:6px;">'
            f'<div style="color:{c["text_faint"]};font-size:12px;">'
            f"{entry.at.replace('T', ' ')}</div>"
            f'<div style="margin:5px 0 9px 0;">'
            f'<span style="color:{color};font-weight:600;">'
            f"[{LEVEL_LABELS.get(entry.level, entry.level)}] {_escape(entry.tag)}</span>"
            f'<span style="color:{c["text"]};">　{_escape(entry.message)}</span></div>'
            f'<div style="color:{c["text_dim"]};font-size:12.5px;'
            f'line-height:165%;">{body}</div></div>'
        )

    def _matches(self, entry) -> bool:
        level = self.level_combo.currentData() or ""
        tag = self.tag_combo.currentData() or ""
        keyword = self.search_edit.text().strip().lower()

        return (
            (not level or entry.level == level)
            and (not tag or entry.tag == tag)
            and (
                not keyword
                or keyword in entry.message.lower()
                or keyword in entry.detail.lower()
                or keyword in entry.tag.lower()
            )
        )

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------

    def _on_clear_memory(self) -> None:
        self._log.clear()
        self.reload()

    def _on_clear_file(self) -> None:
        answer = QMessageBox.question(
            self,
            "清空日志文件",
            f"确定删除日志文件吗？\n{self._log.path}\n\n"
            "内存中的记录不受影响。此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        if not self._log.clear_file():
            QMessageBox.warning(self, "删除失败", "无法删除日志文件，请检查文件权限。")
        self.reload()

    def _on_open_folder(self) -> None:
        """在系统文件管理器里打开日志所在目录。"""
        folder = self._log.path.parent
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(folder)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as exc:
            QMessageBox.warning(
                self, "无法打开目录", f"{folder}\n\n{exc}"
            )


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
