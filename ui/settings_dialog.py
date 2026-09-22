"""API 设置对话框：填写 Key、测连通性、保存到本地 config.json。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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

from core.api_client import DeepSeekClient
from core.config import KNOWN_MODELS, AppConfig
from core.paths import CONFIG_FILE
from ui import styles
from ui.workers import ApiTestThread


class ApiSettingsDialog(QDialog):
    """DeepSeek API 配置窗口。

    测试用的是「当前输入框里的值」，不要求先保存 ——
    免得用户为了验证一个 Key 得先把它写进磁盘。
    """

    def __init__(self, config: AppConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("API 设置")
        self.setModal(True)
        self.setMinimumWidth(560)

        self._config = config
        self._thread: ApiTestThread | None = None

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

        root.addLayout(form)

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

    def current_config(self) -> AppConfig:
        """用界面上的值构造一份配置，保留未展示的字段。"""
        self._config.api_key = self.key_edit.text().strip()
        self._config.base_url = (
            self.base_url_edit.text().strip() or "https://api.deepseek.com"
        )
        self._config.model = self.model_combo.currentText().strip() or "deepseek-chat"
        self._config.timeout = self.timeout_spin.value()
        return self._config

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

    def _run_test(self, mode: str) -> None:
        if self._thread is not None and self._thread.isRunning():
            return

        client = DeepSeekClient(
            api_key=self.key_edit.text().strip(),
            base_url=self.base_url_edit.text().strip(),
            model=self.model_combo.currentText().strip(),
            timeout=self.timeout_spin.value(),
        )

        self._set_testing(True)
        self._render_pending(mode)

        # 让 QThread 归本对话框管，对话框销毁时一并回收
        self._thread = ApiTestThread(client, mode, parent=self)
        self._thread.done.connect(self._on_test_done)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _on_test_done(self, result) -> None:
        self._set_testing(False)
        self._render_result(result)

    def _on_thread_finished(self) -> None:
        self._thread = None

    def _set_testing(self, testing: bool) -> None:
        self.test_button.setEnabled(not testing)
        self.test_full_button.setEnabled(not testing)
        self.save_button.setEnabled(not testing)

        if testing:
            self.test_button.setText("测试中…")
            self.cost_hint.setText("正在请求 api.deepseek.com …")
        else:
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
        """测试线程还在跑时不要强行销毁，等它结束。"""
        if self._thread is not None and self._thread.isRunning():
            self._thread.done.disconnect()
            self._thread.wait(3000)
        super().closeEvent(event)

    def reject(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.done.disconnect()
            self._thread.wait(3000)
        super().reject()
