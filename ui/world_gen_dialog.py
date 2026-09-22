"""用大白话生成世界观。

普通用户手里不会有一份符合本程序格式要求的世界观文档。
让他们自己写、或者拿提示词去外部 AI 生成再贴回来，
中间任何一步出错都会得到一份读不进来的文件。

这个窗口把整条链路收进程序内部：描述 → 生成 → 预览 → 不满意就继续改 → 使用。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.api_client import DeepSeekClient
from core.config import AppConfig
from core.world import WorldDocument, WorldStore
from core.worldgen import (
    DESCRIPTION_EXAMPLES,
    MAX_CHARS,
    MIN_CHARS,
    WorldGenError,
    WorldGenResult,
)
from ui import styles
from ui.workers import WorldGenThread


class WorldGenDialog(QDialog):
    """世界观生成窗口。"""

    def __init__(
        self,
        config: AppConfig,
        *,
        tracker=None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("生成世界观")
        self.setModal(True)
        self.resize(980, 780)

        self._config = config
        self._tracker = tracker
        self._thread: WorldGenThread | None = None

        #: 生成出来并确认使用的文档
        self.result_document: WorldDocument | None = None
        #: 当前预览中的文档正文
        self._current_text = ""

        self._build_ui()
        self._refresh_ready_state()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        c = styles.COLORS
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        intro = QLabel(
            "用一句话描述你想玩的世界，AI 会把它扩写成一份完整的设定文档。"
            "写得越具体，设定越有特色。"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(
            f"color:{c['text_faint']}; font-size:12.5px; line-height:150%;"
        )
        root.addWidget(intro)

        # ---------- 描述输入 ----------
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setPlaceholderText(
            "例如：一个被海洋完全覆盖的世界，人们住在移动的船城上，"
            "靠打捞海底的旧文明遗物为生"
        )
        self.desc_edit.setFixedHeight(86)
        self.desc_edit.textChanged.connect(self._refresh_ready_state)
        root.addWidget(self.desc_edit)

        # 示例按钮：给不会写描述的用户一个起点，点一下就能改
        example_row = QHBoxLayout()
        example_row.setSpacing(6)
        example_row.addWidget(_hint("试试这些"))

        for i, example in enumerate(DESCRIPTION_EXAMPLES[:3], start=1):
            button = QPushButton(f"示例 {i}")
            button.setToolTip(example)
            button.setStyleSheet("padding: 3px 12px; font-size: 12px;")
            button.clicked.connect(
                lambda _=False, text=example: self.desc_edit.setPlainText(text)
            )
            example_row.addWidget(button)

        example_row.addStretch(1)

        self._generate_button = QPushButton("开始生成")
        self._generate_button.setObjectName("PrimaryButton")
        self._generate_button.setToolTip("用上面的描述生成一份完整的设定文档")
        self._generate_button.clicked.connect(self._on_generate)
        example_row.addWidget(self._generate_button)

        root.addLayout(example_row)

        # ---------- 预览 ----------
        preview_header = QLabel("设定预览")
        preview_header.setObjectName("PanelHint")
        root.addWidget(preview_header)

        self.preview = QTextBrowser()
        self.preview.setObjectName("StoryView")
        self.preview.setPlaceholderText("生成的内容会显示在这里，可以边写边看")
        root.addWidget(self.preview, 1)

        # ---------- 修改要求 ----------
        revise_header = QLabel("不满意？告诉它想改什么")
        revise_header.setObjectName("PanelHint")
        root.addWidget(revise_header)

        revise_row = QHBoxLayout()
        revise_row.setSpacing(8)

        self.revise_edit = QLineEdit()
        self.revise_edit.setPlaceholderText(
            "例如：势力太少了，再加两个互相敌对的组织；或者：把力量体系的代价写得更狠一点"
        )
        self.revise_edit.setClearButtonEnabled(True)
        self.revise_edit.returnPressed.connect(self._on_revise)
        self.revise_edit.textChanged.connect(self._refresh_ready_state)
        revise_row.addWidget(self.revise_edit, 1)

        self.revise_button = QPushButton("让它改")
        self.revise_button.clicked.connect(self._on_revise)
        revise_row.addWidget(self.revise_button)

        root.addLayout(revise_row)

        # ---------- 状态与按钮 ----------
        footer = QHBoxLayout()
        footer.setSpacing(8)

        self.status_label = QLabel()
        self.status_label.setStyleSheet(
            f"color:{c['text_faint']}; font-size:12px;"
        )
        footer.addWidget(self.status_label, 1)

        self.cancel_button = QPushButton("取消生成")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._on_cancel)
        footer.addWidget(self.cancel_button)

        self.use_button = QPushButton("使用这份设定")
        self.use_button.setObjectName("PrimaryButton")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self._on_use)
        footer.addWidget(self.use_button)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        footer.addWidget(close_button)

        root.addLayout(footer)

    def _refresh_ready_state(self) -> None:
        busy = self._thread is not None and self._thread.isRunning()
        has_key = self._config.has_api_key
        has_desc = bool(self.desc_edit.toPlainText().strip())
        has_doc = bool(self._current_text.strip())
        has_request = bool(self.revise_edit.text().strip())

        self._generate_button.setEnabled(not busy and has_key and has_desc)
        self.revise_button.setEnabled(not busy and has_key and has_doc and has_request)
        self.use_button.setEnabled(not busy and has_doc)
        self.revise_edit.setEnabled(not busy and has_doc)

        if not has_key:
            self.status_label.setText("尚未配置 API Key，请先到「设置 → API 设置」填入")
            self.status_label.setStyleSheet(
                f"color:{styles.COLORS['warning']}; font-size:12px;"
            )
        elif not self._current_text:
            self.status_label.setText(
                f"生成约需 {MIN_CHARS}~{MAX_CHARS} 字，会消耗一些 token"
            )
            self.status_label.setStyleSheet(
                f"color:{styles.COLORS['text_faint']}; font-size:12px;"
            )

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------

    def _make_client(self) -> DeepSeekClient:
        return DeepSeekClient(
            api_key=self._config.api_key,
            base_url=self._config.normalized_base_url(),
            model=self._config.model,
            # 生成一份文档要写几千字，超时给足
            timeout=max(self._config.timeout, 120),
        )

    def _start(self, *, description: str = "", change_request: str = "") -> None:
        if self._thread is not None and self._thread.isRunning():
            return

        self.preview.clear()
        self._set_busy(True)

        self._thread = WorldGenThread(
            self._make_client(),
            description=description,
            current_text=self._current_text if change_request else "",
            change_request=change_request,
            attempts=3,
            parent=self,
        )
        self._thread.progress.connect(self._on_progress)
        self._thread.delta.connect(self._on_delta)
        self._thread.done.connect(self._on_done)
        self._thread.failed.connect(self._on_failed)
        self._thread.usage_ready.connect(self._on_usage)
        self._thread.finished.connect(self._on_finished)
        self._thread.start()

    def _on_generate(self) -> None:
        self._start(description=self.desc_edit.toPlainText().strip())

    def _on_revise(self) -> None:
        request = self.revise_edit.text().strip()
        if not request or not self._current_text:
            return
        self._start(change_request=request)

    def _on_cancel(self) -> None:
        if self._thread is not None:
            self._thread.cancel()
            self.status_label.setText("正在取消…")

    def _on_usage(self, model: str, usage: dict, reason: str) -> None:
        if self._tracker is None:
            return
        self._tracker.record(
            model=model, usage=usage, reason=reason, peak=self._config.forced_peak()
        )

    def _on_finished(self) -> None:
        self._thread = None
        self._refresh_ready_state()

    def _set_busy(self, busy: bool) -> None:
        self.cancel_button.setEnabled(busy)
        self.desc_edit.setEnabled(not busy)
        self._generate_button.setText("生成中…" if busy else "开始生成")
        self._refresh_ready_state()
        if busy:
            self._generate_button.setEnabled(False)
            self.revise_button.setEnabled(False)
            self.use_button.setEnabled(False)

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------

    def _on_progress(self, message: str) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            f"color:{styles.COLORS['text_dim']}; font-size:12px;"
        )

    def _on_delta(self, piece: str) -> None:
        """流式显示，让用户看见它在写什么。

        这里用纯文本追加而不是渲染 Markdown ——
        内容还在写，渲染半截 Markdown 只会闪来闪去。
        """
        if not self.preview.toPlainText():
            self.preview.clear()

        cursor = self.preview.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(piece)
        bar = self.preview.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_done(self, result: WorldGenResult) -> None:
        self._set_busy(False)
        # 定稿后用等宽纯文本展示，保留 Markdown 原文便于用户核对
        self.preview.setPlainText(result.document.text)
        self._current_text = result.document.text

        c = styles.COLORS
        headings = result.document.text.count("\n## ")
        tail = f"，重试 {result.retries_used} 次" if result.retries_used else ""
        self.status_label.setText(
            f"完成：{result.document.name}　{result.document.char_count} 字"
            f"　{headings} 个分节{tail}"
        )
        self.status_label.setStyleSheet(
            f"color:{c['success']}; font-size:12px;"
        )

        self._refresh_ready_state()

    def _on_failed(self, error) -> None:
        self._set_busy(False)

        message = getattr(error, "message", str(error))
        title = "生成失败" if isinstance(error, WorldGenError) else "调用失败"

        c = styles.COLORS
        self.preview.setHtml(
            f'<div style="padding:8px;">'
            f'<div style="color:{c["danger"]};font-weight:600;">✕ {title}</div>'
            f'<div style="color:{c["text_dim"]};font-size:13px;line-height:165%;'
            f'margin-top:8px;white-space:pre-wrap;">{_escape(message)}</div></div>'
        )
        self.status_label.setText(title)
        self.status_label.setStyleSheet(f"color:{c['danger']}; font-size:12px;")

        QMessageBox.warning(self, title, message)
        self._refresh_ready_state()

    # ------------------------------------------------------------------
    # 使用
    # ------------------------------------------------------------------

    def _on_use(self) -> None:
        if not self._current_text.strip():
            return

        try:
            document = WorldDocument(
                name=self._extract_name(),
                text=self._current_text,
                source_path="（由 AI 生成）",
                encoding="utf-8",
                source_bytes=len(self._current_text.encode("utf-8")),
            )
            WorldStore().save(document)
        except OSError as exc:
            QMessageBox.warning(
                self, "保存失败", f"无法写入本地仓库：\n{exc}"
            )
            return

        self.result_document = document
        self.accept()

    def _extract_name(self) -> str:
        for line in self._current_text.splitlines():
            line = line.strip()
            if line.startswith("# "):
                return line[2:].strip()[:40] or "未命名世界"
            if line:
                break
        return "未命名世界"

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_thread()
        super().closeEvent(event)

    def reject(self) -> None:
        self._stop_thread()
        super().reject()

    def accept(self) -> None:
        self._stop_thread()
        super().accept()

    def _stop_thread(self) -> None:
        if self._thread is None or not self._thread.isRunning():
            return

        self._thread.cancel()
        for signal in (
            self._thread.progress,
            self._thread.delta,
            self._thread.done,
            self._thread.failed,
            self._thread.usage_ready,
        ):
            try:
                signal.disconnect()
            except TypeError:
                pass
        self._thread.wait(3000)


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(
        f"color:{styles.COLORS['text_faint']}; font-size:12.5px;"
    )
    return label


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
