"""世界观文档管理窗口：导入、预览、切换、删除。

切换世界观文档 = 换一个游戏世界，不需要改任何底层代码。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.world import (
    DEFAULT_CONTEXT_BUDGET,
    SUPPORTED_SUFFIXES,
    WorldDocument,
    WorldImportError,
    WorldStore,
    import_world_file,
)
from ui import styles

#: 预览区最多渲染这么多字符，避免超长文档拖慢界面
PREVIEW_LIMIT = 20000


class WorldDocDialog(QDialog):
    """世界观文档管理器。"""

    def __init__(
        self,
        current: WorldDocument | None = None,
        config=None,
        tracker=None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        #: 生成世界观需要 API Key 与用量统计，没传就不显示生成入口
        self._config = config
        self._tracker = tracker
        self.setWindowTitle("世界观文档")
        self.setModal(True)
        self.resize(1040, 680)

        self._store = WorldStore()
        self._current = current
        self._chosen: WorldDocument | None = current
        #: 用户是否删掉了当前正在使用的世界。
        #: 删掉后 _chosen 会变成 None，光看返回值分不清
        #: 「没选」和「删了」，必须单独记一个标志。
        self.current_deleted = False

        self._build_ui()
        self._reload_list(select=self._chosen)

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # ---------- 左：已导入列表 ----------
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(7)

        list_header = QLabel("已导入的世界观")
        list_header.setObjectName("PanelHint")
        left_layout.addWidget(list_header)

        self.world_list = QListWidget()
        self.world_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.world_list.currentItemChanged.connect(self._on_world_selected)
        left_layout.addWidget(self.world_list, 1)

        splitter.addWidget(left)

        # ---------- 右：详情 + 预览 ----------
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(7)

        detail_header = QLabel("文档详情")
        detail_header.setObjectName("PanelHint")
        right_layout.addWidget(detail_header)

        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        self.info_label.setTextFormat(Qt.TextFormat.RichText)
        self.info_label.setMinimumHeight(96)
        self.info_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.info_label.setStyleSheet(
            f"background:{styles.COLORS['bg_input']};"
            f"border:1px solid {styles.COLORS['border_soft']};"
            "border-radius:8px; padding:12px 14px;"
        )
        right_layout.addWidget(self.info_label)

        preview_header = QLabel("原文预览")
        preview_header.setObjectName("PanelHint")
        right_layout.addWidget(preview_header)

        self.preview = QTextBrowser()
        self.preview.setObjectName("StoryView")
        right_layout.addWidget(self.preview, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([280, 720])

        root.addWidget(splitter, 1)

        # ---------- 底部按钮 ----------
        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        self.generate_button = QPushButton("用 AI 生成…")
        self.generate_button.setObjectName("PrimaryButton")
        self.generate_button.setToolTip(
            "用一句大白话描述你想玩的世界，AI 会写出一份完整设定"
        )
        self.generate_button.clicked.connect(self._on_generate)
        self.generate_button.setEnabled(self._config is not None)
        buttons.addWidget(self.generate_button)

        self.sample_button = QPushButton("导入示例")
        self.sample_button.setToolTip(
            "随程序附带的一份设定，用来先体验一下。不需要 API Key"
        )
        self.sample_button.clicked.connect(self._on_import_sample)
        buttons.addWidget(self.sample_button)

        self.import_button = QPushButton("导入文档…")
        self.import_button.clicked.connect(self._on_import)
        buttons.addWidget(self.import_button)

        self.apply_button = QPushButton("应用此世界")
        self.apply_button.clicked.connect(self._on_apply)
        buttons.addWidget(self.apply_button)

        buttons.addStretch(1)

        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("DangerButton")
        self.delete_button.clicked.connect(self._on_delete)
        buttons.addWidget(self.delete_button)

        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)

        root.addLayout(buttons)

        self._update_buttons()

    # ------------------------------------------------------------------
    # 列表
    # ------------------------------------------------------------------

    def _reload_list(self, select: WorldDocument | None = None) -> None:
        self.world_list.blockSignals(True)
        self.world_list.clear()

        documents = self._store.list_all()
        target_row = -1

        for row, document in enumerate(documents):
            suffix = "（当前）" if self._is_current(document) else ""
            item = QListWidgetItem(f"{document.name}　{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, document)
            item.setToolTip(
                f"来源：{document.source_path or '（无）'}\n"
                f"导入时间：{document.imported_at}\n"
                f"字数：{document.char_count}"
            )
            self.world_list.addItem(item)
            if select is not None and document.checksum == select.checksum:
                target_row = row

        self.world_list.blockSignals(False)

        if self.world_list.count() == 0:
            self._render_empty()
        elif target_row >= 0:
            self.world_list.setCurrentRow(target_row)
        else:
            self.world_list.setCurrentRow(0)

        self._update_buttons()

    def _current_document(self) -> WorldDocument | None:
        item = self.world_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _is_current(self, document: WorldDocument) -> bool:
        return (
            self._current is not None
            and document.checksum == self._current.checksum
        )

    def _on_world_selected(self, *_args) -> None:
        self._render_detail(self._current_document())
        self._update_buttons()

    def _update_buttons(self) -> None:
        document = self._current_document()
        self.apply_button.setEnabled(document is not None)
        self.delete_button.setEnabled(document is not None)

        if document is None:
            self.apply_button.setText("应用此世界")
            self.delete_button.setEnabled(False)
        elif self._is_current(document):
            self.apply_button.setText("当前世界")
            self.apply_button.setEnabled(False)
        else:
            self.apply_button.setText("应用此世界")

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------

    def _render_empty(self) -> None:
        c = styles.COLORS
        self.info_label.setText(
            f'<div style="color:{c["text_faint"]};">'
            "还没有导入任何世界观文档。<br><br>"
            "点击下方「导入新文档…」，选择一份 .txt 或 .md 文件。<br>"
            "这份文档将成为全局硬性规则，AI 生成的一切内容都必须遵守它。"
            "</div>"
        )
        self.preview.setPlainText("")

    def _render_detail(self, document: WorldDocument | None) -> None:
        if document is None:
            self._render_empty()
            return

        c = styles.COLORS
        context_text, compressed = document.context_text()

        # 压缩状态用颜色区分，长文档被压过是玩家需要知道的事
        if document.summary.strip():
            status = ("AI 摘要", c["success"])
        elif compressed:
            status = ("已压缩", c["warning"])
        else:
            status = ("原文完整送入", c["success"])

        rows = [
            ("来源文件", document.source_path or "（未知）"),
            ("文件编码", document.encoding),
            ("原始大小", self._human_bytes(document.source_bytes)),
            ("导入时间", document.imported_at),
        ]
        rows_html = "".join(
            f'<tr><td style="color:{c["text_faint"]};padding:1px 14px 1px 0;'
            f'white-space:nowrap;">{label}</td>'
            f'<td style="color:{c["text_dim"]};">{value}</td></tr>'
            for label, value in rows
        )

        self.info_label.setText(
            f'<div style="color:{c["text"]};font-size:15px;font-weight:600;'
            f'margin-bottom:8px;">{document.name}</div>'
            f'<table style="font-size:12.5px;">{rows_html}'
            f'<tr><td style="color:{c["text_faint"]};padding:1px 14px 1px 0;">'
            f"送入 AI 的上下文</td>"
            f'<td><span style="color:{c["text"]};">{len(context_text)} 字</span>'
            f'　<span style="color:{status[1]};font-weight:600;">{status[0]}</span>'
            f'　<span style="color:{c["text_faint"]};">'
            f'（原文 {document.char_count} 字，预算 {DEFAULT_CONTEXT_BUDGET} 字）'
            f"</span></td></tr></table>"
        )

        preview_text = document.text
        if len(preview_text) > PREVIEW_LIMIT:
            preview_text = (
                preview_text[:PREVIEW_LIMIT]
                + f"\n\n……（预览仅显示前 {PREVIEW_LIMIT} 字，"
                f"完整文档共 {document.char_count} 字）……"
            )
        self.preview.setPlainText(preview_text)

    @staticmethod
    def _human_bytes(size: int) -> str:
        if size <= 0:
            return "（未知）"
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / 1024 / 1024:.1f} MB"

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------

    def _on_import_sample(self) -> None:
        """导入随包附带的示例设定。

        存在的意义是：用户配好 API Key 之后不必先花时间生成一份设定
        才能看到游戏长什么样。也不需要 API Key。
        """
        from core.paths import ASSETS_DIR

        sample = ASSETS_DIR / "示例世界观-眠神纪.md"
        if not sample.is_file():
            QMessageBox.warning(
                self,
                "找不到示例文档",
                f"程序目录下没有找到示例设定：\n{sample}\n\n"
                "可能是安装不完整，请重新安装，或者用「用 AI 生成…」自己写一份。",
            )
            return

        try:
            document = import_world_file(sample)
            self._store.save(document)
        except (WorldImportError, OSError) as exc:
            QMessageBox.warning(self, "导入失败", f"无法导入示例设定：\n{exc}")
            return

        self._reload_list(select=document)
        self._render_detail(document)

    def _on_generate(self) -> None:
        """用大白话生成一份新设定。

        生成窗口自己会把文档存进仓库，这里只需要刷新列表并选中它。
        """
        from ui.world_gen_dialog import WorldGenDialog

        dialog = WorldGenDialog(self._config, tracker=self._tracker, parent=self)
        dialog.exec()

        document = dialog.result_document
        if document is None:
            return

        self._reload_list(select=document)
        self._render_detail(document)

    def _on_import(self) -> None:
        patterns = " ".join(f"*{suffix}" for suffix in sorted(SUPPORTED_SUFFIXES))
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择世界观文档",
            "",
            f"世界观文档 ({patterns});;文本文件 (*.txt);;Markdown (*.md);;所有文件 (*)",
        )
        if not path:
            return

        try:
            document = import_world_file(path)
        except WorldImportError as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        except OSError as exc:
            QMessageBox.warning(self, "导入失败", f"读取文件时出错：\n{exc}")
            return

        try:
            self._store.save(document)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "保存失败",
                f"文档已读取，但无法写入本地仓库：\n{exc}\n\n"
                "本次仍可使用，但重启后会丢失。",
            )

        self._reload_list(select=document)
        self._render_detail(document)

        if document.needs_compression():
            QMessageBox.information(
                self,
                "文档较长",
                f"《{document.name}》共 {document.char_count} 字，"
                f"超过上下文预算 {DEFAULT_CONTEXT_BUDGET} 字。\n\n"
                "送入 AI 时会自动按章节结构压缩，标题会全部保留，"
                "正文按比例截取。你可以在右侧详情里看到压缩后的实际字数。",
            )

    def _on_apply(self) -> None:
        document = self._current_document()
        if document is None:
            return
        self._chosen = document
        self._current = document
        self._reload_list(select=document)
        self.accept()

    def _on_delete(self) -> None:
        document = self._current_document()
        if document is None:
            return

        answer = QMessageBox.question(
            self,
            "删除世界观",
            f"确定从本地仓库中删除《{document.name}》吗？\n\n"
            "这只删除已导入的副本，不会动你的原始文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        if not self._store.delete(document):
            QMessageBox.warning(self, "删除失败", "无法删除该文件，请检查文件权限。")

        if self._chosen is not None and self._chosen.checksum == document.checksum:
            # 当前正在用的世界被删了，退回未选择状态
            self._chosen = None
            self._current = None
            self.current_deleted = True

        self._reload_list()

    # ------------------------------------------------------------------
    # 结果
    # ------------------------------------------------------------------

    def chosen_world(self) -> WorldDocument | None:
        """对话框关闭后被选中的世界（未选择则为 None）。"""
        return self._chosen
