"""API 设置对话框：填写 Key、测连通性、保存到本地 config.json。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.api_client import DeepSeekClient, TestResult
from core.config import (
    KNOWN_MODELS,
    PRICING_AUTO,
    PRICING_OFF_PEAK,
    PRICING_PEAK,
    THINKING_DISABLED,
    THINKING_ENABLED,
    AppConfig,
)
from core.paths import CONFIG_FILE
from core.pricing import cost_from_usage
from core.usage import UsageTracker
from ui import styles
from ui.workers import ApiTestThread, ChatStreamThread


class ApiSettingsDialog(QDialog):
    """DeepSeek API 配置窗口。

    测试用的是「当前输入框里的值」，不要求先保存 ——
    免得用户为了验证一个 Key 得先把它写进磁盘。
    """

    def __init__(
        self,
        config: AppConfig,
        tracker: UsageTracker | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("API 设置")
        self.setModal(True)
        self.setMinimumWidth(620)

        self._config = config
        #: 完整测试会真实消耗 token，理应计入统计
        self._tracker = tracker
        #: 本次测试产生的用量记录，供调用方刷新界面
        self.usage_recorded = None
        self._thread: ApiTestThread | None = None
        self._chat_thread: ChatStreamThread | None = None
        #: 流式测试收到的文本，用于结束时算 token 与显示
        self._stream_text = ""

        self._build_ui()
        self._load_config(config)

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        # ---------- API Key ----------
        key_row = QHBoxLayout()
        key_row.setSpacing(8)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-…")
        key_row.addWidget(self.key_edit, 1)

        self.show_key_check = QCheckBox("显示")
        self.show_key_check.toggled.connect(self._toggle_key_visibility)
        key_row.addWidget(self.show_key_check)

        form.addRow(self._label("DeepSeek API Key"), self._wrap(key_row))

        # ---------- Base URL ----------
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.deepseek.com")
        form.addRow(self._label("接口地址"), self.base_url_edit)

        # ---------- 模型 + 超时 ----------
        model_row = QHBoxLayout()
        model_row.setSpacing(8)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)  # 允许手填新模型名
        self.model_combo.addItems(KNOWN_MODELS)
        model_row.addWidget(self.model_combo, 1)

        model_row.addWidget(self._hint("超时"))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 600)
        self.timeout_spin.setSuffix(" 秒")
        # 宽度要同时容下「600 秒」、左右内边距和原生上下按钮
        self.timeout_spin.setFixedWidth(120)
        model_row.addWidget(self.timeout_spin)

        form.addRow(self._label("模型"), self._wrap(model_row))

        # ---------- 思考模式 / 计价 ----------
        advanced_row = QHBoxLayout()
        advanced_row.setSpacing(8)

        self.thinking_check = QCheckBox("启用深度思考")
        self.thinking_check.setToolTip(
            "默认关闭。实测同一句提示词，开启时输出 17 token、关闭后只需 1 token，\n"
            "而推理 token 按输出价计费，对生成剧情文本没有明显收益。"
        )
        advanced_row.addWidget(self.thinking_check)

        advanced_row.addStretch(1)
        advanced_row.addWidget(self._hint("计价"))

        self.pricing_combo = QComboBox()
        self.pricing_combo.addItem("自动判断峰谷", PRICING_AUTO)
        self.pricing_combo.addItem("固定按高峰价", PRICING_PEAK)
        self.pricing_combo.addItem("固定按低谷价", PRICING_OFF_PEAK)
        self.pricing_combo.setToolTip(
            "DeepSeek 峰谷计价：UTC 周一至周五 01:00-04:00 与 06:00-10:00 为高峰，\n"
            "低谷价为高峰价的一半，其余时间（含周末）均为低谷。"
        )
        self.pricing_combo.setFixedWidth(140)
        advanced_row.addWidget(self.pricing_combo)

        advanced_row.addWidget(self._hint("汇率"))
        self.rate_spin = QDoubleSpinBox()
        self.rate_spin.setRange(0.1, 100.0)
        self.rate_spin.setDecimals(2)
        self.rate_spin.setSingleStep(0.1)
        self.rate_spin.setPrefix("1$ = ¥")
        # 宽度要容下「1$ = ¥7.10」，再给原生上下按钮留位
        self.rate_spin.setFixedWidth(148)
        self.rate_spin.setToolTip("仅用于把美元花费换算成人民币展示，估算值")
        advanced_row.addWidget(self.rate_spin)

        form.addRow(self._label("生成选项"), self._wrap(advanced_row))

        root.addLayout(form)

        # ---------- 计费说明 ----------
        pricing_note = QLabel(
            "花费按官方公开价格估算，仅供参考。"
            "高峰期与法定节假日无法完全自动判定，实际以 DeepSeek 平台账单为准。"
        )
        pricing_note.setWordWrap(True)
        pricing_note.setStyleSheet(
            f"color:{styles.COLORS['text_faint']}; font-size:12px; line-height:150%;"
        )
        root.addWidget(pricing_note)

        # ---------- 存储位置说明 ----------
        storage_hint = QLabel(
            f"Key 仅保存在本地：{CONFIG_FILE}<br>"
            "不会随存档、世界观文件一起存放，也不会上传到任何服务器。"
        )
        storage_hint.setWordWrap(True)
        storage_hint.setStyleSheet(
            f"color:{styles.COLORS['text_faint']}; font-size:12px; line-height:150%;"
        )
        root.addWidget(storage_hint)

        # ---------- 测试按钮 ----------
        test_row = QHBoxLayout()
        test_row.setSpacing(8)

        self.test_button = QPushButton("测试连接")
        self.test_button.setToolTip(
            "拉取模型列表验证 Key 与网络，不消耗任何 token"
        )
        self.test_button.clicked.connect(lambda: self._run_test("connection"))
        test_row.addWidget(self.test_button)

        self.test_full_button = QPushButton("完整测试")
        self.test_full_button.setToolTip(
            "真实调用一次对话接口，验证模型可用；会消耗约 10~20 token"
        )
        self.test_full_button.clicked.connect(lambda: self._run_test("completion"))
        test_row.addWidget(self.test_full_button)

        test_row.addStretch(1)

        self.cost_hint = QLabel("「测试连接」不消耗 token")
        self.cost_hint.setStyleSheet(
            f"color:{styles.COLORS['text_faint']}; font-size:12px;"
        )
        test_row.addWidget(self.cost_hint)

        root.addLayout(test_row)

        # ---------- 结果区 ----------
        self.result_view = QTextBrowser()
        self.result_view.setObjectName("StoryView")
        # 用最小高度而不是固定高度：成功态文案较长，
        # 写死高度会把「可用模型」那几行截掉
        self.result_view.setMinimumHeight(175)
        self.result_view.setPlaceholderText("测试结果会显示在这里")
        root.addWidget(self.result_view)

        # ---------- 底部按钮 ----------
        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton(
            "保存", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.save_button.setObjectName("PrimaryButton")
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------------------------------------------
    # 载入 / 保存
    # ------------------------------------------------------------------

    def _load_config(self, config: AppConfig) -> None:
        self.key_edit.setText(config.api_key)
        self.base_url_edit.setText(config.base_url)
        self.model_combo.setCurrentText(config.model)
        self.timeout_spin.setValue(config.timeout)

        self.thinking_check.setChecked(config.thinking_enabled)

        index = self.pricing_combo.findData(config.pricing_mode)
        self.pricing_combo.setCurrentIndex(index if index >= 0 else 0)
        self.rate_spin.setValue(config.usd_to_cny)

    def current_config(self) -> AppConfig:
        """用界面上的值构造一份配置，保留未展示的字段。"""
        self._config.api_key = self.key_edit.text().strip()
        self._config.base_url = (
            self.base_url_edit.text().strip() or "https://api.deepseek.com"
        )
        self._config.model = self.model_combo.currentText().strip() or "deepseek-flash"
        self._config.timeout = self.timeout_spin.value()
        self._config.thinking_mode = (
            THINKING_ENABLED if self.thinking_check.isChecked() else THINKING_DISABLED
        )
        self._config.pricing_mode = self.pricing_combo.currentData() or PRICING_AUTO
        self._config.usd_to_cny = self.rate_spin.value()
        return self._config

    def _make_client(self) -> DeepSeekClient:
        """用界面当前值（而不是已保存值）构造客户端，便于先测后存。"""
        return DeepSeekClient(
            api_key=self.key_edit.text().strip(),
            base_url=self.base_url_edit.text().strip(),
            model=self.model_combo.currentText().strip(),
            timeout=self.timeout_spin.value(),
        )

    def _on_save(self) -> None:
        if not self.key_edit.text().strip():
            answer = QMessageBox.question(
                self,
                "尚未填写 API Key",
                "API Key 为空，保存后程序无法调用 AI。\n\n仍要保存吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.current_config()
        self.accept()

    # ------------------------------------------------------------------
    # 测试
    # ------------------------------------------------------------------

    def _busy(self) -> bool:
        return (
            (self._thread is not None and self._thread.isRunning())
            or (self._chat_thread is not None and self._chat_thread.isRunning())
        )

    def _run_test(self, mode: str) -> None:
        if self._busy():
            return

        self._set_testing(True)

        if mode == "completion":
            self._start_stream_test()
            return

        self._render_pending(mode)
        # 让 QThread 归本对话框管，对话框销毁时一并回收
        self._thread = ApiTestThread(self._make_client(), mode, parent=self)
        self._thread.done.connect(self._on_test_done)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    # ---- 完整测试：走真实流式对话，边生成边显示 ----

    def _start_stream_test(self) -> None:
        self._stream_text = ""
        self.result_view.setHtml(
            f'<div style="color:{styles.COLORS["text_faint"]};padding:6px;">'
            "正在请求模型并流式返回，请稍候…</div>"
            '<div id="body" style="padding:6px;"></div>'
        )

        self._chat_thread = ChatStreamThread(
            self._make_client(),
            [
                {
                    "role": "user",
                    "content": (
                        "请用一句话（不超过 40 字）描写一座终年被火山灰覆盖的城市。"
                    ),
                }
            ],
            reason="连接测试",
            thinking=self.thinking_check.isChecked(),
            max_tokens=200,
            parent=self,
        )
        self._chat_thread.delta.connect(self._on_stream_delta)
        self._chat_thread.done.connect(self._on_chat_done)
        self._chat_thread.failed.connect(self._on_chat_failed)
        self._chat_thread.finished.connect(self._on_chat_thread_finished)
        self._chat_thread.start()

    def _on_stream_delta(self, piece: str) -> None:
        """逐片段追加到结果区末尾。"""
        if not self._stream_text:
            # 第一个片段到达，清掉「正在请求…」的占位文字
            self.result_view.clear()

        self._stream_text += piece
        cursor = self.result_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(piece)
        self.result_view.setTextCursor(cursor)

    def _on_chat_done(self, result) -> None:
        self._set_testing(False)

        c = styles.COLORS
        peak = self._forced_peak()
        cost = cost_from_usage(result.model, result.usage, peak=peak)
        usage = result.usage or {}

        # 真实消耗了 token，计入统计
        if self._tracker is not None and not result.cancelled:
            self.usage_recorded = self._tracker.record(
                model=result.model,
                usage=result.usage,
                reason="连接测试",
                peak=peak,
            )

        lines = [
            f"耗时 {result.latency_ms} ms",
            f"输入 {usage.get('prompt_tokens', '?')} token"
            f"（缓存命中 {cost.cache_hit_tokens}）",
            f"输出 {usage.get('completion_tokens', '?')} token",
            f"本次花费 ${cost.total_usd:.6f}（约 ¥{cost.total_cny(self.rate_spin.value()):.4f}）",
            f"计价档位：{'高峰' if cost.peak else '低谷'}",
        ]
        if result.finish_reason != "stop":
            lines.append(f"⚠ {result.finish_note}")

        footer = (
            f'<div style="margin-top:12px;padding-top:10px;'
            f'border-top:1px solid {c["border_soft"]};'
            f'color:{c["text_faint"]};font-size:12px;line-height:165%;">'
            + "<br>".join(lines)
            + "</div>"
        )

        cursor = self.result_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(footer)

        self.cost_hint.setText("本次调用的 token 与花费已记入统计")

    def _on_chat_failed(self, error) -> None:
        self._set_testing(False)
        self._render_result(
            TestResult(
                ok=False,
                title="调用失败",
                message=error.message,
                detail=error.detail,
            )
        )

    def _forced_peak(self) -> bool | None:
        mode = self.pricing_combo.currentData()
        if mode == PRICING_PEAK:
            return True
        if mode == PRICING_OFF_PEAK:
            return False
        return None

    # ---- 共用状态 ----

    def _on_test_done(self, result) -> None:
        self._set_testing(False)
        self._render_result(result)

    def _on_thread_finished(self) -> None:
        self._thread = None

    def _on_chat_thread_finished(self) -> None:
        self._chat_thread = None

    def _set_testing(self, testing: bool) -> None:
        self.test_button.setEnabled(not testing)
        self.test_full_button.setEnabled(not testing)
        self.save_button.setEnabled(not testing)

        if testing:
            self.test_full_button.setText("生成中…")
            self.test_button.setText("测试中…")
            self.cost_hint.setText("正在请求 api.deepseek.com …")
        else:
            self.test_full_button.setText("完整测试")
            self.test_button.setText("测试连接")
            self.cost_hint.setText("「测试连接」不消耗 token")

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------

    def _render_pending(self, mode: str) -> None:
        what = "真实调用对话接口" if mode == "completion" else "拉取模型列表"
        self.result_view.setHtml(
            f'<div style="color:{styles.COLORS["text_dim"]};padding:6px;">'
            f"正在{what}，请稍候…</div>"
        )

    def _render_result(self, result) -> None:
        c = styles.COLORS
        color = c["success"] if result.ok else c["danger"]
        icon = "✓" if result.ok else "✕"

        latency = (
            f'<span style="color:{c["text_faint"]};font-size:12px;">'
            f"耗时 {result.latency_ms} ms</span>"
            if result.latency_ms
            else ""
        )
        cost = (
            f'<span style="color:{c["text_faint"]};font-size:12px;">'
            f"　{result.cost_hint}</span>"
            if result.cost_hint
            else ""
        )

        # 换行转义成 <br>，同时转义 HTML
        message = (
            str(result.message)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )

        html = f"""
        <div style="padding:6px;">
          <div style="color:{color};font-size:14px;font-weight:600;margin-bottom:8px;">
            {icon} {result.title}
          </div>
          <div style="color:{c['text']};font-size:13px;line-height:165%;">{message}</div>
          <div style="margin-top:10px;">{latency}{cost}</div>
        </div>
        """
        self.result_view.setHtml(html)

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"color:{styles.COLORS['text_dim']}; font-size:13px;"
        )
        return label

    @staticmethod
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"color:{styles.COLORS['text_faint']}; font-size:12.5px;"
        )
        return label

    @staticmethod
    def _wrap(layout) -> QWidget:
        host = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        host.setLayout(layout)
        return host

    def _toggle_key_visibility(self, shown: bool) -> None:
        self.key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
        )

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 接口
        """有线程还在跑时不要强行销毁，先取消再等它结束。"""
        self._stop_threads()
        super().closeEvent(event)

    def reject(self) -> None:
        self._stop_threads()
        super().reject()

    def accept(self) -> None:
        self._stop_threads()
        super().accept()

    def _stop_threads(self) -> None:
        """取消并回收测试线程。

        流式线程要显式 cancel()，否则它会一直读到流结束；
        wait() 给足时间让 run() 自己退出，避免 Qt 报线程销毁警告。
        """
        if self._thread is not None and self._thread.isRunning():
            try:
                self._thread.done.disconnect()
            except TypeError:
                pass  # 已经断开过
            self._thread.wait(3000)

        if self._chat_thread is not None and self._chat_thread.isRunning():
            self._chat_thread.cancel()
            for signal in (self._chat_thread.delta, self._chat_thread.done,
                           self._chat_thread.failed):
                try:
                    signal.disconnect()
                except TypeError:
                    pass
            self._chat_thread.wait(3000)
