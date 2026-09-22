"""世界观文档：导入、存储、上下文压缩。

世界观是全局硬性规则，所有 AI 生成内容都必须遵守它。
这份文档同时也要塞进每一次请求的 prompt，所以「太长怎么办」
必须在进入 AI 调用之前就解决掉。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from core.paths import WORLDS_DIR, ensure_dirs

#: 可导入的扩展名
SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown"}

#: 默认上下文预算（字符数）。约等于 6k~8k token，视中英文比例浮动
DEFAULT_CONTEXT_BUDGET = 12000

#: 文件大小上限，防止误选了一个几百 MB 的日志文件把内存打爆
MAX_FILE_BYTES = 8 * 1024 * 1024


class WorldImportError(Exception):
    """导入失败，message 可直接展示给用户。"""


# ----------------------------------------------------------------------
# 读取
# ----------------------------------------------------------------------


def _decode_score(text: str) -> float:
    """给一次解码结果打分，分数越高越像「解对了」。

    存在这个函数的原因：gb18030 和 big5 都能把对方的字节序列「解成功」，
    不报错，但结果是乱码。光靠 try/except 的顺序挑不出正确答案。
    实测特征：
      · GB18030 解 Big5 字节 → 大量私用区字符（U+E000~U+F8FF）
      · Big5 解 GBK 字节    → 无报错，但汉字数量偏少、夹杂异常码点
    """
    if not text:
        return float("-inf")

    score = 0
    for char in text:
        code = ord(char)
        if code == 0xFFFD:                 # 替换字符：明确解错了
            score -= 100
        elif 0xE000 <= code <= 0xF8FF:     # 私用区：GB18030 解 Big5 的典型特征
            score -= 50
        elif code < 0x80:                  # ASCII
            score += 2
        elif 0x4E00 <= code <= 0x9FFF:     # 常用汉字
            score += 3
        elif 0x3000 <= code <= 0x303F:     # 中文标点
            score += 2
        elif 0xFF00 <= code <= 0xFFEF:     # 全角字符
            score += 1
        elif code in (0x2018, 0x2019, 0x201C, 0x201D, 0x2026, 0x2014, 0x00B7):
            score += 1                     # 弯引号、省略号、破折号
        else:
            score -= 1                     # 少见区段，多半是解错了

    # 按字符数归一化，否则长文本会仅因为长而胜出
    return score / len(text)


def read_text_file(path: Path) -> tuple[str, str]:
    """读文本文件，返回 (内容, 实际使用的编码)。

    中文 Windows 上 GBK 系编码的 txt 非常常见，直接按 utf-8 读会炸；
    而 GBK 与 Big5 之间还存在「双方都能解成功、但其中一方是乱码」的
    歧义，所以候选编码要解码后打分比优，不能只靠顺序。
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise WorldImportError(f"无法读取文件：{exc}") from exc

    if len(raw) > MAX_FILE_BYTES:
        raise WorldImportError(
            f"文件过大（{len(raw) / 1024 / 1024:.1f} MB），"
            f"上限为 {MAX_FILE_BYTES // 1024 // 1024} MB。"
        )

    if not raw.strip():
        raise WorldImportError("文件是空的，没有可导入的内容。")

    # 带 BOM 的 UTF-16 必须先判，否则会被当成别的编码解出一堆乱码
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass

    # UTF-8 结构严格、能自校验，解成功基本就是对的，直接采信
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue

    # 到了这里说明不是 UTF-8。gb18030 / big5 都很宽容，
    # 必须逐个解码打分，取分数最高者，否则会产生静默乱码。
    best_text = ""
    best_encoding = ""
    best_score = float("-inf")

    for encoding in ("gb18030", "big5"):
        try:
            decoded = raw.decode(encoding)
        except UnicodeDecodeError:
            continue

        score = _decode_score(decoded)
        if score > best_score:
            best_text, best_encoding, best_score = decoded, encoding, score

    if best_encoding:
        return best_text, best_encoding

    # 兜底：latin-1 对任意字节都不会失败，至少保证程序不崩
    return raw.decode("latin-1", errors="replace"), "latin-1"


# ----------------------------------------------------------------------
# 上下文压缩
# ----------------------------------------------------------------------

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+\S")


def _split_sections(text: str) -> list[tuple[str, str]]:
    """把文档切成 [(标题行, 正文), ...]。

    有 Markdown 标题就按标题切；
    没有标题的纯文本会退化成「第一个标题为空、整篇是正文」，
    再由 compress_for_context 走首尾截断分支。
    """
    sections: list[tuple[str, list[str]]] = []
    current_heading = ""
    current_body: list[str] = []

    for line in text.splitlines():
        if _HEADING_RE.match(line):
            if current_heading or current_body:
                sections.append((current_heading, current_body))
            current_heading = line.strip()
            current_body = []
        else:
            current_body.append(line)

    if current_heading or current_body:
        sections.append((current_heading, current_body))

    return [(h, "\n".join(b)) for h, b in sections]


def _head_tail(text: str, budget: int) -> str:
    """首尾保留、中间省略。用于无法按结构切分的文档。"""
    marker = "\n\n……（中段内容已省略）……\n\n"
    keep = max(budget - len(marker), 0)
    head_len = int(keep * 0.7)  # 开头信息密度更高，多留一些
    tail_len = keep - head_len
    return text[:head_len].rstrip() + marker + text[-tail_len:].lstrip()


def compress_for_context(
    text: str, budget: int = DEFAULT_CONTEXT_BUDGET
) -> tuple[str, bool]:
    """把世界观压到 budget 字符以内，返回 (文本, 是否发生了压缩)。

    策略：结构优先。保留全部标题作为「目录」，正文按各节长度
    等比例分配剩余预算。这样 AI 至少知道世界观里有哪些部分，
    不会因为压缩而凭空丢掉一整个设定板块。
    """
    text = text.strip()
    if len(text) <= budget:
        return text, False

    sections = _split_sections(text)
    headings = [h for h, _ in sections if h]
    heading_cost = sum(len(h) + 1 for h in headings)

    # 没有可用结构时退回首尾截断：
    # 没有标题的纯文本、或只有一节 —— 此时按比例分配等于只保留开头，
    # 会丢掉文档结尾的设定，首尾各留一段更稳。
    # 标题本身吃掉大半预算时同理。
    if len(sections) <= 1 or not headings or heading_cost >= budget * 0.5:
        return _head_tail(text, budget), True

    # 每个被压缩的小节要加一句省略标记，先预留出来
    truncated_count = sum(
        1 for _, body in sections if len(body) > 0
    )
    marker_reserve = truncated_count * 16 + len(sections) * 2
    body_budget = budget - heading_cost - marker_reserve
    if body_budget <= 0:
        return _head_tail(text, budget), True

    total_body = sum(len(body) for _, body in sections) or 1

    blocks: list[str] = []
    for heading, body in sections:
        pieces: list[str] = []
        if heading:
            pieces.append(heading)

        if body.strip():
            share = max(int(body_budget * len(body) / total_body), 80)
            if len(body) <= share:
                pieces.append(body)
            else:
                pieces.append(body[:share].rstrip() + "\n……（本节已压缩）")

        if pieces:
            blocks.append("\n".join(pieces))

    compressed = "\n\n".join(blocks).strip()

    # 比例分配 + 下限兜底后仍可能超预算，这时退回首尾截断保证硬上限
    if len(compressed) > budget:
        return _head_tail(text, budget), True

    return compressed, True


# ----------------------------------------------------------------------
# 文档模型
# ----------------------------------------------------------------------


@dataclass
class WorldDocument:
    """一份导入的世界观文档。"""

    name: str
    text: str
    source_path: str = ""
    encoding: str = "utf-8"
    imported_at: str = ""
    #: 阶段 5 接入 AI 摘要后回填；有值时优先用它当上下文
    summary: str = ""
    #: 原始文件大小（字节），用于界面上做对照
    source_bytes: int = 0

    def __post_init__(self) -> None:
        if not self.imported_at:
            self.imported_at = datetime.now().isoformat(timespec="seconds")

    # ---------- 派生信息 ----------

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    # ---------- 上下文 ----------

    def context_text(
        self, budget: int = DEFAULT_CONTEXT_BUDGET
    ) -> tuple[str, bool]:
        """返回喂给 AI 的世界观文本。

        已有 AI 摘要时直接用摘要 —— 摘要本身就是为这个用途生成的。
        """
        if self.summary.strip():
            return self.summary.strip(), True
        return compress_for_context(self.text, budget)

    def needs_compression(self, budget: int = DEFAULT_CONTEXT_BUDGET) -> bool:
        return self.char_count > budget and not self.summary.strip()

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WorldDocument":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in allowed})


# ----------------------------------------------------------------------
# 存储
# ----------------------------------------------------------------------


def _slugify(name: str) -> str:
    """把文档名转成安全的文件名片段，保留中文。"""
    safe = re.sub(r"[^\w一-鿿-]+", "_", name, flags=re.UNICODE)
    return safe.strip("_")[:40] or "world"


@dataclass
class WorldStore:
    """data/worlds/ 下的世界观文档仓库。"""

    directory: Path = field(default_factory=lambda: WORLDS_DIR)

    def save(self, document: WorldDocument) -> Path:
        ensure_dirs()
        self.directory.mkdir(parents=True, exist_ok=True)

        # 文件名带内容校验和前缀：同名不同内容的两份文档不会互相覆盖
        filename = f"{_slugify(document.name)}-{document.checksum[:8]}.json"
        path = self.directory / filename

        payload = json.dumps(
            document.to_dict(), ensure_ascii=False, indent=2
        )
        path.write_text(payload, encoding="utf-8")
        return path

    def list_all(self) -> list[WorldDocument]:
        """列出已导入的世界观，按导入时间倒序。"""
        if not self.directory.is_dir():
            return []

        documents: list[tuple[str, WorldDocument]] = []
        for path in self.directory.glob("*.json"):
            document = self._load_path(path)
            if document is not None:
                documents.append((document.imported_at, document))

        documents.sort(key=lambda pair: pair[0], reverse=True)
        return [doc for _, doc in documents]

    def _load_path(self, path: Path) -> WorldDocument | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 单个文件损坏不该让整个列表打不开
            return None
        if not isinstance(raw, dict):
            return None
        try:
            return WorldDocument.from_dict(raw)
        except TypeError:
            return None

    def delete(self, document: WorldDocument) -> bool:
        """按内容校验和删除对应文件。"""
        target = self.directory / f"{_slugify(document.name)}-{document.checksum[:8]}.json"
        try:
            target.unlink()
            return True
        except OSError:
            return False


def import_world_file(path: str | Path) -> WorldDocument:
    """从磁盘上的 .txt / .md 导入一份世界观文档。"""
    file_path = Path(path)

    if not file_path.is_file():
        raise WorldImportError(f"文件不存在：{file_path}")

    if file_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        supported = "、".join(sorted(SUPPORTED_SUFFIXES))
        raise WorldImportError(
            f"不支持的文件格式「{file_path.suffix or '（无扩展名）'}」。\n"
            f"支持的格式：{supported}"
        )

    text, encoding = read_text_file(file_path)

    return WorldDocument(
        name=file_path.stem,
        text=text,
        source_path=str(file_path),
        encoding=encoding,
        source_bytes=file_path.stat().st_size,
    )
