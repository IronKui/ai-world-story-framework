"""操作面板：区域 1 = AI 行动选项按钮，区域 2 = 玩家自由文本输入。

阶段 1 只负责界面与信号，点击后抛信号给上层处理。
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.panel_base import Panel


class OptionButton(QPushButton):
    """支持自动换行的行动选项按钮。

    QPushButton 自身不支持 word-wrap，长句子会被截断。
    这里把文本放进一个对鼠标透明的内部 QLabel，按钮只负责
    背景、边框和点击，尺寸由 QLabel 的 heightForWidth 反推。
    """

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("OptionButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._label = QLabel(text, self)
        self._label.setObjectName("OptionText")
        self._label.setWordWrap(True)
        # 让点击穿透到按钮本身，否则 hover / clicked 都会失效
        self._label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.addWidget(self._label)

        policy = QSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def text(self) -> str:  # noqa: A003 - 与 QPushButton 接口保持一致
        return self._label.text()

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt 接口
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt 接口
        margins = self.layout().contentsMargins()
        inner = max(width - margins.left() - margins.right(), 1)
        return self._label.heightForWidth(inner) + margins.top() + margins.bottom()

    def sizeHint(self):  # noqa: N802 - Qt 接口
        return self.layout().sizeHint()


class ActionPanel(Panel):
    """玩家操作入口。两种交互方式共用一套禁用/启用逻辑。"""

    #: 玩家点击了某个选项，携带选项文本
    option_chosen = pyqtSignal(str)
    #: 玩家提交了自由输入，携带输入文本
    free_input_submitted = pyqtSignal(str)
    #: 请求重新生成一批行动选项（阶段 8 接入 AI 事件生成后实现）
    refresh_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("行动", parent)

        self._busy = False
        self._option_buttons: list[QPushButton] = []

        # ---------- 区域 1：选项按钮 ----------
        options_header = QHBoxLayout()
        options_header.setContentsMargins(0, 0, 0, 0)
        label = QLabel("备选行动")
        label.setObjectName("PanelHint")
        options_header.addWidget(label)
        options_header.addStretch(1)

        self.refresh_button = QPushButton("换一批")
        self.refresh_button.setToolTip("让 AI 重新生成一批行动选项")
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button.setFixedHeight(26)
        self.refresh_button.setStyleSheet(
            "padding: 2px 12px; font-size: 12.5px; border-radius: 6px;"
        )
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        options_header.addWidget(self.refresh_button)
        self.body.addLayout(options_header)

        # 选项区域放进滚动容器，选项多的时候不会撑爆面板
        self.options_area = QScrollArea()
        self.options_area.setWidgetResizable(True)
        self.options_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.options_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.options_host = QWidget()
        self.options_layout = QVBoxLayout(self.options_host)
        self.options_layout.setContentsMargins(0, 0, 0, 0)
        self.options_layout.setSpacing(7)
        self.options_layout.addStretch(1)
        self.options_area.setWidget(self.options_host)
        # 双保险：QSS 之外再显式关掉 viewport / host 的自动填充
        self.options_area.viewport().setAutoFillBackground(False)
        self.options_host.setAutoFillBackground(False)
        self.options_area.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.body.addWidget(self.options_area, 1)

        self.empty_hint = QLabel("（等待剧情推进，选项将在此处生成）")
        self.empty_hint.setObjectName("PanelHint")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.options_layout.insertWidget(0, self.empty_hint)

        # ---------- 区域 2：自由输入 ----------
        input_header = QLabel("自由行动")
        input_header.setObjectName("PanelHint")
        self.body.addWidget(input_header)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("描述你想做的事，例如：我撬开箱子看看里面有什么")
        self.input_field.setClearButtonEnabled(True)
        self.input_field.returnPressed.connect(self._emit_free_input)
        input_row.addWidget(self.input_field, 1)

        self.send_button = QPushButton("提交")
        self.send_button.setObjectName("PrimaryButton")
        self.send_button.setFixedHeight(38)
        self.send_button.clicked.connect(self._emit_free_input)
        input_row.addWidget(self.send_button)

        self.body.addLayout(input_row)

    # ---------- 对外接口 ----------

    def set_options(self, options: list[str]) -> None:
        """用一批新选项替换当前选项区。"""
        self._clear_options()

        if not options:
            self.empty_hint = QLabel("（暂无可用选项，可在下方自由输入行动）")
            self.empty_hint.setObjectName("PanelHint")
            self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.options_layout.insertWidget(0, self.empty_hint)
            return

        for index, text in enumerate(options, start=1):
            button = OptionButton(f"{index}.  {text}")
            # 用默认参数把 text 绑进闭包，避免晚绑定
            button.clicked.connect(lambda _=False, t=text: self._choose(t))
            self.options_layout.insertWidget(self.options_layout.count() - 1, button)
            self._option_buttons.append(button)

        self._apply_busy_state()

    def clear_options(self) -> None:
        self._clear_options()
        hint = QLabel("（等待剧情推进，选项将在此处生成）")
        hint.setObjectName("PanelHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.options_layout.insertWidget(0, hint)

    def set_busy(self, busy: bool, reason: str = "") -> None:
        """AI 请求进行中：禁用所有交互，避免重复提交互相覆盖。"""
        self._busy = busy
        self._apply_busy_state()

        if busy:
            self.send_button.setText("生成中…")
            self.refresh_button.setEnabled(False)
            self.input_field.setPlaceholderText(reason or "AI 正在生成内容，请稍候…")
        else:
            self.send_button.setText("提交")
            self.refresh_button.setEnabled(True)
            self.input_field.setPlaceholderText(
                "描述你想做的事，例如：我撬开箱子看看里面有什么"
            )

    def focus_input(self) -> None:
        self.input_field.setFocus()

    # ---------- 内部实现 ----------

    def _choose(self, text: str) -> None:
        if self._busy:
            return
        self.option_chosen.emit(text)

    def _emit_free_input(self) -> None:
        if self._busy:
            return
        text = self.input_field.text().strip()
        if not text:
            return
        self.input_field.clear()
        self.free_input_submitted.emit(text)

    def _apply_busy_state(self) -> None:
        for button in self._option_buttons:
            button.setEnabled(not self._busy)
        self.send_button.setEnabled(not self._busy)
        self.input_field.setEnabled(not self._busy)

    def _clear_options(self) -> None:
        self._option_buttons.clear()
        # 保留末尾的 stretch，其余全部移除
        while self.options_layout.count() > 1:
            item = self.options_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
