"""剧情展示面板：渲染场景描述、事件文本、系统提示。

阶段 1 只负责显示，内容来源由外部调用 append_* 方法注入。
"""

from PyQt6.QtWidgets import QTextBrowser, QWidget

from ui import styles
from ui.panel_base import Panel


class StoryPanel(Panel):
    """剧情 / 叙事文本展示区。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("剧情", parent)

        self.view = QTextBrowser()
        self.view.setObjectName("StoryView")
        self.view.setOpenExternalLinks(False)
        self.view.setReadOnly(True)
        # 不要自动加载远程资源，纯本地渲染
        self.view.setOpenLinks(False)
        self.body.addWidget(self.view, 1)

        self.clear()

    # ---------- 对外接口 ----------

    def clear(self) -> None:
        self.view.clear()

    def append_narrative(self, text: str) -> None:
        """追加一段正文叙事（场景描述 / 事件文本）。"""
        self._append_block(text, css_class="narrative", top_gap=16)

    def append_player_action(self, text: str) -> None:
        """追加一行玩家行动，用主色调高亮，和 AI 叙事区分开。"""
        self._append_block(f"▸ {text}", css_class="action", top_gap=18)

    def append_system(self, text: str) -> None:
        """追加系统提示（存档、导入、报错等），弱化显示。"""
        self._append_block(f"— {text} —", css_class="system", top_gap=14)

    def append_dialog(self, speaker: str, text: str) -> None:
        """追加一段 NPC 对话。"""
        block = (
            f'<span class="speaker">{self._escape(speaker)}</span>'
            f'<span class="colon">：</span>'
            f'<span class="dialog">{self._escape(text)}</span>'
        )
        self._append_block(block, css_class="dialog-wrap", top_gap=10, raw=True)

    def append_html(self, html: str) -> None:
        """直接追加一段已渲染好的 HTML，阶段 7/8 生成结构化内容时用。"""
        self.view.append(html)
        self._scroll_to_bottom()

    # ---------- 内部实现 ----------

    def _append_block(
        self,
        text: str,
        css_class: str = "narrative",
        top_gap: int = 16,
        raw: bool = False,
    ) -> None:
        body = text if raw else self._escape(text)
        # QTextBrowser 的 append 不支持 margin，用 Qt 的 CSS 在块级元素上下手
        block = (
            f'<div class="{css_class}" style="margin-top:{top_gap}px;">{body}</div>'
        )
        self.view.append(block)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    @staticmethod
    def _escape(text: str) -> str:
        """转义 HTML 特殊字符，并把换行转成 <br>，同时保护段落缩进。"""
        escaped = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return escaped.replace("\n", "<br>")

    # ---------- 样式 ----------

    def apply_document_style(self) -> None:
        """QTextBrowser 内部的 CSS（和 QSS 是两套体系，必须单独写）。"""
        c = styles.COLORS
        self.view.document().setDefaultStyleSheet(
            f"""
            body {{ color: {c['text']}; line-height: 175%; }}
            .narrative {{ color: {c['text']}; font-size: 15px; line-height: 180%; }}
            .action {{ color: {c['accent']}; font-size: 14px; font-weight: 600; }}
            .system {{ color: {c['text_faint']}; font-size: 12.5px; }}
            .speaker {{ color: {c['warning']}; font-weight: 600; }}
            .colon {{ color: {c['text_dim']}; }}
            .dialog {{ color: {c['text']}; }}
            .item-name {{ font-weight: 600; }}
            .dim {{ color: {c['text_dim']}; }}
            .hint {{ color: {c['text_faint']}; font-size: 12.5px; }}
            """
        )
