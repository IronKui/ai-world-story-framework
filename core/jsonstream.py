"""从流式返回的 JSON 里增量提取字段值。

为什么需要这个：事件生成用的是 JSON 输出模式，模型返回的是
`{"narrative": "铁索阶梯...", "options": [...]}` 这样的结构。
网络层是流式的，但直接把原始分片打到界面上，用户会看到
`{"narrative": "铁索` 这种噪音 —— 比不流式还糟。

所以要把目标字段的**值**从不断增长的缓冲里抠出来，只显示正文。

难点在于分片是任意的，可能切在转义序列中间（比如 `\` 和 `n` 被拆开），
这时不能急着输出，要等下一个分片到齐。
"""

from __future__ import annotations

#: JSON 字符串里的简单转义
_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


class JsonFieldStream:
    """增量提取某个 JSON 字段的字符串值。

    用法：
        stream = JsonFieldStream("narrative")
        for chunk in chunks:
            text = stream.feed(chunk)   # 返回本次新增的可显示文本
    """

    def __init__(self, field: str):
        self._key = f'"{field}"'
        self._buffer = ""
        #: 值的起始下标（指向开引号之后），-1 表示还没找到
        self._value_start = -1
        #: 已经吐出去的字符数
        self._emitted = 0
        self._finished = False

    @property
    def finished(self) -> bool:
        """值的结束引号已经出现。"""
        return self._finished

    @property
    def text(self) -> str:
        """到目前为止提取到的完整文本。"""
        return self._decode()[0]

    def feed(self, chunk: str) -> str:
        """喂入一个分片，返回本次新增的、可以显示的文本。"""
        if self._finished or not chunk:
            return ""

        self._buffer += chunk

        if self._value_start < 0 and not self._locate_value():
            return ""

        decoded, complete = self._decode()
        if complete:
            self._finished = True

        new_text = decoded[self._emitted :]
        self._emitted = len(decoded)
        return new_text

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _locate_value(self) -> bool:
        """在缓冲里定位值的起始位置。找到了返回 True。"""
        key_at = self._buffer.find(self._key)
        if key_at < 0:
            return False

        colon_at = self._buffer.find(":", key_at + len(self._key))
        if colon_at < 0:
            return False

        # 跳过冒号后的空白，值必须是字符串
        quote_at = colon_at + 1
        while quote_at < len(self._buffer) and self._buffer[quote_at].isspace():
            quote_at += 1

        if quote_at >= len(self._buffer):
            return False
        if self._buffer[quote_at] != '"':
            # 值不是字符串（对象、数组、数字），这个字段不支持流式提取
            self._finished = True
            return False

        self._value_start = quote_at + 1
        return True

    def _decode(self) -> tuple[str, bool]:
        """解码已缓冲的部分。

        返回 (已解码文本, 值是否已完整结束)。

        遇到不完整的转义序列时**不输出半个字符**，
        否则界面上会闪过 `\` 或乱码，等下一个分片补齐再出。
        """
        raw = self._buffer[self._value_start :]
        out: list[str] = []
        index = 0
        length = len(raw)

        while index < length:
            char = raw[index]

            if char == '"':
                # 未转义的引号 = 值结束
                return "".join(out), True

            if char != "\\":
                out.append(char)
                index += 1
                continue

            # 转义序列
            if index + 1 >= length:
                break  # 反斜杠后还没内容，等下一片

            marker = raw[index + 1]

            if marker == "u":
                # \uXXXX 需要够 6 个字符才完整
                if index + 6 > length:
                    break
                hex_digits = raw[index + 2 : index + 6]
                try:
                    out.append(chr(int(hex_digits, 16)))
                except ValueError:
                    out.append("\\u" + hex_digits)
                index += 6
                continue

            if marker in _SIMPLE_ESCAPES:
                out.append(_SIMPLE_ESCAPES[marker])
                index += 2
                continue

            # 未知转义（模型偶尔会写 \' 之类），原样保留
            out.append(marker)
            index += 2

        return "".join(out), False
