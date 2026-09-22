"""世界观生成 agent。

用户用大白话描述想玩什么世界，这里调用他自己的 API Key 生成一份
符合本程序解析要求的世界观文档。

写这个模块的动机很实际：让普通用户自己写一份能被程序正确读取的
Markdown 设定文档，是不现实的。让他们拿提示词去外部 AI 生成再贴回来，
中间每一步都可能出错（格式、编码、字数、把剧情当设定写）。
内置进来就没有这些环节。

**这段系统提示词不是随便写的**，它必须锁死解析器的实际约束：
  · 扩展名与编码 —— world.SUPPORTED_SUFFIXES / read_text_file
  · 必须用 Markdown 标题分节 —— compress_for_context 按 ^#{1,6} 切分
  · 字数区间 —— 超过 DEFAULT_CONTEXT_BUDGET 会走压缩
所以改解析器时，这里的约束要同步改。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from core.api_client import ApiError, DeepSeekClient
from core.debuglog import LOG
from core.world import WorldDocument

#: 生成时给多少 token。一份 3000~5000 字的中文文档约 3000 token，
#: 留足余量，避免写到一半被截断 —— 截断的文档是不能用的
GEN_MAX_TOKENS = 8000

#: 生成的文档长度区间（字符数）。
#: 下限保证 AI 有足够的硬性设定可供校验器参照；
#: 上限压在上下文预算之内，这样不会触发压缩。
MIN_CHARS = 1200
MAX_CHARS = 9000

#: 至少要有几个二级标题。压缩算法靠标题切分，标题太少会退化成首尾截断
MIN_SECTIONS = 4

#: 默认尝试次数（含首次）
DEFAULT_ATTEMPTS = 3

#: 宽松的 Markdown 标题匹配，与 world._HEADING_RE 保持一致
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+\S", re.MULTILINE)


WORLDGEN_SYSTEM = f"""你是一位世界观设定师，为文字游戏撰写世界设定文档。

玩家会用一句话描述他想玩的世界。你要把它扩写成一份完整的、可直接使用的设定文档。

【最重要的区分】
你写的是**世界**，不是**故事**。
  · 要写：这个世界的规则、地理、势力、力量体系、社会形态、矛盾与禁忌
  · 不要写：主角是谁、剧情怎么发展、结局是什么
游戏中的事件与人物由程序实时生成，你写死剧情会与之冲突。

【输出格式 —— 必须严格遵守】
1. 输出 Markdown 格式的纯文本，不要用代码块包裹，不要写任何解释性文字
2. 首行是一级标题：`# 世界名称`
3. 用二级标题 `##` 分节，至少 {MIN_SECTIONS} 个，最多 9 个
   标题必须简洁（不超过 12 字），因为程序会把标题当作目录保留
4. 总字数控制在 {MIN_CHARS}~{MAX_CHARS} 字之间

【建议覆盖的小节】
  世界概述 / 地理 / 势力 / 力量体系或核心规则 / 社会与日常 /
  核心矛盾 / 禁忌与传说 / 名词速查

【内容质量要求】
  · 力量体系要有**明确的规则和代价**。例如「用多了会怎样」——
    有代价的体系才能被校验器用来判断内容是否合理，也才好生成道具
  · 势力和人物要具体：有名字、有立场、彼此之间有矛盾
  · 明确写出**这个世界的禁忌**（什么事绝对不能做、做了会怎样）。
    这是游戏里最有用的一部分
  · 最后加一节「名词速查」，列出地点、组织、专有名词，便于后续引用
  · 避免空泛的形容（「神秘的力量」「古老的传说」），要给具体细节

直接输出文档正文。"""


REVISE_SYSTEM = """你是一位世界观设定师。玩家对现有的世界设定文档不满意，提出了修改要求。

你会收到：现有文档 + 玩家的修改要求。

要求：
1. 按玩家的要求修改，同时**保持文档原有的结构与格式**：
   一级标题、二级标题分节、Markdown 纯文本
2. 只改玩家要求的部分，其余内容尽量保留 —— 玩家没说的地方
   说明他认可
3. 如果玩家的要求与文档其他部分矛盾，顺带把矛盾处一并理顺
4. 修改后仍要保持总字数在合理范围内
5. 不要输出任何解释，不要用代码块包裹，直接输出修改后的完整文档

直接输出文档正文。"""


# ----------------------------------------------------------------------
# 结果与错误
# ----------------------------------------------------------------------


@dataclass
class WorldGenAttempt:
    """一次生成尝试的记录。"""

    index: int
    text: str
    problem: str = ""
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return not self.problem


@dataclass
class WorldGenResult:
    """一次世界观生成的结果。"""

    document: WorldDocument
    attempts: list[WorldGenAttempt] = field(default_factory=list)
    #: 是否为修改已有文档（而非从零生成）
    revised: bool = False

    @property
    def retries_used(self) -> int:
        return max(len(self.attempts) - 1, 0)


class WorldGenError(Exception):
    """连续多次生成都不合格。message 可直接展示给用户。"""

    def __init__(self, problems: list[str], attempts: int):
        self.problems = problems
        detail = "\n".join(f"· {p}" for p in problems) or "（未返回具体原因）"
        super().__init__(
            f"连续 {attempts} 次生成的设定文档都不符合要求，已放弃。\n\n"
            f"问题：\n{detail}\n\n"
            "建议：把描述写得更具体一些（例如「一个被海洋覆盖、"
            "人们住在船上的世界」，而不是「一个有趣的世界」），然后重试。"
        )


# ----------------------------------------------------------------------
# 结构检查
# ----------------------------------------------------------------------


def check_structure(text: str) -> tuple[bool, str]:
    """检查生成的文档是否符合本程序的解析要求。

    返回 (是否合格, 问题说明)。问题说明会回喂给模型让它重写。

    这里只查**结构**，不查内容质量 —— 内容好不好由使用者自己判断，
    程序只能保证「这份文档读得进来、切得开、不会被截断」。
    """
    stripped = (text or "").strip()

    if not stripped:
        return False, "文档内容为空"

    if len(stripped) < MIN_CHARS:
        return False, f"文档只有 {len(stripped)} 字，太短了，至少要 {MIN_CHARS} 字"

    if len(stripped) > MAX_CHARS:
        return False, f"文档有 {len(stripped)} 字，太长了，请压缩到 {MAX_CHARS} 字以内"

    # 首行必须是一级标题，界面上要拿它当文档名
    first_line = stripped.splitlines()[0].strip()
    if not first_line.startswith("# "):
        return False, "文档开头缺少一级标题（形如「# 世界名称」）"

    headings = _HEADING_RE.findall(stripped)
    if len(headings) < MIN_SECTIONS:
        return False, (
            f"只有 {len(headings)} 个分节标题，至少需要 {MIN_SECTIONS} 个"
            "（用 ## 分节）。程序按标题切分文档，标题太少会导致内容被截断丢失"
        )

    # 模型偶尔会不听劝用代码块包裹
    if stripped.startswith("```"):
        return False, "不要用代码块包裹文档，直接输出 Markdown 正文"

    return True, ""


# ----------------------------------------------------------------------
# 提示词组装
# ----------------------------------------------------------------------


def build_generate_messages(description: str, feedback: list[str] | None = None) -> list[dict]:
    content = f"【玩家想要的世界】\n{description.strip()}"

    if feedback:
        bullets = "\n".join(f"· {p}" for p in feedback)
        content += (
            f"\n\n【上一次的输出不符合要求，请修正】\n{bullets}\n\n"
            "请重新输出完整文档。"
        )

    return [
        {"role": "system", "content": WORLDGEN_SYSTEM},
        {"role": "user", "content": content},
    ]


def build_revise_messages(
    current_text: str, change_request: str, feedback: list[str] | None = None
) -> list[dict]:
    content = (
        f"【现有文档】\n{current_text.strip()}\n\n"
        f"【玩家的修改要求】\n{change_request.strip()}"
    )

    if feedback:
        bullets = "\n".join(f"· {p}" for p in feedback)
        content += (
            f"\n\n【上一次的输出不符合要求，请修正】\n{bullets}\n\n"
            "请重新输出修改后的完整文档。"
        )

    return [
        {"role": "system", "content": REVISE_SYSTEM},
        {"role": "user", "content": content},
    ]


# ----------------------------------------------------------------------
# 生成
# ----------------------------------------------------------------------


def _run(
    client: DeepSeekClient,
    make_messages: Callable[[list[str]], list[dict]],
    *,
    attempts: int,
    revised: bool,
    on_progress: Callable[[str], None] | None,
    on_delta: Callable[[str], None] | None,
    on_usage: Callable[[str, dict, str], None] | None,
    should_stop: Callable[[], bool] | None,
) -> WorldGenResult:
    """生成 + 结构检查 + 不合格则带原因重试。

    make_messages 接收「上一轮的问题列表」，返回本次要发的消息。
    首次调用时列表为空。
    """
    records: list[WorldGenAttempt] = []
    feedback: list[str] = []
    reason = "世界观修改" if revised else "世界观生成"

    def report(message: str) -> None:
        if on_progress is not None:
            on_progress(message)

    for index in range(1, attempts + 1):
        if should_stop is not None and should_stop():
            break

        report(f"第 {index}/{attempts} 次生成中…" if index > 1 else "正在生成…")

        result = client.stream_chat(
            make_messages(feedback),
            on_delta=on_delta,
            should_stop=should_stop,
            max_tokens=GEN_MAX_TOKENS,
            temperature=1.0,
        )

        if on_usage is not None and result.usage:
            on_usage(result.model, result.usage, reason)

        text = (result.content or "").strip()
        attempt = WorldGenAttempt(index=index, text=text)

        # 撞上 max_tokens 说明文档写到一半断了，必须重来 ——
        # 半截文档比没有更糟，用户可能直接拿去用了
        if result.truncated:
            attempt.truncated = True
            attempt.problem = (
                f"输出被长度上限截断（写到 {len(text)} 字就断了），"
                "文档不完整。请精简内容，控制在字数上限以内"
            )
        else:
            ok, problem = check_structure(text)
            if not ok:
                attempt.problem = problem

        records.append(attempt)

        if attempt.ok:
            document = WorldDocument(
                name=_extract_name(text),
                text=text,
                source_path="（由 AI 生成）",
                encoding="utf-8",
                source_bytes=len(text.encode("utf-8")),
            )
            return WorldGenResult(
                document=document, attempts=records, revised=revised
            )

        feedback = [attempt.problem]
        report(f"第 {index} 次不合格，正在重写…")
        LOG.warn("生成", f"世界观{reason}第 {index} 次不合格：{attempt.problem}")

    problems = [a.problem for a in records if a.problem]
    LOG.error("生成", f"世界观{reason}连续失败", "\n".join(problems))
    raise WorldGenError(problems, attempts)


def generate_world(
    client: DeepSeekClient,
    description: str,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    on_progress: Callable[[str], None] | None = None,
    on_delta: Callable[[str], None] | None = None,
    on_usage: Callable[[str, dict, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> WorldGenResult:
    """按用户的大白话描述生成一份世界观文档。"""
    if not description.strip():
        raise ApiError("请先描述你想玩的世界。", kind="format")

    return _run(
        client,
        lambda feedback: build_generate_messages(description, feedback),
        attempts=attempts,
        revised=False,
        on_progress=on_progress,
        on_delta=on_delta,
        on_usage=on_usage,
        should_stop=should_stop,
    )


def revise_world(
    client: DeepSeekClient,
    current_text: str,
    change_request: str,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    on_progress: Callable[[str], None] | None = None,
    on_delta: Callable[[str], None] | None = None,
    on_usage: Callable[[str, dict, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> WorldGenResult:
    """按用户的修改要求重写已有文档。"""
    if not change_request.strip():
        raise ApiError("请先说明想改什么。", kind="format")

    return _run(
        client,
        lambda feedback: build_revise_messages(current_text, change_request, feedback),
        attempts=attempts,
        revised=True,
        on_progress=on_progress,
        on_delta=on_delta,
        on_usage=on_usage,
        should_stop=should_stop,
    )


def _extract_name(text: str) -> str:
    """从一级标题取文档名。取不到就给个通用名。"""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            name = line[2:].strip()
            if name:
                return name[:40]
        if line:
            break
    return "未命名世界"


#: 界面上给用户的描述示例。写具体一些的例子，比写「请输入描述」有用
DESCRIPTION_EXAMPLES = [
    "一个被海洋完全覆盖的世界，人们住在移动的船城上，靠打捞海底的旧文明遗物为生",
    "蒸汽朋克风格的沙漠世界，城市建在巨大的机械兽背上，机械兽会定期沉睡",
    "一座永远在下雨的山城，雨水会让人遗忘事情，居民靠一种矿石保存记忆",
    "低魔奇幻世界，魔法需要以记忆为代价，越是强大的法师失去的东西越多",
]
