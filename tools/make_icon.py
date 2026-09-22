"""生成应用图标。

图标是画出来的而不是找的图 —— 这样尺寸、配色都能跟着主题走，
也不用为一张图操心版权。

生成多尺寸 ICO：Windows 会在不同场合用不同尺寸
（任务栏 32、资源管理器 16/48、安装包 256），只给一个大图会被糊掉。

用法：
    .venv/Scripts/python.exe packaging/make_icon.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QBuffer, QByteArray, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import (  # noqa: E402
    QBrush,
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

#: ICO 里要包含的尺寸
SIZES = [16, 24, 32, 48, 64, 128, 256]

#: 配色跟着默认主题的深蓝走
BG_TOP = "#1c2130"
BG_BOTTOM = "#0d0f16"
RING = "#6c8cff"
CORE = "#a9bcff"


def draw_icon(size: int) -> QImage:
    """画一枚图标：圆角深色底 + 光环 + 内核。"""
    # 超采样再缩回去，边缘才平滑（直接在目标尺寸上画会毛糙）
    scale = 4
    big = size * scale

    image = QImage(big, big, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # ---- 圆角底 ----
    radius = big * 0.22
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, big, big), radius, radius)

    gradient = QLinearGradient(0, 0, big, big)
    gradient.setColorAt(0.0, QColor(BG_TOP))
    gradient.setColorAt(1.0, QColor(BG_BOTTOM))
    painter.fillPath(path, QBrush(gradient))

    # 一圈极细的内描边，让图标在深色背景上仍有边界
    border = QPen(QColor(255, 255, 255, 28))
    border.setWidthF(big * 0.012)
    painter.setPen(border)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    inset = big * 0.012
    painter.drawRoundedRect(
        QRectF(inset, inset, big - 2 * inset, big - 2 * inset), radius, radius
    )

    # ---- 光环 ----
    ring_pen = QPen(QColor(RING))
    ring_pen.setWidthF(big * 0.072)
    ring_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(ring_pen)

    margin = big * 0.255
    painter.drawEllipse(QRectF(margin, margin, big - 2 * margin, big - 2 * margin))

    # ---- 内核 ----
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(CORE))
    core = big * 0.175
    painter.drawEllipse(QRectF((big - core) / 2, (big - core) / 2, core, core))

    painter.end()

    return image.scaled(
        size,
        size,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def png_bytes(image: QImage) -> bytes:
    """把 QImage 编码成 PNG 字节。

    注意 QByteArray 必须先赋给变量再用 —— 写成 QBuffer(QByteArray())
    的话那个临时对象会被立刻回收，QBuffer 随即指向已释放的内存，
    直接段错误（没有异常、没有堆栈，退出码 139）。
    """
    array = QByteArray()
    buffer = QBuffer(array)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(array)


def write_ico(images: list[QImage], path: Path) -> None:
    """把多张 PNG 组装成 ICO。

    Windows Vista 以后 ICO 容器里可以直接放 PNG，
    不必再拼 BMP + 掩码，文件也小得多。
    """
    payloads = [png_bytes(image) for image in images]

    header = struct.pack("<HHH", 0, 1, len(images))  # 保留位, 类型=图标, 数量
    offset = len(header) + 16 * len(images)

    entries = bytearray()
    for image, payload in zip(images, payloads):
        # ICO 用 0 表示 256
        width = 0 if image.width() >= 256 else image.width()
        height = 0 if image.height() >= 256 else image.height()
        entries += struct.pack(
            "<BBBBHHII",
            width,
            height,
            0,      # 调色板颜色数（真彩色填 0）
            0,      # 保留位
            1,      # 色彩平面
            32,     # 位深
            len(payload),
            offset,
        )
        offset += len(payload)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(header)
        handle.write(bytes(entries))
        for payload in payloads:
            handle.write(payload)


def main() -> int:
    # QPainter 必须在有 QGuiApplication 之后才能用。
    # 少了这一句，Qt 会直接 abort 进程 —— 没有报错、没有堆栈，退出码 127。
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)

    output = ROOT / "assets" / "app.ico"

    images = [draw_icon(size) for size in SIZES]
    write_ico(images, output)

    # 顺带存一张大图，README 和安装包向导页都能用
    preview = ROOT / "assets" / "icon-preview.png"
    images[-1].save(str(preview))

    print(f"已生成 {output}（{output.stat().st_size / 1024:.1f} KB，{len(SIZES)} 个尺寸）")
    print(f"已生成 {preview}")
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
