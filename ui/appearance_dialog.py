"""外观设置：配色方案与自定义背景图。

主题在启动时应用、改完需要重启。原因是代码里有 80 多处直接在构造时
读取颜色值，要做到即时生效得把那批内联样式全部改写成 QSS 选择器 ——
那是一次独立重构，不适合塞进这一版。用户改主题的频率很低，
「重启后生效」是可接受的代价。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.config import AppConfig
from ui import styles, themes

#: 背景图支持的格式
IMAGE_FILTER = "图片 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*)"


class AppearanceDialog(QDialog):
    """配色与背景图设置。"""

    def __init__(self, config: AppConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("外观设置")
        self.setModal(True)
        self.resize(680, 620)

        #: 改的是副本，用户点保存才写回
        self._config = config
        self._theme_key = config.theme if config.theme in themes.THEMES else themes.DEFAULT_THEME
        self._background = config.background_image
        #: 是否有实际改动，决定要不要提示重启
        self.changed = False

        self._build_ui()
        self._select_theme(self._theme_key)
        self._refresh_background_row()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        c = styles.COLORS
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        # ---------- 配色 ----------
        theme_header = QLabel("配色方案")
        theme_header.setObjectName("PanelHint")
        root.addWidget(theme_header)

        self.theme_list = QListWidget()
        self.theme_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        for key, name, description in themes.theme_names():
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setText(f"{name}　—　{description}")
            item.setForeground(QColor(styles.COLORS["text"]))
            self.theme_list.addItem(item)
        self.theme_list.currentItemChanged.connect(self._on_theme_changed)
        self.theme_list.setMinimumHeight(170)
        root.addWidget(self.theme_list)

        # 色板预览：光看名字看不出实际效果
        preview_header = QLabel("配色预览")
        preview_header.setObjectName("PanelHint")
        root.addWidget(preview_header)

        self.swatch_row = QHBoxLayout()
        self.swatch_row.setSpacing(6)
        self.swatch_row.setContentsMargins(0, 0, 0, 0)
        self._swatches: list[QFrame] = []
        for _ in range(8):
            swatch = QFrame()
            swatch.setFixedHeight(34)
            swatch.setFrameShape(QFrame.Shape.NoFrame)
            self.swatch_row.addWidget(swatch, 1)
            self._swatches.append(swatch)
        root.addLayout(self.swatch_row)

        # ---------- 背景图 ----------
        bg_header = QLabel("自定义背景")
        bg_header.setObjectName("PanelHint")
        root.addWidget(bg_header)

        bg_row = QHBoxLayout()
        bg_row.setSpacing(8)

        self.bg_label = QLabel()
        self.bg_label.setWordWrap(True)
        self.bg_label.setStyleSheet(
            f"background:{c['bg_input']};border:1px solid {c['border_soft']};"
            "border-radius:7px;padding:9px 12px;"
        )
        bg_row.addWidget(self.bg_label, 1)

        self.bg_choose = QPushButton("选择图片…")
        self.bg_choose.clicked.connect(self._on_choose_background)
        bg_row.addWidget(self.bg_choose)

        self.bg_clear = QPushButton("移除")
        self.bg_clear.clicked.connect(self._on_clear_background)
        bg_row.addWidget(self.bg_clear)

        root.addLayout(bg_row)

        note = QLabel(
            "背景图会被压暗后铺在界面底层，面板保持半透明以便看清文字。"
            "主题与背景的改动需要重启程序后生效。"
        )
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color:{c['text_faint']}; font-size:12px; line-height:150%;"
        )
        root.addWidget(note)

        root.addStretch(1)

        # ---------- 底部 ----------
        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        reset_button = QPushButton("恢复默认")
        reset_button.clicked.connect(self._on_reset)
        buttons.addWidget(reset_button)

        buttons.addStretch(1)

        save_button = QPushButton("保存")
        save_button.setObjectName("PrimaryButton")
        save_button.clicked.connect(self._on_save)
        buttons.addWidget(save_button)

        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)

        root.addLayout(buttons)

    # ------------------------------------------------------------------
    # 配色
    # ------------------------------------------------------------------

    def _select_theme(self, key: str) -> None:
        for row in range(self.theme_list.count()):
            item = self.theme_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                self.theme_list.setCurrentRow(row)
                return
        self.theme_list.setCurrentRow(0)

    def _on_theme_changed(self, current, _previous) -> None:
        if current is None:
            return
        self._theme_key = current.data(Qt.ItemDataRole.UserRole)
        self._render_swatches(self._theme_key)

    def _render_swatches(self, key: str) -> None:
        """用该主题的几个代表色填充预览方块。"""
        colors = themes.get_colors(key)
        order = ["bg_panel", "bg_elev", "text", "accent",
                 "r_good", "r_rare", "r_epic", "r_legend"]

        for swatch, color_key in zip(self._swatches, order):
            color = colors.get(color_key, "#000000")
            swatch.setStyleSheet(
                f"background:{color};border:1px solid {colors['border']};"
                "border-radius:6px;"
            )

    # ------------------------------------------------------------------
    # 背景图
    # ------------------------------------------------------------------

    def _refresh_background_row(self) -> None:
        if not self._background:
            self.bg_label.setText("未设置背景图")
            self.bg_label.setToolTip("")
            self.bg_clear.setEnabled(False)
            return

        path = Path(self._background)
        exists = path.is_file()

        if exists:
            size = path.stat().st_size / 1024
            self.bg_label.setText(f"{path.name}　（{size:.0f} KB）")
        else:
            self.bg_label.setText(f"{path.name}　⚠ 文件已不存在，将被清除")

        self.bg_label.setToolTip(str(path))
        self.bg_clear.setEnabled(True)

    def _on_choose_background(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景图片", "", IMAGE_FILTER
        )
        if not path:
            return

        # 选之前先确认能解码 —— 坏图直接告诉用户，比存下去再静默失败好
        pixmap = QPixmap(path)
        if pixmap.isNull():
            QMessageBox.warning(
                self, "无法读取图片", "这个文件不是程序能识别的图片格式。"
            )
            return

        if pixmap.width() < 800 or pixmap.height() < 600:
            answer = QMessageBox.question(
                self,
                "图片偏小",
                f"这张图只有 {pixmap.width()}×{pixmap.height()}，"
                "铺满窗口后会比较模糊。\n\n仍然使用吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self._background = path
        self._refresh_background_row()

    def _on_clear_background(self) -> None:
        self._background = ""
        self._refresh_background_row()

    def _on_reset(self) -> None:
        self._theme_key = themes.DEFAULT_THEME
        self._background = ""
        self._select_theme(self._theme_key)
        self._refresh_background_row()

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------

    def _on_save(self) -> None:
        self.changed = (
            self._theme_key != self._config.theme
            or self._background != self._config.background_image
        )
        self._config.theme = self._theme_key
        self._config.background_image = self._background
        self.accept()
