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

from core.config import AppConfig, ConfigStore
from core.models import Item, WorldState
from core.world import DEFAULT_CONTEXT_BUDGET, WorldDocument, WorldStore
from ui import styles
from ui.action_panel import ActionPanel
from ui.inventory_panel import InventoryPanel
from ui.settings_dialog import ApiSettingsDialog
from ui.story_panel import StoryPanel
from ui.world_doc_dialog import WorldDocDialog
from ui.world_panel import WorldPanel


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("动态世界观文字游戏框架")
        self.resize(1360, 880)
        self.setMinimumSize(1080, 700)

        # 配置要在建菜单栏之前读：调试日志勾选项的初始状态依赖它
        self._config_store = ConfigStore()
        self._config: AppConfig = self._config_store.load()
        self._world_store = WorldStore()
        #: 当前生效的世界观文档，导入或读档后填充
        self._world: WorldDocument | None = None

        self._build_menubar()
        self._build_body()
        self._build_statusbar()
        self._connect_signals()
        self._refresh_status()

        # 演示数据：接入 AI 后由 core 层填充
        self._load_demo_content()

    # ------------------------------------------------------------------
    # 菜单栏
    # ------------------------------------------------------------------

    def _build_menubar(self) -> None:
        bar = self.menuBar()

        # ---------- 游戏 ----------
        game_menu = bar.addMenu("游戏")

        self.act_world = QAction("世界观文档…", self)
        self.act_world.setShortcut(QKeySequence("Ctrl+O"))
        self.act_world.setStatusTip("导入、预览或切换世界观文档")
        self.act_world.triggered.connect(self._on_world_docs)
        game_menu.addAction(self.act_world)

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

        self.act_api = QAction("API 设置…", self)
        self.act_api.setShortcut(QKeySequence("Ctrl+,"))
        self.act_api.setStatusTip("配置 DeepSeek API Key 与模型")
        self.act_api.triggered.connect(self._on_api_settings)
        settings_menu.addAction(self.act_api)

        settings_menu.addSeparator()

        self.act_debug_log = QAction("开启调试日志", self)
        self.act_debug_log.setCheckable(True)
        self.act_debug_log.setChecked(self._config.debug_log)
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

        # 阶段 3 临时路由：让引导选项直接跳到对应入口。
        # 阶段 9 主循环接管后，这里统一改成把选项发给 AI 结算。
        handler = self._route_option(text)
        if handler is not None:
            handler()
            return

        self._todo("行动结算", "阶段 9 实现（主循环）")

    def _route_option(self, text: str):
        """把引导性质的选项映射到对应槽函数，普通行动返回 None。"""
        if "世界观" in text:
            return self._on_world_docs
        if "API" in text:
            return self._on_api_settings
        return None

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

    # ---- 设置 ----

    def _on_api_settings(self) -> None:
        dialog = ApiSettingsDialog(self._config, self)
        if dialog.exec() != ApiSettingsDialog.DialogCode.Accepted:
            return

        config = dialog.current_config()
        try:
            self._config_store.save(config)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "保存失败",
                f"无法写入配置文件：\n{exc}\n\n本次修改仅对当前运行有效。",
            )
        else:
            self.story_panel.append_system("API 设置已保存到本地配置文件")

        self._config = config
        self._refresh_status()

    def _on_debug_log_toggled(self, enabled: bool) -> None:
        self._config.debug_log = enabled
        try:
            self._config_store.save(self._config)
        except OSError as exc:
            QMessageBox.warning(
                self, "保存失败", f"无法写入配置文件：\n{exc}"
            )

        state = "开启" if enabled else "关闭"
        self.status_mode.setText(f"调试日志已{state}")
        if enabled:
            self.story_panel.append_system(
                "调试日志已开启（日志记录功能将在阶段 10 写入文件）"
            )

    # ---- 世界观 ----

    def _on_world_docs(self) -> None:
        dialog = WorldDocDialog(self._world, self)
        dialog.exec()

        if dialog.current_deleted:
            self._set_world(None)
            self.story_panel.append_system("当前世界观已被删除，请重新导入或选择")
            return

        chosen = dialog.chosen_world()
        if chosen is not None and (
            self._world is None or chosen.checksum != self._world.checksum
        ):
            self._set_world(chosen)

    def _set_world(self, document: WorldDocument | None) -> None:
        """切换当前世界观，并同步状态栏与剧情区提示。"""
        self._world = document
        self._refresh_status()

        if document is None:
            self.story_panel.append_system("当前没有生效的世界观文档")
            self._refresh_options()
            return

        context_text, compressed = document.context_text()
        self.story_panel.append_system(f"已加载世界观：《{document.name}》")

        note = ""
        if compressed:
            note = (
                f"\n文档共 {document.char_count} 字，超出上下文预算，"
                f"送入 AI 时按章节结构压缩为 {len(context_text)} 字（标题全部保留）。"
            )

        self.story_panel.append_narrative(
            f"世界规则已载入：《{document.name}》。\n"
            f"接下来的场景、道具、事件与 NPC 都由 AI 依据这份设定实时生成，"
            f"框架本身不含任何预设内容。{note}"
        )
        self._refresh_options()

    def _refresh_options(self) -> None:
        """按当前就绪状态给出一批引导选项。"""
        if self._world is None:
            self.action_panel.set_options(
                [
                    "打开「世界观文档」导入一份设定",
                    "打开 API 设置，配置 DeepSeek Key",
                ]
            )
            return

        if not self._config.has_api_key:
            self.action_panel.set_options(
                [
                    "当前世界观已就绪，去配置 API Key",
                    "查看当前世界观文档",
                ]
            )
            return

        self.action_panel.set_options(
            [
                "环顾四周，看看这里有什么",
                "查看当前世界观文档",
                "开始正式游玩（阶段 9）",
            ]
        )

    def _refresh_status(self) -> None:
        """刷新状态栏的「世界观 / API」两段状态。"""
        if self._world is None:
            self.status_world.setText("世界观：未导入")
            self.status_world.setStyleSheet(
                f"color:{styles.COLORS['warning']}; font-size:12px;"
            )
            self.status_world.setToolTip("点击菜单「游戏 → 世界观文档」导入 .txt / .md")
        else:
            self.status_world.setText(f"世界观：{self._world.name}")
            self.status_world.setStyleSheet(
                f"color:{styles.COLORS['success']}; font-size:12px;"
            )
            context_text, compressed = self._world.context_text()
            self.status_world.setToolTip(
                f"原文 {self._world.char_count} 字\n"
                f"送入 AI {len(context_text)} 字"
                f"{'（已压缩）' if compressed else '（完整）'}\n"
                f"预算 {DEFAULT_CONTEXT_BUDGET} 字"
            )

        if self._config.has_api_key:
            self.status_api.setText(f"API：已配置（{self._config.masked_key()}）")
            self.status_api.setStyleSheet(
                f"color:{styles.COLORS['success']}; font-size:12px;"
            )
            self.status_api.setToolTip(
                f"接口地址：{self._config.normalized_base_url()}\n"
                f"模型：{self._config.model}"
            )
        else:
            self.status_api.setText("API：未配置")
            self.status_api.setStyleSheet(
                f"color:{styles.COLORS['warning']}; font-size:12px;"
            )
            self.status_api.setToolTip("点击菜单「设置 → API 设置」填入 DeepSeek API Key")

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

    def _load_demo_content(self) -> None:
        """演示内容，阶段 9 会被真实的 AI 开场替换。"""
        story = self.story_panel
        story.append_system("界面框架与本地配置已就绪")
        story.append_narrative(
            "程序已启动。你现在看到的是一套空的世界容器——没有预设地图、"
            "没有写死的道具表、也没有写死的事件脚本。\n\n"
            "导入一份世界观文档之后，世界才会真正开始运转。"
        )

        if self._config.has_api_key:
            story.append_dialog(
                "系统",
                f"已读取本地配置，API Key（{self._config.masked_key()}）就绪。"
                "可在「设置 → API 设置」中测试连通性。",
            )
        else:
            story.append_dialog(
                "系统",
                "尚未配置 DeepSeek API Key。请打开「设置 → API 设置」填入后再继续。",
            )

        self._refresh_options()

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
