"""全局样式表（QSS）。

只负责外观，不含任何业务逻辑。
用 string.Template 而不是 str.format / f-string，
这样 QSS 里大量的花括号可以原样保留。
"""

from string import Template

# 中文字体优先级：Windows 用微软雅黑，兜底无衬线
FONT_FAMILY = '"Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif'

COLORS = {
    # 背景层次，越靠上层越亮
    "bg_window": "#0f1117",
    "bg_panel": "#171a23",
    "bg_elev": "#1e2230",
    "bg_input": "#12141c",
    "bg_hover": "#262b3b",
    # 描边
    "border": "#2a3044",
    "border_soft": "#212636",
    # 文字
    "text": "#d8dce6",
    "text_dim": "#8b94a8",
    "text_faint": "#5d6579",
    # 主色调
    "accent": "#6c8cff",
    "accent_hover": "#839eff",
    "accent_dim": "#3d4d8f",
    # 状态色
    "danger": "#e05c6e",
    "danger_hover": "#f0707f",
    "success": "#4fbf8b",
    "warning": "#d9a441",
    # 稀有度
    "r_common": "#9aa3b5",
    "r_good": "#4fbf8b",
    "r_rare": "#5aa9f0",
    "r_epic": "#a97bf0",
    "r_legend": "#e8a13c",
}

# 稀有度 → 颜色，背包/道具展示共用
RARITY_COLORS = {
    "普通": COLORS["r_common"],
    "精良": COLORS["r_good"],
    "稀有": COLORS["r_rare"],
    "史诗": COLORS["r_epic"],
    "传说": COLORS["r_legend"],
}


def rarity_color(rarity: str) -> str:
    """取稀有度对应的颜色，未知稀有度按普通处理。"""
    return RARITY_COLORS.get(rarity, COLORS["r_common"])


_QSS = Template(
    """
* {
    font-family: $font;
    font-size: 14px;
    color: $text;
}

QWidget#Root { background: $bg_window; }

QMainWindow, QDialog { background: $bg_window; }

/* ---------- 菜单栏 ---------- */
QMenuBar {
    background: $bg_panel;
    border-bottom: 1px solid $border;
    padding: 2px 6px;
}
QMenuBar::item {
    padding: 6px 14px;
    background: transparent;
    border-radius: 6px;
}
QMenuBar::item:selected { background: $bg_hover; }
QMenuBar::item:pressed { background: $accent_dim; }

QMenu {
    background: $bg_elev;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 6px;
}
QMenu::item {
    padding: 7px 28px 7px 16px;
    border-radius: 6px;
}
QMenu::item:selected { background: $accent_dim; }
QMenu::item:disabled { color: $text_faint; }
QMenu::separator {
    height: 1px;
    background: $border_soft;
    margin: 5px 10px;
}

/* ---------- 面板容器 ---------- */
QFrame#Panel {
    background: $bg_panel;
    border: 1px solid $border;
    border-radius: 10px;
}
QLabel#PanelTitle {
    color: $text_dim;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 1px;
    padding: 0px 2px 0px 2px;
}
QLabel#PanelHint {
    color: $text_faint;
    font-size: 12px;
}

/* ---------- 剧情区 ---------- */
QTextBrowser#StoryView {
    background: $bg_input;
    border: 1px solid $border_soft;
    border-radius: 8px;
    padding: 14px 16px;
    selection-background-color: $accent_dim;
}

/* ---------- 选项按钮 ---------- */
/* 选项按钮：QPushButton 不认 word-wrap，文本由内部 QLabel 承载，
   所以内边距交给布局的 contentsMargins，这里 padding 必须为 0 */
QPushButton#OptionButton {
    background: $bg_elev;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 0px;
    text-align: left;
    font-size: 14px;
}
QLabel#OptionText {
    background: transparent;
    border: none;
    color: $text;
    font-size: 14px;
}
QPushButton#OptionButton:hover {
    background: $bg_hover;
    border-color: $accent;
}
QPushButton#OptionButton:pressed { background: $accent_dim; }
QPushButton#OptionButton:disabled { color: $text_faint; border-color: $border_soft; }

/* ---------- 普通 / 主按钮 ---------- */
QPushButton {
    background: $bg_elev;
    border: 1px solid $border;
    border-radius: 7px;
    padding: 7px 16px;
}
QPushButton:hover { background: $bg_hover; border-color: $accent; }
QPushButton:pressed { background: $accent_dim; }
QPushButton:disabled { color: $text_faint; border-color: $border_soft; background: $bg_panel; }

QPushButton#PrimaryButton {
    background: $accent;
    border: 1px solid $accent;
    color: #0f1117;
    font-weight: 600;
}
QPushButton#PrimaryButton:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#PrimaryButton:disabled { background: $accent_dim; border-color: $accent_dim; color: $text_dim; }

QPushButton#DangerButton { color: $danger; border-color: #4a2a32; }
QPushButton#DangerButton:hover { background: #2a1c22; border-color: $danger; }

/* ---------- 输入控件 ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {
    background: $bg_input;
    border: 1px solid $border;
    border-radius: 7px;
    padding: 8px 12px;
    selection-background-color: $accent_dim;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {
    border-color: $accent;
}
QLineEdit:disabled, QPlainTextEdit:disabled { color: $text_faint; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: $bg_elev;
    border: 1px solid $border;
    selection-background-color: $accent_dim;
    outline: none;
}

/* ---------- 列表 / 表格 ---------- */
QListWidget, QTreeWidget, QTableWidget {
    background: $bg_input;
    border: 1px solid $border_soft;
    border-radius: 8px;
    outline: none;
    gridline-color: $border_soft;
}
QListWidget::item { padding: 6px 8px; border-radius: 5px; }
QListWidget::item:selected { background: $accent_dim; }
QListWidget::item:hover { background: $bg_hover; }

QTableWidget::item { padding: 6px 8px; border: none; }
QTableWidget::item:selected { background: $accent_dim; }
QTableWidget::item:hover { background: $bg_hover; }

QHeaderView::section {
    background: $bg_elev;
    color: $text_dim;
    border: none;
    border-right: 1px solid $border_soft;
    border-bottom: 1px solid $border;
    padding: 7px 8px;
    font-size: 12px;
    font-weight: 600;
}

/* ---------- 滚动区域 ----------
   QScrollArea 的 viewport 默认取调色板的 Base 色（浅色主题下是白色），
   不显式透明会在深色界面上露出白边 */
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollArea > QWidget > QScrollBar { background: transparent; }

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #333a4e;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: $accent_dim; }
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #333a4e;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover { background: $accent_dim; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---------- 分隔条 ---------- */
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 10px; }
QSplitter::handle:vertical { height: 10px; }
QSplitter::handle:hover { background: $accent_dim; }

/* ---------- 状态栏 ---------- */
QStatusBar {
    background: $bg_panel;
    border-top: 1px solid $border;
    color: $text_dim;
    font-size: 12px;
}
QStatusBar::item { border: none; }

/* ---------- 弹窗 ---------- */
QMessageBox { background: $bg_panel; }
QMessageBox QLabel { color: $text; }

QToolTip {
    background: $bg_elev;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 6px 8px;
}

/* ---------- 分组框 ---------- */
QGroupBox {
    border: 1px solid $border;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 10px;
    font-weight: 600;
    color: $text_dim;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}

QCheckBox { spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid $border;
    border-radius: 4px;
    background: $bg_input;
}
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }
"""
)


def stylesheet() -> str:
    """生成最终 QSS 文本。"""
    return _QSS.substitute(font=FONT_FAMILY, **COLORS)
