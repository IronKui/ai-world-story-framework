# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

用法（在项目根目录执行）：
    .venv/Scripts/python.exe -m PyInstaller AIWorldStoryFramework.spec --noconfirm

几个关键选择：
  · onedir 而不是 onefile —— onefile 每次启动都要把上百 MB 解压到临时目录，
    PyQt6 的程序冷启动要十几秒，而且更容易被杀毒软件误报
  · windowed —— 不带控制台黑窗
  · 排除用不到的 Qt 模块 —— PyQt6 全量带上能到 200MB+，
    本程序只用到 Widgets / Gui / Core / Svg
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve()

#: 本程序实际用到的 Qt 模块之外，全部排除。
#: 注意别把 QtNetwork 排掉 —— 虽然我们用标准库 urllib 发请求，
#: 但 Qt 内部有依赖。
EXCLUDES = [
    # 体积最大的几个，完全用不到
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineQuick",
    "PyQt6.QtQuick",
    "PyQt6.QtQuick3D",
    "PyQt6.QtQuickWidgets",
    "PyQt6.QtQml",
    "PyQt6.QtQmlModels",
    # 3D / 图表 / 多媒体
    "PyQt6.Qt3DCore",
    "PyQt6.Qt3DRender",
    "PyQt6.Qt3DInput",
    "PyQt6.Qt3DLogic",
    "PyQt6.Qt3DAnimation",
    "PyQt6.Qt3DExtras",
    "PyQt6.QtCharts",
    "PyQt6.QtDataVisualization",
    "PyQt6.QtGraphs",
    "PyQt6.QtMultimedia",
    "PyQt6.QtMultimediaWidgets",
    "PyQt6.QtSpatialAudio",
    "PyQt6.QtTextToSpeech",
    # 硬件 / 系统集成
    "PyQt6.QtBluetooth",
    "PyQt6.QtNfc",
    "PyQt6.QtPositioning",
    "PyQt6.QtSerialPort",
    "PyQt6.QtSensors",
    "PyQt6.QtRemoteObjects",
    # 开发工具类
    "PyQt6.QtDesigner",
    "PyQt6.QtHelp",
    "PyQt6.QtTest",
    "PyQt6.QtUiTools",
    "PyQt6.QtSql",
    "PyQt6.QtScxml",
    "PyQt6.QtStateMachine",
    # 其它
    "PyQt6.QtPdf",
    "PyQt6.QtPdfWidgets",
    "PyQt6.QtWebChannel",
    "PyQt6.QtWebSockets",
    "PyQt6.QtHttpServer",
    "PyQt6.QtOpenGL",
    "PyQt6.QtOpenGLWidgets",
    # 第三方大件（本项目一个都不依赖）
    "numpy",
    "PIL",
    "matplotlib",
    "scipy",
    "pandas",
    "tkinter",
]

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    # 随包资源：示例世界观与提示词文档。
    # 打包后解压在 _MEIPASS 下，由 core/paths.py 的 RESOURCE_DIR 读取
    datas=[(str(ROOT / "assets"), "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AIWorldStoryFramework",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # 不用 UPX：压缩率有限，但会显著提高被杀毒软件误报的概率
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "app.ico") if (ROOT / "assets" / "app.ico").is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AIWorldStoryFramework",
)
