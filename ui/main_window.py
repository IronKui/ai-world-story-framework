"""主窗口：菜单栏 + 各面板拼装 + 状态栏。

阶段 1 只有界面和交互骨架，菜单项统一走 _todo() 占位，
后续阶段逐个把槽函数替换成真实实现。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.models import Item, WorldState
from ui import styles
from ui.action_panel import ActionPanel
from ui.inventory_panel import InventoryPanel
from ui.story_panel import StoryPanel
from ui.world_panel import WorldPanel


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("动态世界观文字游戏框架")
        self.resize(1360, 880)
        self.setMinimumSize(1080, 700)

        self._build_menubar()
        self._build_body()
        self._build_statusbar()
        self._connect_signals()

        # 阶段 1 演示数据：接入 AI 后由 core 层填充
        self._load_stage1_demo()

    # ------------------------------------------------------------------
    # 菜单栏
    # ------------------------------------------------------------------

    def _build_menubar(self) -> None:
        bar = self.menuBar()

        # ---------- 游戏 ----------
        game_menu = bar.addMenu("游戏")

        self.act_import_world = self._make_action(
            game_menu, "导入世界观文档…", "Ctrl+O", "阶段 3 实现"
        )
        game_menu.addSeparator()

        self.act_start = self._make_action(
            game_menu, "开始新游戏", "Ctrl+N", "阶段 4 实现"
        )
        self.act_load = self._make_action(
            game_menu, "读取存档…", "Ctrl+L", "阶段 4 实现"
        )
        self.act_save = self._make_action(
            game_menu, "保存存档…", "Ctrl+S", "阶段 4 实现"
        )
        game_menu.addSeparator()

        self.act_quit = QAction("退出", self)
        self.act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        self.act_quit.triggered.connect(self.close)
        game_menu.addAction(self.act_quit)

        # ---------- 设置 ----------
        settings_menu = bar.addMenu("设置")

        self.act_api = self._make_action(
            settings_menu, "API 设置…", "Ctrl+,", "阶段 2 实现"
        )
        settings_menu.addSeparator()

        self.act_debug_log = QAction("开启调试日志", self)
        self.act_debug_log.setCheckable(True)
        self.act_debug_log.setToolTip("记录每一次发给 AI 的 prompt 与返回结果")
        self.act_debug_log.toggled.connect(self._on_debug_log_toggled)
        settings_menu.addAction(self.act_debug_log)

        # ---------- 帮助 ----------
        help_menu = bar.addMenu("帮助")

        self.act_about = QAction("关于", self)
        self.act_about.triggered.connect(self._show_about)
        help_menu.addAction(self.act_about)

    def _make_action(
        self, menu, text: str, shortcut: str | None, todo_stage: str
    ) -> QAction:
        """创建一个尚未实现的菜单项，点击后提示所属阶段。"""
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.setStatusTip(todo_stage)
        action.triggered.connect(lambda: self._todo(text, todo_stage))
        menu.addAction(action)
        return action

    # ------------------------------------------------------------------
    # 窗体主体
    # ------------------------------------------------------------------

    def _build_body(self) -> None:
        root = QWidget()
        root.setObjectName("Root")

        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(0)

        # 左右两栏，用 splitter 让玩家自己调整比例
        outer = QSplitter(Qt.Orientation.Horizontal)
        outer.setChildrenCollapsible(False)

        # ---- 左栏：剧情 + 操作 ----
        self.story_panel = StoryPanel()
        self.action_panel = ActionPanel()

        left = QSplitter(Qt.Orientation.Vertical)
        left.setChildrenCollapsible(False)
        left.addWidget(self.story_panel)
        left.addWidget(self.action_panel)
        left.setStretchFactor(0, 3)
        left.setStretchFactor(1, 2)
        left.setSizes([460, 400])

        # ---- 右栏：世界状态 + 背包 ----
        self.world_panel = WorldPanel()
        self.inventory_panel = InventoryPanel()

        right = QSplitter(Qt.Orientation.Vertical)
        right.setChildrenCollapsible(False)
        right.addWidget(self.world_panel)
        right.addWidget(self.inventory_panel)
        right.setStretchFactor(0, 2)
        right.setStretchFactor(1, 3)
        right.setSizes([300, 560])

        outer.addWidget(left)
        outer.addWidget(right)
        outer.setStretchFactor(0, 3)
        outer.setStretchFactor(1, 2)
        outer.setSizes([880, 460])

        layout.addWidget(outer)
        self.setCentralWidget(root)

        # 应用 QTextBrowser 内部样式
        self.story_panel.apply_document_style()

    def _build_statusbar(self) -> None:
        bar = self.statusBar()

        self.status_world = QLabel("世界观：未导入")
        self.status_api = QLabel("API：未配置")
        self.status_mode = QLabel("就绪")

        for label in (self.status_world, self.status_api):
            label.setStyleSheet(
                f"color:{styles.COLORS['text_faint']}; font-size:12px;"
            )

        bar.addWidget(self.status_world)
        bar.addWidget(self._separator_label())
        bar.addWidget(self.status_api)
        bar.addPermanentWidget(self.status_mode)

    @staticmethod
    def _separator_label() -> QLabel:
        label = QLabel("│")
        label.setStyleSheet(f"color:{styles.COLORS['border']};")
        return label

    # ------------------------------------------------------------------
    # 信号连接
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self.action_panel.option_chosen.connect(self._on_option_chosen)
        self.action_panel.free_input_submitted.connect(self._on_free_input)
        self.action_panel.refresh_requested.connect(self._on_refresh_requested)

        self.inventory_panel.use_requested.connect(self._on_item_use)
        self.inventory_panel.drop_requested.connect(self._on_item_drop)

    # ---- 玩家操作：阶段 1 只做回显，阶段 9 接入主循环 ----

    def _on_option_chosen(self, text: str) -> None:
        self.story_panel.append_player_action(text)
        self._todo("行动结算", "阶段 9 实现（主循环）")

    def _on_free_input(self, text: str) -> None:
        self.story_panel.append_player_action(text)
        self._todo("自由行动结算", "阶段 9 实现（主循环）")

    def _on_refresh_requested(self) -> None:
        self._todo("重新生成行动选项", "阶段 8 实现（事件生成）")

    def _on_item_use(self, item_id: str) -> None:
        item = next((i for i in self.inventory_panel.items() if i.id == item_id), None)
        if item is None:
            return
        self.story_panel.append_system(f"使用道具：{item.name}")
        self._todo("道具效果结算", "阶段 7 实现（道具生成）")

    def _on_item_drop(self, item_id: str) -> None:
        item = next((i for i in self.inventory_panel.items() if i.id == item_id), None)
        if item is None:
            return

        answer = QMessageBox.question(
            self,
            "丢弃道具",
            f"确定丢弃「{item.name}」吗？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.inventory_panel.remove_item(item_id)
        self.story_panel.append_system(f"丢弃了：{item.name}")

    def _on_debug_log_toggled(self, enabled: bool) -> None:
        state = "开启" if enabled else "关闭"
        self.status_mode.setText(f"调试日志已{state}")
        self._todo(f"调试日志{state}", "阶段 10 实现（日志）")

    # ------------------------------------------------------------------
    # 占位与演示
    # ------------------------------------------------------------------

    def _todo(self, feature: str, stage: str) -> None:
        """尚未实现的入口统一提示，避免玩家以为程序坏了。"""
        QMessageBox.information(
            self,
            "尚未实现",
            f"「{feature}」将在 {stage}。\n\n"
            f"当前为阶段 1：仅完成界面框架，尚未接入 AI。",
        )

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "关于",
            "<b>动态世界观文字游戏框架</b><br><br>"
            "技术栈：Python + PyQt6<br>"
            "模型：DeepSeek（需自备 API Key）<br>"
            "存储：本地 JSON，不上传云端<br><br>"
            "<span style='color:#8b94a8'>当前进度：阶段 1 / 10 —— 窗体 UI 框架</span>",
        )

    def _load_stage1_demo(self) -> None:
        """阶段 1 演示内容，阶段 9 会被真实的 AI 开场替换。"""
        story = self.story_panel
        story.append_system("阶段 1：界面框架已就绪，尚未接入 AI")
        story.append_narrative(
            "程序已启动。你现在看到的是一套空的世界容器——没有预设地图、"
            "没有写死的道具表、也没有写死的事件脚本。\n\n"
            "导入一份世界观文档之后，世界才会真正开始运转。"
        )

        story.append_dialog(
            "系统", "「导入世界观文档」与「API 设置」将在后续阶段开放。"
        )

        self.action_panel.set_options(
            [
                "查看当前界面框架",
                "导入世界观文档（阶段 3）",
                "配置 DeepSeek API Key（阶段 2）",
            ]
        )

        self.world_panel.update_world(
            WorldState(
                location="未导入世界观",
                time="—",
                factions=[],
                flags=[],
            )
        )

        self.inventory_panel.set_items(
            [
                Item(
                    name="示例道具·锈蚀的钥匙",
                    rarity="普通",
                    category="杂物",
                    description="一把锈得几乎认不出齿形的铜钥匙。",
                    lore="用于验证背包面板的排版效果，阶段 7 起全部由 AI 生成。",
                    effect="无实际效果（演示用）",
                    source="阶段 1 演示数据",
                ),
                Item(
                    name="示例道具·残页手札",
                    rarity="稀有",
                    category="文献",
                    description="半张被水浸过的羊皮纸，字迹尚有三分之一可辨。",
                    lore="同样是演示数据，读档时不会被写入存档。",
                    effect="无实际效果（演示用）",
                    source="阶段 1 演示数据",
                ),
            ]
        )

        self.story_panel.append_system("提示：点击左侧选项或直接输入文字，试试面板交互")
