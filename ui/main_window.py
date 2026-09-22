"""主窗口：菜单栏 + 各面板拼装 + 状态栏 + 游戏主循环。

点击选项与自由文本输入最终都汇入 _begin_turn()，
该回合在后台线程里跑完「生成事件 → 落状态 → 生成道具 → 折叠历史」，
再回到主线程刷新界面。
"""

from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
)

from core.api_client import DeepSeekClient
from core.config import AppConfig, ConfigStore
from core.game import OPENING_ACTION
from core.debuglog import LOG
from core.models import WorldState
from core.savegame import (
    GameSave,
    HistoryLog,
    PlayerState,
    SaveStore,
)
from core.usage import UsageTracker
from core.validator import ValidationExhausted
from core.world import DEFAULT_CONTEXT_BUDGET, WorldDocument, WorldStore
from ui import styles
from ui.action_panel import ActionPanel
from ui.appearance_dialog import AppearanceDialog
from ui.backdrop import Backdrop
from ui.event_dialog import EventDialog
from ui.inventory_panel import InventoryPanel
from ui.item_gen_dialog import ItemGenDialog
from ui.log_dialog import LogDialog
from ui.save_dialog import MODE_LOAD, MODE_SAVE, SaveDialog
from ui.settings_dialog import ApiSettingsDialog
from ui import shellutils
from ui.story_panel import StoryPanel
from ui.usage_dialog import UsageDialog
from ui.validate_dialog import ValidateDialog
from ui.workers import TurnThread
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
        self._save_store = SaveStore()
        self._usage = UsageTracker()
        self._usage.load()
        # 让全局日志器的开关与配置保持一致
        LOG.set_enabled(self._config.debug_log)
        #: 当前生效的世界观文档，导入或读档后填充
        self._world: WorldDocument | None = None
        #: 玩家信息与历史摘要，构成存档的核心内容
        self._player = PlayerState()
        self._history = HistoryLog()
        self._world_state = WorldState()
        self._turns = 0
        #: 当前正在跑的回合线程，同一时间只允许一个
        self._thread: TurnThread | None = None

        self._build_menubar()
        self._build_body()
        self._build_statusbar()
        self._apply_background()
        self._connect_signals()
        self._refresh_status()
        self._refresh_usage_label()

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

        self.act_start = QAction("开始新游戏", self)
        self.act_start.setShortcut(QKeySequence("Ctrl+N"))
        self.act_start.setStatusTip("清空当前进度，从头开始")
        self.act_start.triggered.connect(self._on_new_game)
        game_menu.addAction(self.act_start)

        self.act_load = QAction("读取存档…", self)
        self.act_load.setShortcut(QKeySequence("Ctrl+L"))
        self.act_load.triggered.connect(self._on_load_game)
        game_menu.addAction(self.act_load)

        self.act_save = QAction("保存存档…", self)
        self.act_save.setShortcut(QKeySequence("Ctrl+S"))
        self.act_save.triggered.connect(self._on_save_game)
        game_menu.addAction(self.act_save)

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

        self.act_appearance = QAction("外观设置…", self)
        self.act_appearance.setStatusTip("配色方案与自定义背景")
        self.act_appearance.triggered.connect(self._on_appearance)
        settings_menu.addAction(self.act_appearance)

        settings_menu.addSeparator()

        self.act_debug_log = QAction("开启调试日志", self)
        self.act_debug_log.setCheckable(True)
        self.act_debug_log.setChecked(self._config.debug_log)
        self.act_debug_log.setToolTip("记录每一次发给 AI 的 prompt 与返回结果")
        self.act_debug_log.toggled.connect(self._on_debug_log_toggled)
        settings_menu.addAction(self.act_debug_log)

        self.act_usage = QAction("用量与花费…", self)
        self.act_usage.setStatusTip("查看 token 消耗与估算花费")
        self.act_usage.triggered.connect(self._on_usage)
        settings_menu.addAction(self.act_usage)

        # ---------- 工具 ----------
        tools_menu = bar.addMenu("工具")

        self.act_validate = QAction("世界观一致性校验…", self)
        self.act_validate.setStatusTip(
            "测试 AI 生成的内容是否会因违背世界观而被拦截并重试"
        )
        self.act_validate.triggered.connect(self._on_validate)
        tools_menu.addAction(self.act_validate)

        self.act_gen_items = QAction("生成道具…", self)
        self.act_gen_items.setStatusTip("让 AI 依据世界观现场生成道具")
        self.act_gen_items.triggered.connect(self._on_generate_items)
        tools_menu.addAction(self.act_gen_items)

        self.act_gen_event = QAction("生成事件…", self)
        self.act_gen_event.setStatusTip(
            "让 AI 结算一次玩家操作，生成事件、NPC 与行动选项"
        )
        self.act_gen_event.triggered.connect(self._on_generate_event)
        tools_menu.addAction(self.act_gen_event)

        tools_menu.addSeparator()

        self.act_log_viewer = QAction("调试日志…", self)
        self.act_log_viewer.setStatusTip(
            "查看发给 AI 的 prompt、返回内容、校验冲突与网络异常"
        )
        self.act_log_viewer.triggered.connect(self._on_log_viewer)
        tools_menu.addAction(self.act_log_viewer)

        tools_menu.addSeparator()

        self.act_open_data = QAction("打开数据目录…", self)
        self.act_open_data.setStatusTip(
            "打开存放配置、存档、世界观与日志的目录"
        )
        self.act_open_data.triggered.connect(self._on_open_data_dir)
        tools_menu.addAction(self.act_open_data)

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
        # 用 Backdrop 而不是普通 QWidget：设了背景图时要自己缩放绘制，
        # QSS 的 background-image 不会缩放，尺寸对不上会很难看
        root = Backdrop()
        self._backdrop = root

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
        self.status_usage = QLabel("本次花费：—")
        self.status_mode = QLabel("就绪")

        for label in (self.status_world, self.status_api, self.status_usage):
            label.setStyleSheet(
                f"color:{styles.COLORS['text_faint']}; font-size:12px;"
            )

        bar.addWidget(self.status_world)
        bar.addWidget(self._separator_label())
        bar.addWidget(self.status_api)
        bar.addPermanentWidget(self.status_usage)
        bar.addPermanentWidget(self._separator_label())
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
        self.inventory_panel.generate_requested.connect(self._on_generate_items)

    # ---- 玩家操作：两种交互方式汇入同一条主循环 ----

    def _on_option_chosen(self, text: str) -> None:
        """点击选项。"""
        # 未开局时的引导选项先走路由，不消耗 AI 调用
        handler = self._route_option(text)
        if handler is not None:
            self.story_panel.append_player_action(text)
            handler()
            return

        self._begin_turn(text)

    def _on_free_input(self, text: str) -> None:
        """自由输入文本。与点击选项走完全相同的流程。"""
        self._begin_turn(text)

    def _on_refresh_requested(self) -> None:
        """换一批选项：以「重新观察」为动作再结算一回合。"""
        if self._thread is not None and self._thread.isRunning():
            return
        self._begin_turn("我停下来，重新观察周围的情况。")

    def _route_option(self, text: str):
        """把引导性质的选项映射到对应槽函数，普通行动返回 None。"""
        if "世界观" in text:
            return self._on_world_docs
        if "API" in text:
            return self._on_api_settings
        if "保存" in text and "进度" in text:
            return self._on_save_game
        if "开始新的旅程" in text or "生成开场" in text:
            return self._on_new_game
        return None

    # ---- 主循环 ----

    def _game_ready(self) -> bool:
        """开局条件检查。不满足时给出明确指引，而不是静默失败。"""
        if self._world is None:
            QMessageBox.information(
                self,
                "尚未导入世界观",
                "这个框架不含任何预设剧情，一切都由 AI 依据世界观生成。\n\n"
                "请先在「游戏 → 世界观文档」中导入一份 .txt 或 .md 设定。",
            )
            return False

        if not self._config.has_api_key:
            QMessageBox.warning(
                self,
                "尚未配置 API Key",
                "游玩需要调用 DeepSeek 接口。\n\n"
                "请先在「设置 → API 设置」中填入 API Key 并测试连通性。",
            )
            return False

        return True

    def _make_client(self) -> DeepSeekClient:
        return DeepSeekClient(
            api_key=self._config.api_key,
            base_url=self._config.normalized_base_url(),
            model=self._config.model,
            timeout=self._config.timeout,
        )

    def _begin_turn(self, action: str, *, is_opening: bool = False) -> None:
        """开始一个回合。点击选项与自由输入最终都汇到这里。"""
        if self._thread is not None and self._thread.isRunning():
            return
        if not self._game_ready():
            return

        if not is_opening:
            self.story_panel.append_player_action(action)

        self.action_panel.set_busy(True, "AI 正在生成内容，请稍候…")
        self.inventory_panel.set_actions_enabled(False)
        self.status_mode.setText("结算中…")

        self._thread = TurnThread(
            self._make_client(),
            self._world,
            action,
            player=self._player,
            state=self._world_state,
            history=self._history,
            inventory=self.inventory_panel.items(),
            max_retries=self._config.max_validate_retries,
            is_opening=is_opening,
            parent=self,
        )
        self._thread.progress.connect(self._on_turn_progress)
        self._thread.done.connect(self._on_turn_done)
        self._thread.failed.connect(self._on_turn_failed)
        self._thread.usage_ready.connect(self._on_usage_ready)
        self._thread.finished.connect(self._on_turn_finished)
        self._thread.start()

    def _begin_opening(self) -> None:
        """开场。复用主循环，动作由 framework 合成。"""
        if self._thread is not None and self._thread.isRunning():
            return
        if not self._game_ready():
            return

        self.action_panel.set_busy(True, "正在展开世界…")
        self.inventory_panel.set_actions_enabled(False)
        self.status_mode.setText("开场…")

        self._thread = TurnThread(
            self._make_client(),
            self._world,
            OPENING_ACTION,
            player=self._player,
            state=self._world_state,
            history=self._history,
            inventory=[],
            max_retries=self._config.max_validate_retries,
            is_opening=True,
            parent=self,
        )
        self._thread.progress.connect(self._on_turn_progress)
        self._thread.done.connect(self._on_turn_done)
        self._thread.failed.connect(self._on_turn_failed)
        self._thread.usage_ready.connect(self._on_usage_ready)
        self._thread.finished.connect(self._on_turn_finished)
        self._thread.start()

    def _on_turn_progress(self, message: str) -> None:
        self.status_mode.setText(message)

    def _on_usage_ready(self, model: str, usage: dict, reason: str) -> None:
        """在主线程记账 —— 一个回合可能来好几条（事件 / 校验 / 道具 / 摘要）。"""
        self._usage.record(
            model=model,
            usage=usage,
            reason=reason,
            peak=self._config.forced_peak(),
        )

    def _on_turn_finished(self) -> None:
        self._thread = None

    def _on_turn_done(self, result) -> None:
        """回合结束。渲染本身出错也必须先把界面恢复可交互状态，
        否则玩家会卡在「生成中」动不了。"""
        self._turns += 1
        try:
            self._render_turn(result)
        except Exception:  # noqa: BLE001
            LOG.exception(
                "界面", "渲染回合结果时出错，本回合内容可能显示不完整", sys.exc_info()[1]
            )
        finally:
            self._restore_after_turn()

        # 换上新一批行动选项
        self.action_panel.set_options(result.event.options)
        self.action_panel.focus_input()

    def _restore_after_turn(self) -> None:
        """把界面恢复到可交互状态。回合的每条退出路径都要经过这里。"""
        self._refresh_usage_label()
        self._refresh_status()
        self.action_panel.set_busy(False)
        self.inventory_panel.set_actions_enabled(True)
        self.status_mode.setText(f"第 {self._turns} 回合")

    def _on_turn_failed(self, error) -> None:
        self._restore_after_turn()
        self.status_mode.setText("已中断")

        if isinstance(error, ValidationExhausted):
            # 需求指定：连续 3 次校验失败弹窗提示玩家重新操作
            self.story_panel.append_system("本次生成未通过世界观校验，已放弃")
            message = f"当前 AI 无法生成符合世界观的内容，请重新进行操作。\n\n{error}"
        else:
            self.story_panel.append_system("本次操作未能完成")
            message = getattr(error, "message", str(error))

        QMessageBox.warning(self, "生成失败", message)

        # 把可用选项还给玩家，别让人卡在空面板上
        self._refresh_options()

    # ---- 回合渲染 ----

    def _render_turn(self, result) -> None:
        """把一个回合的结果铺到界面上。

        顺序与玩家阅读顺序一致：正文 → NPC → 状态变化 → 道具 → 毁灭推进。
        """
        event = result.event

        self.story_panel.append_narrative(event.narrative)

        if event.has_npc():
            self.story_panel.append_dialog(event.npc, event.npc_dialog or "……")

        self.world_panel.update_world(self._world_state)
        self.world_panel.update_player(self._player)

        # 状态变更用系统行提示，避免和叙事正文混在一起
        if result.change_notes:
            self.story_panel.append_system("　".join(result.change_notes))

        for item in result.items_gained:
            self.inventory_panel.upsert_item(item)
        if result.items_gained:
            names = "、".join(
                f"「{item.name}」（{item.rarity}）" for item in result.items_gained
            )
            self.story_panel.append_system(f"获得道具：{names}")

        # 毁灭进度推进是大事，必须让玩家看见
        if result.applied_doom_delta > 0:
            self.story_panel.append_system(
                f"毁灭进度推进 {result.applied_doom_delta} 级 → "
                f"{self._world_state.doom.progress_text()}"
            )

        # 重试与折叠只在调试日志里留痕，界面上不打扰玩家
        if result.retries_used:
            LOG.info("回合", f"本回合经过 {result.retries_used} 次重试后通过")
        if result.history_folded:
            LOG.info("回合", "本回合触发了一次历史摘要折叠")

    def _on_item_use(self, item_id: str) -> None:
        item = next((i for i in self.inventory_panel.items() if i.id == item_id), None)
        if item is None:
            return

        if item.quantity > 1:
            answer = QMessageBox.question(
                self,
                "使用道具",
                f"使用「{item.name}」？\n"
                f"当前持有 {item.quantity} 件，使用后剩余 {item.quantity - 1} 件。\n\n"
                f"效果：{item.effect or '（未标注）'}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            item.quantity -= 1
            self.inventory_panel.upsert_item(item)
        else:
            answer = QMessageBox.question(
                self,
                "使用道具",
                f"使用「{item.name}」？\n"
                f"这是最后一件，使用后将从背包中消失。\n\n"
                f"效果：{item.effect or '（未标注）'}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.inventory_panel.remove_item(item_id)

        self.story_panel.append_player_action(f"使用「{item.name}」")
        if item.effect:
            self.story_panel.append_narrative(item.effect)

        # 使用道具同样计入历史，后续 AI 生成时能看到
        self._history.record(f"使用了道具「{item.name}」")

    def _on_generate_event(self) -> None:
        if self._world is None:
            QMessageBox.information(
                self,
                "尚未导入世界观",
                "事件必须依据世界观生成，请先在「游戏 → 世界观文档」中导入一份。",
            )
            return

        if not self._config.has_api_key:
            QMessageBox.warning(
                self,
                "尚未配置 API Key",
                "生成事件需要调用 DeepSeek 接口，请先在「设置 → API 设置」中填入 Key。",
            )
            return

        dialog = EventDialog(
            self._config,
            self._world,
            player=self._player,
            state=self._world_state,
            inventory=self.inventory_panel.items(),
            history=self._history,
            tracker=self._usage,
            parent=self,
        )
        dialog.exec()
        self._refresh_usage_label()

        # 对话框里直接把变更应用到了 _world_state / _player 上，这里只需刷新界面
        self.world_panel.update_world(self._world_state)
        self.world_panel.update_player(self._player)
        self._refresh_status()

    def _on_generate_items(self) -> None:
        if self._world is None:
            QMessageBox.information(
                self,
                "尚未导入世界观",
                "道具必须依据世界观生成，请先在「游戏 → 世界观文档」中导入一份。",
            )
            return

        if not self._config.has_api_key:
            QMessageBox.warning(
                self,
                "尚未配置 API Key",
                "生成道具需要调用 DeepSeek 接口，请先在「设置 → API 设置」中填入 Key。",
            )
            return

        dialog = ItemGenDialog(
            self._config,
            self._world,
            player=self._player,
            state=self._world_state,
            inventory=self.inventory_panel.items(),
            history=self._history,
            tracker=self._usage,
            parent=self,
        )
        dialog.exec()
        self._refresh_usage_label()

        items = dialog.accepted_items
        if not items:
            return

        for item in items:
            self.inventory_panel.upsert_item(item)

        names = "、".join(f"「{item.name}」" for item in items)
        self.story_panel.append_system(f"获得道具：{names}")
        self._history.record(f"获得了道具：{names}")

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
        dialog = ApiSettingsDialog(self._config, tracker=self._usage, parent=self)
        accepted = dialog.exec() == ApiSettingsDialog.DialogCode.Accepted

        # 完整测试可能产生过调用，无论保存与否都要刷新花费显示
        if dialog.usage_recorded is not None:
            self._refresh_usage_label()

        if not accepted:
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

    def _on_appearance(self) -> None:
        dialog = AppearanceDialog(self._config, self)
        if dialog.exec() != AppearanceDialog.DialogCode.Accepted:
            return

        try:
            self._config_store.save(self._config)
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", f"无法写入配置文件：\n{exc}")
            return

        if not dialog.changed:
            return

        # 主题在启动时应用，这里只能提示重启
        answer = QMessageBox.question(
            self,
            "外观已保存",
            "配色与背景的改动需要重启程序后生效。\n\n现在重启吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._restart()

    def _restart(self) -> None:
        """重启程序。

        用 QProcess 启动一份全新实例再退出自己 —— 比在进程内重建界面可靠，
        重建界面会漏掉一堆初始化顺序上的坑。
        """
        import sys as _sys

        from PyQt6.QtCore import QProcess

        try:
            QProcess.startDetached(_sys.executable, _sys.argv)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self, "重启失败", f"请手动关闭并重新打开程序。\n\n{exc}"
            )
            return

        self.close()

    def _on_debug_log_toggled(self, enabled: bool) -> None:
        self._config.debug_log = enabled
        try:
            self._config_store.save(self._config)
        except OSError as exc:
            QMessageBox.warning(
                self, "保存失败", f"无法写入配置文件：\n{exc}"
            )

        LOG.set_enabled(enabled)

        state = "开启" if enabled else "关闭"
        self.status_mode.setText(f"调试日志已{state}")
        if enabled:
            self.story_panel.append_system(
                f"调试日志已开启，校验冲突与网络异常将记录到 {LOG.path}"
            )

    # ---- 世界观 ----

    def _on_world_docs(self) -> None:
        dialog = WorldDocDialog(self._world, self._config, self._usage, self)
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

    # ---- 存档 / 读档 ----

    def _snapshot(self) -> GameSave:
        """把当前界面状态收集成一份存档快照。

        刻意不含 API Key —— Key 属于程序配置，不属于游戏进度。
        slot 由 SaveDialog 写入前决定。
        """
        return GameSave(
            turns=self._turns,
            player=self._player,
            inventory=self.inventory_panel.items(),
            world_state=self._world_state,
            world=self._world,
            history=self._history,
        )

    def _has_progress(self) -> bool:
        return self._turns > 0 or not self._history.is_empty()

    def _on_save_game(self) -> None:
        dialog = SaveDialog(
            self._save_store, MODE_SAVE, snapshot=self._snapshot(), parent=self
        )
        if dialog.exec() != SaveDialog.DialogCode.Accepted:
            return

        slot = dialog.selected_slot()
        self.story_panel.append_system(f"进度已保存到存档 {slot}")
        self.status_mode.setText(f"已保存到存档 {slot}")

    def _on_load_game(self) -> None:
        dialog = SaveDialog(self._save_store, MODE_LOAD, parent=self)
        dialog.exec()

        save = dialog.loaded_save
        if save is None:
            return

        if self._has_progress():
            answer = QMessageBox.question(
                self,
                "读取存档",
                "读取存档会覆盖当前进度，尚未保存的变化将丢失。\n\n确定继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self._apply_save(save)

    def _apply_save(self, save: GameSave) -> None:
        """把存档恢复到界面与内存状态。"""
        self._turns = save.turns
        self._player = save.player
        self._history = save.history
        self._world_state = save.world_state
        self._world = save.world

        # 存档里带着世界观副本。如果本地仓库没有这份（比如换过机器），
        # 补写回去 —— 文件名带内容校验和，重复写入是幂等的。
        if save.world is not None:
            try:
                self._world_store.save(save.world)
            except OSError:
                pass  # 补写失败不影响本次游玩

        self.world_panel.update_player(save.player)
        self.world_panel.update_world(save.world_state)
        self.inventory_panel.set_items(save.inventory)

        self.story_panel.clear()
        self.story_panel.append_system(
            f"已读取存档 {save.slot}（保存于 {save.saved_at}，回合 {save.turns}）"
        )

        world_note = (
            f"世界观《{save.world.name}》" if save.world else "存档未绑定世界观"
        )
        self.story_panel.append_narrative(
            f"进度已恢复。{world_note}，"
            f"当前位于{save.world_state.location or '未知地点'}，"
            f"背包 {len(save.inventory)} 件道具。"
        )

        if save.history.summary:
            self.story_panel.append_dialog(
                "回忆", save.history.summary[:400]
            )

        self._refresh_status()
        self._refresh_options()
        self.status_mode.setText(f"已读取存档 {save.slot}")

    def _on_new_game(self) -> None:
        if self._has_progress():
            answer = QMessageBox.question(
                self,
                "开始新游戏",
                "当前进度将被清空，未保存的变化将丢失。\n\n确定开始新游戏吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        if not self._game_ready():
            return

        self._reset_progress()
        self.story_panel.clear()
        self.story_panel.append_system(
            f"新的旅程开始　·　世界观《{self._world.name}》"
        )

        # 开场也走主循环，只是动作是合成的
        self._begin_opening()

    def _reset_progress(self) -> None:
        self._turns = 0
        self._player = PlayerState()
        self._history = HistoryLog()
        self._world_state = WorldState()
        self.inventory_panel.set_items([])
        self.world_panel.update_player(self._player)
        self.world_panel.update_world(self._world_state)
        self._refresh_status()
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

        if self._turns > 0:
            # 已经在游玩中：选项由每回合的 AI 生成结果接管，
            # 这里只在回合失败时兜底
            options = ["重新观察周围的情况", "查看当前世界观文档"]
        else:
            options = [
                "开始新的旅程（生成开场）",
                "查看当前世界观文档",
            ]

        self.action_panel.set_options(options)

    def _on_usage(self) -> None:
        dialog = UsageDialog(self._usage, self._config.usd_to_cny, self)
        dialog.exec()
        self._refresh_usage_label()

    def _on_open_data_dir(self) -> None:
        """打开数据目录。

        打包成 exe 后数据在 %LOCALAPPDATA% 下，用户自己找不到，
        必须提供这个入口，否则存档没法备份、世界观没法手动放。
        """
        from core.paths import DATA_DIR, ensure_dirs

        ensure_dirs()
        ok, reason = shellutils.open_folder(DATA_DIR)
        if not ok:
            QMessageBox.warning(
                self,
                "无法打开目录",
                f"{DATA_DIR}\n\n{reason}\n\n你可以手动复制上面的路径。",
            )

    def _on_log_viewer(self) -> None:
        dialog = LogDialog(LOG, self)
        dialog.exec()

    def _on_validate(self) -> None:
        dialog = ValidateDialog(
            self._config,
            self._world,
            player=self._player,
            state=self._world_state,
            history=self._history,
            tracker=self._usage,
            parent=self,
        )
        dialog.exec()
        self._refresh_usage_label()

    def _refresh_usage_label(self) -> None:
        """状态栏的本次运行花费。只统计本次运行，避免历史数字干扰判断。"""
        session = self._usage.session
        usd_to_cny = self._config.usd_to_cny

        if session.calls == 0:
            self.status_usage.setText("本次花费：—")
            self.status_usage.setStyleSheet(
                f"color:{styles.COLORS['text_faint']}; font-size:12px;"
            )
            self.status_usage.setToolTip("尚未调用过 AI")
            return

        self.status_usage.setText(
            f"本次花费：¥{session.cny(usd_to_cny):.4f}"
        )
        self.status_usage.setStyleSheet(
            f"color:{styles.COLORS['text_dim']}; font-size:12px;"
        )
        self.status_usage.setToolTip(
            f"本次运行 {session.calls} 次调用，"
            f"{session.total_tokens:,} token\n"
            f"估算 ${session.cost_usd:.6f}（汇率 1$ = ¥{usd_to_cny:.2f}）\n"
            f"历史累计 {self._usage.lifetime.calls} 次，"
            f"${self._usage.lifetime.cost_usd:.6f}"
        )

    def _apply_background(self) -> None:
        """把配置里的背景图铺到根容器上。

        图加载失败不报错也不打断启动 —— 只是没有背景图而已，
        用户自己选的文件损坏了，不该让程序起不来。
        """
        path = self._config.background_image.strip()
        if not path:
            self._backdrop.set_background(None, styles.COLORS["bg_window"])
            return

        ok = self._backdrop.set_background(path, styles.COLORS["bg_window"])
        if not ok:
            LOG.warn("界面", f"背景图加载失败，已忽略：{path}")

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
            "<span style='color:#8b94a8'>界面框架 + 本地存储 + AI 动态生成</span>",
        )

    def _load_demo_content(self) -> None:
        """启动画面。

        这里刻意不放任何示例剧情或示例道具 ——
        框架不含预设内容，世界必须由玩家的世界观文档 + AI 生成。
        """
        story = self.story_panel
        story.append_system("框架已就绪")
        story.append_narrative(
            "这是一套空的世界容器：没有预设地图、没有写死的道具表、"
            "也没有写死的事件脚本。\n\n"
            "导入一份世界观文档并配置 API Key 之后，"
            "世界才会由 AI 依据你的设定开始运转。"
        )

        if self._config.has_api_key:
            story.append_dialog(
                "系统",
                f"已读取本地配置，API Key（{self._config.masked_key()}）就绪。",
            )
        else:
            story.append_dialog(
                "系统",
                "尚未配置 DeepSeek API Key。请打开「设置 → API 设置」填入后再继续。",
            )

        self._world_state = WorldState(
            location="未导入世界观", time="—", factions=[], flags=[]
        )
        self.world_panel.update_world(self._world_state)

        # 背包刻意留空：道具全部由 AI 动态生成，框架不含任何预设道具。
        self.inventory_panel.set_items([])

        self._refresh_options()
