"""面板基类：统一的标题栏 + 内容区外壳，各面板只关心内容。"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class Panel(QFrame):
    """带标题的面板容器。

    布局：
        ┌──────────────────────────────┐
        │ 标题            [右上角插槽] │
        │ ──────────────────────────── │
        │ 内容区 (self.body)           │
        └──────────────────────────────┘
    """

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Panel")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("PanelTitle")
        header.addWidget(self.title_label)
        header.addStretch(1)

        # 子类可以往这里塞小控件（按钮 / 状态标签）
        self.header_slot = QHBoxLayout()
        self.header_slot.setSpacing(6)
        header.addLayout(self.header_slot)

        outer.addLayout(header)

        # 内容区，子类把控件加到这里
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(9)
        outer.addLayout(self.body, 1)

    def add_header_widget(self, widget: QWidget) -> None:
        """往标题栏右侧添加控件。"""
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header_slot.addWidget(widget)
