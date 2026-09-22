"""世界观一致性校验。

流程：AI 产出内容 → 交给校验模型判断是否违背世界观 →
不通过就带着冲突原因重新生成 → 最多重试 3 次 → 仍失败则抛错让界面弹窗。

一个关键设计：**「世界观没提到」不算冲突**。
如果把未提及也判为冲突，AI 就只能复述设定原文，
这套「道具事件全部动态生成」的框架直接废掉。
所以提示词里明确区分「与既有设定矛盾」和「合理延伸」，只判前者。

另一个设计：校验本身出错（网络失败、返回的不是合法 json）时**放行内容**。
校验不可用不该让玩家卡死；重试循环是用来处理「内容有问题」的，
不是用来处理「校验器坏了」的。这种情况会记进调试日志。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from core.api_client import ApiError, ChatResult, DeepSeekClient
from core.debuglog import LOG
from core.prompts import JSON_REQUIREMENT, world_block
from core.world import WorldDocument

#: 需求锁定的重试上限
DEFAULT_MAX_RETRIES = 3

#: 校验模型的 max_tokens。只需要输出一个短的 json 结论
VALIDATE_MAX_TOKENS = 800


# ----------------------------------------------------------------------
# 校验结果
# ----------------------------------------------------------------------


@dataclass
class ValidationVerdict:
    """一次校验的结论。"""

    #: 是否通过（含「校验器出错放行」的情况，那种看 error 字段）
    passed: bool = True
    #: 冲突原因（一句话）
    reason: str = ""
    #: 具体冲突点列表
    conflicts: list[str] = field(default_factory=list)
    #: 校验器本身出错时的说明。非空表示这次是放行，不是真的判定通过
    error: str = ""
    #: 模型原始返回，调试用
    raw: str = ""

    @property
    def reliable(self) -> bool:
        """这次校验结论是否可信。"""
        return not self.error

    def summary(self) -> str:
        if self.error:
            return f"校验未完成（{self.error}）"
        if self.passed:
            return "与世界观一致"
        return self.reason or "与世界观存在冲突"

    def detail_lines(self) -> list[str]:
        if self.conflicts:
            return list(self.conflicts)
        return [self.reason] if self.reason else []


# ----------------------------------------------------------------------
# 异常
# ----------------------------------------------------------------------


class ValidationExhausted(Exception):
    """连续多次生成都没能产出可用内容。

    失败可能是两种原因之一，也可能混在一起：
      · 输出格式不合要求（JSON 解析不出来）—— 阶段 7 起的结构化生成
      · 内容与世界观设定冲突
    message 可直接展示给玩家。
    """

    def __init__(self, attempts: list["GenerationAttempt"], max_retries: int):
        self.attempts = attempts
        self.max_retries = max_retries

        reasons: list[str] = []
        for attempt in attempts:
            if attempt.parse_error:
                line = f"输出格式错误：{attempt.parse_error}"
                if line not in reasons:
                    reasons.append(line)
                continue
            for detail in attempt.verdict.detail_lines():
                if detail not in reasons:
                    reasons.append(detail)

        self.reasons = reasons
        detail = "\n".join(f"· {r}" for r in reasons[:6]) or "（未返回具体原因）"

        # 两种失败模式的提示语不一样：格式问题通常与世界观无关
        has_format_issue = any(a.parse_error for a in attempts)
        has_conflict = any(
            not a.parse_error and not a.verdict.passed for a in attempts
        )

        if has_format_issue and not has_conflict:
            headline = f"连续 {max_retries} 次生成的内容格式都不合要求，已放弃本次生成。"
            advice = "建议：换一种操作方式重试；若反复出现，可在调试日志中查看模型的原始输出。"
        else:
            headline = f"连续 {max_retries} 次生成的内容都与世界观设定冲突，已放弃本次生成。"
            advice = "建议：换一种行动方式，或检查世界观文档中是否存在自相矛盾的设定。"

        super().__init__(f"{headline}\n\n问题：\n{detail}\n\n{advice}")


# ----------------------------------------------------------------------
# 尝试记录
# ----------------------------------------------------------------------


@dataclass
class GenerationAttempt:
    """一次生成 + 解析 + 校验的完整记录。"""

    index: int
    content: str
    verdict: ValidationVerdict
    #: 解析回调的产物（阶段 7 起是结构化对象）。纯文本生成时为 None
    parsed: object = None
    #: 解析失败原因。非空表示这一轮根本没走到校验
    parse_error: str = ""
    #: 这一轮生成本身消耗的 token 情况，供用量统计
    generate_usage: dict = field(default_factory=dict)
    validate_usage: dict = field(default_factory=dict)
    model: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict.passed and not self.parse_error


@dataclass
class GuardedResult:
    """带了校验的生成结果。"""

    content: str
    attempts: list[GenerationAttempt]
    #: 解析回调的产物，调用方真正要用的东西
    parsed: object = None

    @property
    def retries_used(self) -> int:
        return max(len(self.attempts) - 1, 0)

    @property
    def had_conflicts(self) -> bool:
        return self.retries_used > 0

    @property
    def final_verdict(self) -> ValidationVerdict:
        return self.attempts[-1].verdict if self.attempts else ValidationVerdict()


# ----------------------------------------------------------------------
# 校验
# ----------------------------------------------------------------------

VALIDATE_SYSTEM = """你是一个世界观一致性校验器。你的唯一职责是判断「待校验内容」是否与「世界观设定」相矛盾。

判断标准（务必严格区分这两种情况）：

【算冲突】内容与世界观既有设定直接矛盾，例如：
  1. 世界观说明某项能力不存在或已失传，内容里却有人在使用它
  2. 与世界观明确规定的规则相反（如设定「灰晶遇水失效」，内容里却用水晶长期储水）
  3. 使用了与世界观地理、组织、人物设定相抵触的名词或事实
  4. 人物的能力、身份、关系与设定矛盾

【不算冲突】以下情况一律判定为「不冲突」：
  1. 世界观只是没有提到，内容做了合理延伸（这是被鼓励的）
  2. 新增了世界观未涉及的地名、人物、物品，且不与既有设定矛盾
  3. 对已有设定做了更细致的描写，但没有改变其基本性质
  4. 文风、语气、长度方面的偏好差异

宁可放过模棱两可的情况，也不要误判 —— 误判会让系统反复重试却始终无法产出内容。

""" + JSON_REQUIREMENT + """

输出格式：
{
  "conflict": true 或 false,
  "reason": "一句话说明冲突原因；不冲突时为空字符串",
  "conflicts": ["具体冲突点1", "具体冲突点2"]
}"""


def build_validate_prompt(
    world: WorldDocument | None, content: str, kind: str = "内容"
) -> str:
    """组装校验请求。"""
    return (
        f"{world_block(world)}\n\n"
        f"【待校验内容（类型：{kind}）】\n{content}"
    )


def parse_verdict(raw: str) -> ValidationVerdict:
    """解析校验模型返回的 json。

    解析失败时返回 error 非空的结论 —— 调用方据此放行并记录日志。
    """
    text = (raw or "").strip()
    if not text:
        return ValidationVerdict(passed=True, error="校验模型返回为空", raw=raw)

    payload = _load_json_object(text)
    if payload is None:
        return ValidationVerdict(
            passed=True, error="校验模型返回的不是合法 json", raw=raw
        )

    conflict = payload.get("conflict")
    if not isinstance(conflict, bool):
        # 容忍模型写成 "true" / "否" 之类
        if isinstance(conflict, str):
            conflict = conflict.strip().lower() in ("true", "yes", "1", "是")
        else:
            return ValidationVerdict(
                passed=True, error="校验结果缺少 conflict 字段", raw=raw
            )

    reason = str(payload.get("reason") or "").strip()

    conflicts: list[str] = []
    raw_conflicts = payload.get("conflicts")
    if isinstance(raw_conflicts, list):
        conflicts = [str(item).strip() for item in raw_conflicts if str(item).strip()]
    elif isinstance(raw_conflicts, str) and raw_conflicts.strip():
        conflicts = [raw_conflicts.strip()]

    return ValidationVerdict(
        passed=not conflict,
        reason=reason,
        conflicts=conflicts,
        raw=raw,
    )


def _load_json_object(text: str) -> dict | None:
    """从模型返回里取出 json 对象。

    即使开了 JSON 模式，模型偶尔仍会用 ```json 包裹，
    所以这里做一层宽容处理。
    """
    import json

    candidates = [text]

    if text.startswith("```"):
        stripped = text.strip("`")
        # 去掉可能的语言标注行
        if "\n" in stripped:
            first, rest = stripped.split("\n", 1)
            if first.strip().lower() in ("json", ""):
                candidates.insert(0, rest)

    # 兜底：截取第一个 { 到最后一个 }
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed

    return None


def validate_content(
    client: DeepSeekClient,
    world: WorldDocument | None,
    content: str,
    *,
    kind: str = "内容",
    on_delta: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[ValidationVerdict, ChatResult | None]:
    """调用校验模型判断内容是否与世界观冲突。

    校验调用本身失败（网络、鉴权等）不抛异常，返回 error 非空的结论，
    由调用方决定放行 —— 见模块开头的说明。
    """
    if not (content or "").strip():
        return ValidationVerdict(passed=True, error="待校验内容为空"), None

    prompt = build_validate_prompt(world, content, kind)

    try:
        result = client.stream_chat(
            [
                {"role": "system", "content": VALIDATE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            on_delta=on_delta,
            should_stop=should_stop,
            json_mode=True,
            max_tokens=VALIDATE_MAX_TOKENS,
            temperature=0,
        )
    except ApiError as exc:
        LOG.warn("校验", f"校验调用失败，本次放行：{exc.message}")
        return ValidationVerdict(
            passed=True, error=f"校验调用失败：{exc.message.splitlines()[0]}"
        ), None

    verdict = parse_verdict(result.content)

    if verdict.error:
        LOG.warn("校验", f"校验结论无法解析，本次放行：{verdict.error}", result.content[:800])
    elif not verdict.passed:
        LOG.info(
            "校验",
            f"检出世界观冲突：{verdict.reason or '（未给出原因）'}",
            "\n".join(verdict.detail_lines()),
        )

    return verdict, result


# ----------------------------------------------------------------------
# 带校验的生成循环
# ----------------------------------------------------------------------


def guarded_generate(
    client: DeepSeekClient,
    world: WorldDocument | None,
    *,
    generate: Callable[[list[str]], str],
    parse: Callable[[str], tuple[object, str]] | None = None,
    render_for_validation: Callable[[object], str] | None = None,
    kind: str = "内容",
    max_retries: int = DEFAULT_MAX_RETRIES,
    on_progress: Callable[[str], None] | None = None,
    on_attempt: Callable[[GenerationAttempt], None] | None = None,
    on_usage: Callable[[str, dict, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> GuardedResult:
    """生成 → 解析 → 校验，任一环节不合格就带着原因重试，最多 max_retries 次。

    generate 接收「上一轮的问题列表」（首次为空），把问题回喂给模型。

    parse 是可选的解析回调，返回 (结果, 错误说明)。
    错误说明非空表示这一轮没生成出可用结构 —— 直接重试，不再浪费一次
    校验调用去检查一段本来就无法使用的文本。

    render_for_validation 把解析结果转成给校验模型看的文本。结构化生成
    时应该提供：让校验模型逐字段读带标签的文本，比让它一边解析 json
    一边判断冲突要可靠得多。

    连续失败时抛 ValidationExhausted，调用方负责弹窗。
    """
    attempts: list[GenerationAttempt] = []
    feedback: list[str] = []

    def report(message: str) -> None:
        if on_progress is not None:
            on_progress(message)

    def record(attempt: GenerationAttempt) -> None:
        attempts.append(attempt)
        if on_attempt is not None:
            on_attempt(attempt)

    for index in range(1, max_retries + 1):
        if should_stop is not None and should_stop():
            break

        report(f"第 {index}/{max_retries} 次生成中…" if index > 1 else "生成中…")

        content = generate(feedback)

        if not (content or "").strip():
            record(
                GenerationAttempt(
                    index=index,
                    content="",
                    verdict=ValidationVerdict(passed=False, reason="生成内容为空"),
                )
            )
            feedback = ["上一次生成的内容为空"]
            continue

        # ---- 结构解析 ----
        parsed: object = None
        if parse is not None:
            parsed, parse_error = parse(content)
            if parse_error:
                record(
                    GenerationAttempt(
                        index=index,
                        content=content,
                        verdict=ValidationVerdict(
                            passed=False, reason=f"输出格式不合要求：{parse_error}"
                        ),
                        parse_error=parse_error,
                    )
                )
                feedback = [f"输出格式错误：{parse_error}"]
                report(f"第 {index} 次输出格式有误，正在重试…")
                continue

        # ---- 世界观校验 ----
        # 结构化生成时改送「逐字段排好的可读文本」而不是原始 json
        check_text = content
        if render_for_validation is not None and parsed is not None:
            rendered = render_for_validation(parsed)
            if (rendered or "").strip():
                check_text = rendered

        report(f"第 {index}/{max_retries} 次校验中…" if index > 1 else "校验中…")
        verdict, check_result = validate_content(
            client, world, check_text, kind=kind, should_stop=should_stop
        )

        # 校验也是一次真实的 API 调用，同样计入用量
        if on_usage is not None and check_result is not None and check_result.usage:
            on_usage(check_result.model, check_result.usage, "世界观校验")

        record(
            GenerationAttempt(
                index=index, content=content, verdict=verdict, parsed=parsed
            )
        )

        if verdict.passed:
            if index > 1:
                report(f"第 {index} 次生成通过校验")
            if verdict.error:
                report("校验未完成（已放行）")
                LOG.info("校验", f"本次内容未经有效校验即放行：{verdict.error}")
            return GuardedResult(
                content=content, attempts=attempts, parsed=parsed
            )

        feedback = verdict.detail_lines() or [verdict.reason or "与世界观冲突"]
        report(f"第 {index} 次未通过校验，正在调整…")

    # 全部尝试失败
    LOG.error(
        "校验",
        f"{max_retries} 次生成均未产出可用内容",
        "\n".join(
            f"第{a.index}次：{a.parse_error or '；'.join(a.verdict.detail_lines()) or a.verdict.reason}"
            for a in attempts
        ),
    )
    raise ValidationExhausted(attempts, max_retries)
