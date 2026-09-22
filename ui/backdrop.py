"""可以铺背景图的根容器。

为什么需要单独一个组件：Qt 的 QSS 无法缩放背景图，
`background-image` 只能平铺或原尺寸居中，图片尺寸和窗口对不上时很难看。
所以要自己 paintEvent，把图按比例缩放铺满，再压一层半透明底色保证文字可读。
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QWidget

#: 背景图上压的那层暗色不透明度（0~255）。
#: 太低文字会看不清，太高背景图就白设了。这个值调过。
DIM_ALPHA = 155


class Backdrop(QWidget):
    """根容器。可选的背景图 + 压暗层。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Root")
        self._source: QPixmap | None = None
        self._scaled: QPixmap | None = None
        self._dim = QColor(0, 0, 0, 0)

    # ------------------------------------------------------------------
    # 设置
    # ------------------------------------------------------------------

    def set_background(self, path: str | Path | None, dim_color: str) -> bool:
        """设置背景图。返回是否加载成功。

        图很大时只解码一次，之后按窗口尺寸缩放并缓存，
        避免每次重绘都重新解码 —— 那会让窗口拖拽时明显卡顿。
        """
        self._dim = _to_qcolor(dim_color, DIM_ALPHA)

        if not path:
            self._source = None
            self._scaled = None
            self.update()
            return True

        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._source = None
            self._scaled = None
            self.update()
            return False

        self._source = pixmap
        self._scaled = None
        self.update()
        return True

    def has_background(self) -> bool:
        return self._source is not None

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 接口
        # 窗口尺寸变了，缓存的缩放图作废
        self._scaled = None
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 接口
        if self._source is None:
            super().paintEvent(event)
            return

        painter = QPainter(self)

        if self._scaled is None:
            self._scaled = self._scale_to_cover(self._source, self.size())

        # 居中绘制
        x = (self.width() - self._scaled.width()) // 2
        y = (self.height() - self._scaled.height()) // 2
        painter.drawPixmap(x, y, self._scaled)

        # 压暗，保证上面的文字仍然清晰
        if self._dim.alpha() > 0:
            painter.fillRect(self.rect(), self._dim)

        painter.end()

    @staticmethod
    def _scale_to_cover(source: QPixmap, size) -> QPixmap:
        """按比例缩放并铺满，多余部分裁掉。

        用 KeepAspectRatioByExpanding 而不是拉伸 —— 拉伸会让人像和
        建筑变形，一眼就看出是硬凑的。
        """
        if size.width() <= 0 or size.height() <= 0:
            return source

        return source.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )


def _to_qcolor(hex_color: str, alpha: int) -> QColor:
    color = QColor(hex_color)
    if not color.isValid():
        color = QColor(0, 0, 0)
    color.setAlpha(alpha)
    return color
