"""全局样式表（QSS）。

只负责外观，不含任何业务逻辑。
用 string.Template 而不是 str.format / f-string，
这样 QSS 里大量的花括号可以原样保留。
"""

from pathlib import Path
from string import Template

from ui import themes

# 中文字体优先级：Windows 用微软雅黑，兜底无衬线
FONT_FAMILY = '"Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif'

#: 当前生效的颜色表。
#:
#: 全项目有 80 多处直接读 COLORS[...]，所以切换主题时**必须原地修改**
#: 这个 dict，不能重新赋值 —— 否则那些已经持有引用的地方会拿到旧颜色。
COLORS: dict[str, str] = themes.get_colors(themes.DEFAULT_THEME)

#: 当前主题 key
_current_theme: str = themes.DEFAULT_THEME

#: 自定义背景图路径。空表示不用背景图
_background_image: str = ""

#: 背景图存在时，各层背景的不透明度（0~255）。
#:
#: 数值越高越不透明、文字越清晰，但太高就等于没有背景图了。
#: 这里能压到 210 左右是有依据的：背景图先被 Backdrop 压暗到最亮
#: 也只有 77 的亮度（见 backdrop.DIM_ALPHA），此时面板半透明后
#: 正文对比度仍在 10:1 以上，远高于 4.5:1 的达标线。
_OVERLAY_ALPHA = {
    "bg_window": 0,     # 完全透明，由 Backdrop 组件负责画图 + 压暗
    "bg_panel": 210,    # 面板半透明，背景图透出来
    "bg_elev": 230,     # 浮起层（卡片）稍实一点，保证内容可读
    "bg_input": 185,    # 阅读区更透，背景图在这里最明显
}


def set_theme(key: str) -> None:
    """切换主题。原地更新 COLORS。"""
    global _current_theme
    _current_theme = key if key in themes.THEMES else themes.DEFAULT_THEME
    COLORS.clear()
    COLORS.update(themes.get_colors(_current_theme))


def set_background(path: str | Path | None) -> None:
    """设置自定义背景图。传空表示取消。"""
    global _background_image
    if not path:
        _background_image = ""
        return

    candidate = Path(path)
    # 图不存在就当没设，避免渲染时反复报错
    _background_image = str(candidate) if candidate.is_file() else ""


def current_theme() -> str:
    return _current_theme


def theme_display_name(key: str | None = None) -> str:
    data = themes.THEMES.get(key or _current_theme) or themes.THEMES[themes.DEFAULT_THEME]
    return data["name"]


def background_image() -> str:
    return _background_image


def has_background() -> bool:
    return bool(_background_image)


def _with_alpha(hex_color: str, alpha: int) -> str:
    """把 #rrggbb 转成 QSS 能用的 rgba()。"""
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return hex_color
    try:
        r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return hex_color
    return f"rgba({r}, {g}, {b}, {alpha})"


def rarity_color(rarity: str) -> str:
    """取稀有度对应的颜色，未知稀有度按普通处理。"""
    key = themes.RARITY_KEYS.get(rarity, "r_common")
    return COLORS.get(key, COLORS["r_common"])


#: 势力关系 → 调色板键。
#: 这些颜色属于外观，必须跟随主题 —— 早先它们写死在 core/models.py 里，
#: 结果切主题时势力关系那一栏颜色纹丝不动。
RELATION_KEYS = {
    "盟友": "success",
    "友好": "r_good",
    "中立": "r_common",
    "疏远": "warning",
    "敌对": "danger",
    "死敌": "danger_hover",
}


def relation_color(relation: str) -> str:
    """取势力关系对应的颜色，未知关系按中立处理。"""
    key = RELATION_KEYS.get(relation, "r_common")
    return COLORS.get(key, COLORS["text_dim"])


_QSS = Template(
    """
* {
    font-family: $font;
    font-size: 14px;
    color: $text;
}

QWidget#Root { background: $bg_root; }

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
    color: $bg_window;   /* 主色上的文字，用最深的底色保证对比 */
    font-weight: 600;
}
QPushButton#PrimaryButton:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#PrimaryButton:disabled { background: $accent_dim; border-color: $accent_dim; color: $text_dim; }

QPushButton#DangerButton { color: $danger; border-color: $danger_soft; }
QPushButton#DangerButton:hover { background: $danger_bg; border-color: $danger; }

/* ---------- 输入控件 ---------- */
/* 注意：QDoubleSpinBox 不是 QSpinBox 的子类，
   选择器必须分别写，漏掉哪个哪个就保持系统默认的浅色外观 */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: $bg_input;
    border: 1px solid $border;
    border-radius: 7px;
    padding: 8px 12px;
    selection-background-color: $accent_dim;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: $accent;
}
QLineEdit:disabled, QPlainTextEdit:disabled,
QSpinBox:disabled, QDoubleSpinBox:disabled { color: $text_faint; }
/* 右侧要给原生上下按钮留位，否则文字（含后缀）会压到箭头上 */
QSpinBox, QDoubleSpinBox { padding-right: 22px; }

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
    background: $bg_hover;
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
    background: $bg_hover;
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
/* 未勾选态必须比 $border 明显亮，否则在深色面板上等于隐形 */
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid $text_faint;
    border-radius: 4px;
    background: $bg_elev;
}
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked {
    background: $accent;
    border-color: $accent;
}
QCheckBox::indicator:disabled { border-color: $border_soft; }

/* ---------- 数值输入框 ----------
   刻意不覆盖 QSpinBox 的 up/down-button / arrow 子控件：
   一旦覆盖，Qt 就不再绘制默认箭头，而 QSS 又没有可行的替代画法
   （浏览器那套 border 拼三角形在 Qt 里会渲染成实心方块）。
   所以字段外观走上面的通用规则，箭头交给 Qt 原生绘制。 */
"""
)


def stylesheet() -> str:
    """生成最终 QSS 文本。"""
    colors = dict(COLORS)
    # 根容器：没有背景图时就是普通窗口底色；
    # 有背景图时必须透空，交给 Backdrop 组件去画图
    colors["bg_root"] = "transparent" if _background_image else colors["bg_window"]

    # 危险色的柔和变体。各主题的危险色不同，写死一个暗红会在别的主题里突兀
    colors["danger_soft"] = _with_alpha(colors["danger"], 90)
    colors["danger_bg"] = _with_alpha(colors["danger"], 30)

    if _background_image:
        # 各层底色改成半透明，否则面板会把背景图盖得严严实实
        for key, alpha in _OVERLAY_ALPHA.items():
            colors[key] = _with_alpha(colors[key], alpha)

    return _QSS.substitute(font=FONT_FAMILY, **colors)
