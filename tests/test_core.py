"""核心层回归测试。

不依赖 pytest，直接跑：
    .venv/Scripts/python.exe tests/test_core.py

所有用例都在临时目录里操作，不会碰 data/ 下的真实配置、存档与世界观。
每完成一个阶段就往下追加一组，改动了底层结构时先跑这个。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import AppConfig, ConfigStore  # noqa: E402
from core.models import Item, WorldState  # noqa: E402
from core.savegame import (  # noqa: E402
    GameSave,
    HistoryLog,
    PlayerState,
    SaveFormatError,
    SaveStore,
)
from core.world import (  # noqa: E402
    WorldDocument,
    compress_for_context,
    import_world_file,
)

_results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((name, bool(condition), detail))


# ----------------------------------------------------------------------
# 阶段 2：配置
# ----------------------------------------------------------------------


def test_config(tmp: pathlib.Path) -> None:
    path = tmp / "config.json"
    store = ConfigStore(path=path)

    check("阶段2 缺失文件回退默认", store.load().model == "deepseek-flash")

    config = AppConfig(api_key="sk-test-abcdefghijklmn", debug_log=True, timeout=45)
    store.save(config)
    check("阶段2 配置往返一致", store.load().to_dict() == config.to_dict())
    check("阶段2 Key 掩码不泄露", store.load().masked_key() == "sk-tes******klmn")
    check(
        "阶段2 权限收紧(POSIX)",
        os.name != "posix" or oct(path.stat().st_mode)[-3:] == "600",
    )

    path.write_text("{ 这不是合法 JSON", encoding="utf-8")
    check("阶段2 损坏配置不抛异常", store.load().api_key == "")

    path.write_text('{"timeout": "abc", "debug_log": 1}', encoding="utf-8")
    recovered = store.load()
    check("阶段2 字段类型错误回退", recovered.timeout == 60 and recovered.debug_log is True)

    check(
        "阶段2 URL 归一化",
        AppConfig(base_url="https://api.deepseek.com/v1/").normalized_base_url()
        == "https://api.deepseek.com/v1",
    )


# ----------------------------------------------------------------------
# 阶段 3：世界观
# ----------------------------------------------------------------------


def test_world(tmp: pathlib.Path) -> None:
    cases = [
        ("utf-8 无 BOM", "世界", "utf-8"),
        ("utf-8 带 BOM", "世界", "utf-8-sig"),
        ("gbk", "北境王庭的骑士团", "gbk"),
        ("big5", "北境王庭的騎士團", "big5"),
        ("utf-16", "记事本另存的文档", "utf-16"),
    ]
    for label, text, encoding in cases:
        path = tmp / f"enc-{encoding}.txt"
        path.write_bytes(text.encode(encoding))
        document = import_world_file(path)
        # 关键：GBK 与 Big5 互相能解成功但会产生乱码，
        # 必须比对文本内容，不能只看有没有抛异常
        check(f"阶段3 编码识别 {label}", document.text.strip() == text, document.encoding)

    for bad_suffix in (".pdf", ".docx"):
        path = tmp / f"bad{bad_suffix}"
        path.write_text("x", encoding="utf-8")
        try:
            import_world_file(path)
            check(f"阶段3 拒绝 {bad_suffix}", False)
        except Exception:
            check(f"阶段3 拒绝 {bad_suffix}", True)

    empty = tmp / "empty.md"
    empty.write_text("   \n  ", encoding="utf-8")
    try:
        import_world_file(empty)
        check("阶段3 拒绝空文件", False)
    except Exception:
        check("阶段3 拒绝空文件", True)

    # 短文档原样保留
    short = "# 设定\n\n一段简短的世界观。"
    out, compressed = compress_for_context(short, 12000)
    check("阶段3 短文档不压缩", out == short and not compressed)

    # 长文档压缩。注意总量必须真的超过预算，
    # 否则测的是「不压缩」分支而不是压缩分支
    # （12 节 × 60 行只有 11690 字，差一点没到 12000，会走错分支）
    sections = [
        f"## 第{i}章 卷宗\n\n" + ("北境的冰墙之下埋藏着远古封印。\n" * 80)
        for i in range(1, 13)
    ]
    long_text = "# 世界设定\n\n导言。\n\n" + "\n\n".join(sections)
    check("阶段3 测试文档确实超预算", len(long_text) > 12000)
    out, compressed = compress_for_context(long_text, 12000)
    check("阶段3 长文档被压缩", compressed and len(out) <= 12000)
    check(
        "阶段3 标题全部保留",
        all(f"## 第{i}章" in out for i in range(1, 13)),
    )

    # 无结构长文本走首尾截断，两端都要保留
    plain = "这是一段没有任何标题的纯文本。" * 3000
    out, compressed = compress_for_context(plain, 12000)
    check("阶段3 无标题文档在预算内", compressed and len(out) <= 12000)
    check(
        "阶段3 无标题文档首尾均保留",
        out.startswith(plain[:20]) and out.endswith(plain[-20:]),
    )

    document = WorldDocument(name="测试世界", text="正文")
    check(
        "阶段3 摘要优先于原文",
        WorldDocument(name="x", text="原文", summary="摘要").context_text()[0] == "摘要",
    )
    check("阶段3 文档往返", WorldDocument.from_dict(document.to_dict()).name == "测试世界")


# ----------------------------------------------------------------------
# 阶段 4：存档
# ----------------------------------------------------------------------


def test_savegame(tmp: pathlib.Path) -> None:
    store = SaveStore(directory=tmp / "saves", slot_count=5)
    check("阶段4 空仓库槽位数", len(store.list_slots()) == 5)
    check("阶段4 初始全空", all(not s.exists for s in store.list_slots()))

    # 滚动摘要：无论玩多久，长度必须有界
    # 303 而不是 300：最后一个回合若是折叠点，就没有「未折叠回合」可测
    rounds = 303
    log = HistoryLog()
    compact_points: list[int] = []
    for turn in range(rounds):
        if log.record(f"回合{turn}：在灰烬港下城与码头帮交涉，取得一枚灰晶。"):
            log.compact_locally()
            compact_points.append(turn)

    check("阶段4 摘要有硬上限", len(log.summary) <= log.MAX_SUMMARY_CHARS)
    check("阶段4 recent 窗口有界", len(log.recent) <= log.RECENT_WINDOW)
    check(
        "阶段4 折叠按 COMPACT_EVERY 触发",
        all(b - a == HistoryLog.COMPACT_EVERY for a, b in zip(compact_points, compact_points[1:])),
    )

    # 最后一次折叠之后录入的回合还没被折叠，应留在 recent 窗口里。
    # 到底有几个取决于 rounds 与 COMPACT_EVERY 的整除关系，
    # 所以这里按实际折叠点推算，不要写死回合号。
    last_folded = compact_points[-1]
    unfolded = list(range(last_folded + 1, rounds))

    check("阶段4 摘要保留早期经历", "回合0：" in log.summary)
    check("阶段4 摘要尾部保留最近折叠", f"回合{last_folded}" in log.summary)
    check("阶段4 本用例含未折叠回合", bool(unfolded), f"rounds={rounds}")
    check(
        "阶段4 未折叠回合留在窗口且不在摘要",
        all(
            f"回合{turn}" in log.context_text() and f"回合{turn}" not in log.summary
            for turn in unfolded
        ),
    )
    # 中段被牺牲是设计取舍，不是 bug
    check("阶段4 摘要丢弃中段", "回合150" not in log.summary)

    oversized = HistoryLog()
    oversized.apply_compacted("很长的摘要" * 2000)
    check(
        "阶段4 超限摘要保留头尾",
        len(oversized.summary) <= oversized.MAX_SUMMARY_CHARS
        and oversized.summary.startswith("很长的摘要")
        and oversized.summary.endswith("很长的摘要"),
    )

    # 损坏存档
    (tmp / "saves").mkdir(parents=True, exist_ok=True)
    (tmp / "saves" / "save_3.json").write_text("{ 坏 JSON", encoding="utf-8")
    info = store.slot_info(3)
    check("阶段4 损坏档被标记", info.exists and info.corrupted)
    try:
        store.load(3)
        check("阶段4 读取损坏档报错", False)
    except SaveFormatError:
        check("阶段4 读取损坏档报错", True)

    # 完整往返
    save = GameSave(
        slot=1,
        turns=42,
        player=PlayerState(
            name="凌昭",
            attributes={"灰烬亲和": "中", "体质": "7"},
            status=["轻度灰化"],
        ),
        inventory=[
            Item(name="灰晶核", rarity="史诗", category="材料", effect="大幅回复烬火"),
            Item(name="灰舟", rarity="精良", category="载具"),
        ],
        world_state=WorldState(
            location="灰烬港·下城",
            time="灰烬三十七年·秋",
            factions=[{"name": "码头帮", "relation": "敌对"}],
            flags=["欠码头帮一笔钱"],
        ),
        world=WorldDocument(name="灰烬纪元", text="# 世界设定\n\n正文。"),
        history=log,
    )
    store.save(save)
    loaded = store.load(1)

    check(
        "阶段4 存档往返完整",
        loaded.turns == 42
        and loaded.player.name == "凌昭"
        and loaded.player.attributes == {"灰烬亲和": "中", "体质": "7"}
        and [i.name for i in loaded.inventory] == ["灰晶核", "灰舟"]
        and loaded.world_state.location == "灰烬港·下城"
        and loaded.world_state.factions == [{"name": "码头帮", "relation": "敌对"}]
        and loaded.world.name == "灰烬纪元"
        and loaded.inventory[0].effect == "大幅回复烬火",
    )

    raw = (tmp / "saves" / "save_1.json").read_text(encoding="utf-8")
    check("阶段4 存档不含 api_key", "api_key" not in raw and "sk-" not in raw)
    check("阶段4 存档含全部必需字段",
          all(k in raw for k in ("player", "inventory", "world_state", "world", "history")))

    # 版本兼容
    import json

    bumped = json.loads(raw)
    bumped["version"] = 99
    (tmp / "saves" / "save_4.json").write_text(
        json.dumps(bumped, ensure_ascii=False), encoding="utf-8"
    )
    try:
        store.load(4)
        check("阶段4 拒绝高版本存档", False)
    except SaveFormatError:
        check("阶段4 拒绝高版本存档", True)

    try:
        store.save(GameSave(slot=99))
        check("阶段4 拒绝越界槽位", False)
    except SaveFormatError:
        check("阶段4 拒绝越界槽位", True)

    check("阶段4 最新槽位", store.latest_slot() == 1)

    # 单件道具损坏不该拖垮整个存档
    broken = json.loads(raw)
    broken["slot"] = 5
    broken["inventory"] = ["不是对象", {"name": "正常道具", "rarity": "稀有"}]
    (tmp / "saves" / "save_5.json").write_text(
        json.dumps(broken, ensure_ascii=False), encoding="utf-8"
    )
    salvaged = store.load(5)
    check("阶段4 跳过损坏道具条目", len(salvaged.inventory) == 1
          and salvaged.inventory[0].name == "正常道具")


# ----------------------------------------------------------------------
# 阶段 5：计价与用量
# ----------------------------------------------------------------------


def test_pricing() -> None:
    from datetime import datetime, timezone

    from core.pricing import (
        MODEL_PRICING,
        calculate_cost,
        cost_from_usage,
        is_peak_now,
        resolve_model,
    )

    # 逐项核对官方价表：100 万 token 的花费应恰好等于标价
    for model in ("deepseek-flash", "deepseek-v4-pro"):
        for tier, peak in (("peak", True), ("off_peak", False)):
            rates = MODEL_PRICING[model][tier]
            cost = calculate_cost(
                model,
                cache_hit_tokens=1_000_000,
                cache_miss_tokens=1_000_000,
                output_tokens=1_000_000,
                peak=peak,
            )
            check(
                f"阶段5 价表 {model}/{tier}",
                abs(cost.input_cost - (rates["cache_hit"] + rates["cache_miss"])) < 1e-9
                and abs(cost.output_cost - rates["output"]) < 1e-9,
            )

    # 官方明确：低谷价 = 高峰价的一半
    for model in ("deepseek-flash", "deepseek-v4-pro"):
        peak_cost = calculate_cost(model, cache_miss_tokens=10**6, output_tokens=10**6, peak=True)
        off_cost = calculate_cost(model, cache_miss_tokens=10**6, output_tokens=10**6, peak=False)
        check(
            f"阶段5 {model} 低谷为高峰之半",
            abs(peak_cost.total_usd / off_cost.total_usd - 2.0) < 1e-9,
        )

    # 峰谷时段：UTC 周一至周五 01:00-04:00 与 06:00-10:00，左闭右开
    windows = [
        ("周一 00:59 低谷", datetime(2026, 9, 21, 0, 59, tzinfo=timezone.utc), False),
        ("周一 01:00 高峰", datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc), True),
        ("周一 03:59 高峰", datetime(2026, 9, 21, 3, 59, tzinfo=timezone.utc), True),
        ("周一 04:00 低谷", datetime(2026, 9, 21, 4, 0, tzinfo=timezone.utc), False),
        ("周一 05:59 低谷", datetime(2026, 9, 21, 5, 59, tzinfo=timezone.utc), False),
        ("周一 06:00 高峰", datetime(2026, 9, 21, 6, 0, tzinfo=timezone.utc), True),
        ("周一 09:59 高峰", datetime(2026, 9, 21, 9, 59, tzinfo=timezone.utc), True),
        ("周一 10:00 低谷", datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc), False),
        ("周六 02:00 低谷", datetime(2026, 9, 26, 2, 0, tzinfo=timezone.utc), False),
        ("周日 07:00 低谷", datetime(2026, 9, 27, 7, 0, tzinfo=timezone.utc), False),
    ]
    for label, moment, expected in windows:
        check(f"阶段5 峰谷 {label}", is_peak_now(moment) is expected)

    # 别名归一：官方说明旧名仍被接受，按 Flash 计费
    for alias in ("deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash"):
        check(f"阶段5 别名 {alias} 归一到 Flash", resolve_model(alias) == "deepseek-flash")
    check("阶段5 未知模型兜底", resolve_model("不存在的模型") == "deepseek-flash")

    # usage 缺缓存拆分时，全部按未命中计 —— 宁可估高不估低
    no_cache_split = cost_from_usage(
        "deepseek-flash", {"prompt_tokens": 1000, "completion_tokens": 500}, peak=False
    )
    check(
        "阶段5 无缓存拆分时按未命中计",
        no_cache_split.cache_miss_tokens == 1000 and no_cache_split.cache_hit_tokens == 0,
    )

    # 实测返回的完整 usage 结构
    real_usage = {
        "prompt_tokens": 13,
        "completion_tokens": 51,
        "total_tokens": 64,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 13,
    }
    cost = cost_from_usage("deepseek-flash", real_usage, peak=False)
    expected = (13 * 0.15 + 51 * 0.60) / 1_000_000
    check("阶段5 实测 usage 计价", abs(cost.total_usd - expected) < 1e-12)


def test_usage(tmp: pathlib.Path) -> None:
    from core.usage import UsageTotals, UsageTracker

    tracker = UsageTracker(path=tmp / "usage.json")
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 200,
        "prompt_cache_hit_tokens": 40,
        "prompt_cache_miss_tokens": 60,
        "completion_tokens_details": {"reasoning_tokens": 0},
    }

    record = tracker.record(
        model="deepseek-flash", usage=usage, reason="剧情", peak=False
    )
    check("阶段5 记录 token 拆分",
          record.prompt_tokens == 100 and record.completion_tokens == 200)
    check("阶段5 记录花费为正", record.cost_usd > 0)
    check("阶段5 session 与 lifetime 同步",
          tracker.session.calls == 1 and tracker.lifetime.calls == 1)
    check("阶段5 按模型归集", "deepseek-flash" in tracker.by_model)

    # 别名应该归到同一个模型桶里，而不是各算各的
    tracker.record(model="deepseek-chat", usage=usage, reason="剧情", peak=False)
    check(
        "阶段5 别名归入同一模型桶",
        list(tracker.by_model.keys()) == ["deepseek-flash"]
        and tracker.by_model["deepseek-flash"].calls == 2,
    )

    # 持久化往返
    reloaded = UsageTracker(path=tmp / "usage.json")
    reloaded.load()
    check("阶段5 累计持久化往返", reloaded.lifetime.calls == 2
          and abs(reloaded.lifetime.cost_usd - tracker.lifetime.cost_usd) < 1e-12)
    check("阶段5 最近记录持久化", len(reloaded.recent) == 2)

    # 重置只清历史累计，本次运行的统计要保留。
    # 这里必须用 tracker（session 里有 2 次）来断言，
    # reloaded 是从磁盘加载的，它的 session 本来就是空的。
    tracker.reset_lifetime()
    check("阶段5 重置只清历史", tracker.lifetime.calls == 0
          and tracker.session.calls == 2)

    # 统计文件不含正文内容
    raw = (tmp / "usage.json").read_text(encoding="utf-8")
    check("阶段5 统计文件不含 prompt 原文",
          "content" not in raw and "message" not in raw)

    # 损坏文件不该让程序起不来
    (tmp / "usage.json").write_text("{ 坏", encoding="utf-8")
    broken = UsageTracker(path=tmp / "usage.json")
    broken.load()
    check("阶段5 损坏统计文件不抛异常", broken.lifetime.calls == 0)

    check("阶段5 UsageTotals 类型兜底",
          UsageTotals.from_dict({"calls": "abc", "cost_usd": None}).calls == 0)


# ----------------------------------------------------------------------
# 阶段 6：世界观一致性校验
# ----------------------------------------------------------------------


class _FakeClient:
    """假的 API 客户端：按预设脚本回应校验请求。

    只有 json_mode=True 的调用（也就是校验）会被应答，
    其余一律视为测试写错，直接抛错。
    """

    def __init__(self, verdict_responses: list):
        self._responses = list(verdict_responses)
        self.validate_prompts: list[str] = []

    def stream_chat(self, messages, **kwargs):
        from core.api_client import ChatResult

        if not kwargs.get("json_mode"):
            raise AssertionError("guarded_generate 只应调用校验接口，不应自己生成")
        self.validate_prompts.append(messages[-1]["content"])
        payload = self._responses.pop(0)
        if isinstance(payload, BaseException):
            raise payload
        return ChatResult(content=payload, model="fake", usage={})


def test_validator() -> None:
    import json

    from core.api_client import ApiError
    from core.prompts import retry_note
    from core.validator import (
        ValidationExhausted,
        guarded_generate,
        parse_verdict,
    )
    from core.world import WorldDocument

    # ---------- 结论解析 ----------
    ok = parse_verdict('{"conflict": false, "reason": "", "conflicts": []}')
    check("阶段6 解析 无冲突", ok.passed and ok.reliable and not ok.conflicts)

    bad = parse_verdict(
        '{"conflict": true, "reason": "灰晶遇水", "conflicts": ["设定说遇水失效，内容却用来储水"]}'
    )
    check(
        "阶段6 解析 有冲突",
        not bad.passed and bad.reason == "灰晶遇水" and len(bad.conflicts) == 1,
    )

    # 即使开了 json 模式，模型偶尔仍会包 ```json
    wrapped = parse_verdict('```json\n{"conflict": false}\n```')
    check("阶段6 解析 剥掉代码块", wrapped.passed and wrapped.reliable)

    # 前后有解释文字时，截取第一个 { 到最后一个 }
    noisy = parse_verdict('好的，我的判断是：{"conflict": false} 以上。')
    check("阶段6 解析 忽略前后噪音", noisy.passed and noisy.reliable)

    # 模型把布尔写成字符串
    strbool = parse_verdict('{"conflict": "true", "reason": "x"}')
    check("阶段6 解析 字符串布尔", not strbool.passed)

    # 解析不出来时必须标记为「不可信」，而不是当成通过
    for label, payload in [
        ("空返回", ""),
        ("非 json", "我觉得没问题"),
        ("缺字段", '{"reason": "没有 conflict 字段"}'),
    ]:
        verdict = parse_verdict(payload)
        check(
            f"阶段6 解析失败标记不可信 {label}",
            verdict.error != "" and not verdict.reliable,
        )

    # ---------- 重试循环 ----------
    world = WorldDocument(name="灰烬纪元", text="灰晶遇水会失效。")

    def make_generate(contents: list[str], seen: list[list[str]]):
        def generate(reasons: list[str]) -> str:
            seen.append(list(reasons))
            return contents[min(len(seen) - 1, len(contents) - 1)]

        return generate

    # 一次通过
    client = _FakeClient(['{"conflict": false}'])
    seen: list[list[str]] = []
    result = guarded_generate(
        client, world, generate=make_generate(["内容A"], seen), max_retries=3
    )
    check("阶段6 一次通过", result.content == "内容A" and len(result.attempts) == 1)
    check("阶段6 一次通过时无重试", not result.had_conflicts and result.retries_used == 0)
    check("阶段6 首次生成不带冲突反馈", seen == [[]])

    # 校验提示词里必须带上世界观原文
    check("阶段6 校验请求含世界观", "灰晶遇水会失效" in client.validate_prompts[0])

    # 冲突两次后通过，且冲突原因要回喂给下一轮生成
    client = _FakeClient([
        '{"conflict": true, "reason": "矛盾A", "conflicts": ["冲突点A"]}',
        '{"conflict": true, "reason": "矛盾B", "conflicts": ["冲突点B"]}',
        '{"conflict": false}',
    ])
    seen = []
    result = guarded_generate(
        client, world, generate=make_generate(["内容A", "内容B", "内容C"], seen),
        max_retries=3,
    )
    check("阶段6 重试后通过", result.content == "内容C" and result.retries_used == 2)
    check(
        "阶段6 冲突原因被回喂给生成",
        seen[0] == [] and seen[1] == ["冲突点A"] and seen[2] == ["冲突点B"],
    )

    # 连续 3 次失败要抛 ValidationExhausted，且信息可读
    client = _FakeClient(['{"conflict": true, "reason": "一直冲突", "conflicts": ["点X"]}'] * 3)
    seen = []
    try:
        guarded_generate(
            client, world, generate=make_generate(["A", "B", "C"], seen), max_retries=3
        )
        check("阶段6 连续失败抛异常", False)
    except ValidationExhausted as exc:
        check("阶段6 连续失败抛异常", True)
        check("阶段6 异常含重试次数", exc.max_retries == 3 and len(exc.attempts) == 3)
        check("阶段6 异常含冲突点", "点X" in str(exc))
        check("阶段6 异常文案可直接给玩家", "世界观" in str(exc))

    # 重试上限可配：设成 1 就只试一次
    client = _FakeClient(['{"conflict": true, "reason": "x"}'])
    try:
        guarded_generate(
            client, world, generate=make_generate(["A"], []), max_retries=1
        )
        check("阶段6 重试上限可配", False)
    except ValidationExhausted as exc:
        check("阶段6 重试上限可配", len(exc.attempts) == 1)

    # 生成空内容也算一次失败，继续重试
    client = _FakeClient(['{"conflict": false}'])
    seen = []
    result = guarded_generate(
        client, world, generate=make_generate(["", "有内容了"], seen), max_retries=2
    )
    check("阶段6 空内容触发重试", result.content == "有内容了" and len(result.attempts) == 2)

    # ---------- 校验器自身出问题 → 放行 ----------
    # 关键设计：重试循环是用来处理「内容有问题」的，
    # 不是用来处理「校验器坏了」的。校验不可用不该让玩家卡死。
    client = _FakeClient(["这不是 json"])
    seen = []
    result = guarded_generate(
        client, world, generate=make_generate(["内容A"], seen), max_retries=3
    )
    check(
        "阶段6 校验结论无法解析时放行",
        result.content == "内容A"
        and len(result.attempts) == 1
        and not result.final_verdict.reliable,
    )

    failing = _FakeClient([ApiError("网络炸了", kind="network")])
    seen = []
    result = guarded_generate(
        failing, world, generate=make_generate(["内容B"], seen), max_retries=3
    )
    check(
        "阶段6 校验调用失败时放行",
        result.content == "内容B" and len(result.attempts) == 1,
    )

    # ---------- 提示词约束 ----------
    # 这条是整个模块能不能用的前提：如果把「世界观没提到」也判成冲突，
    # AI 就只能复述设定，动态生成直接废掉。断言提示词里写明了这条。
    from core.validator import VALIDATE_SYSTEM

    check(
        "阶段6 提示词区分「未提及」与「冲突」",
        "不算冲突" in VALIDATE_SYSTEM and "没有提到" in VALIDATE_SYSTEM,
    )
    check("阶段6 提示词要求 json", "json" in VALIDATE_SYSTEM.lower())

    note = retry_note(["冲突点A", "冲突点B"])
    check("阶段6 重试说明含全部冲突点", "冲突点A" in note and "冲突点B" in note)
    check("阶段6 无冲突时重试说明为空", retry_note([]) == "")

    # 没有世界观时不该报错，且提示词要说明「可自由发挥」
    worldless = _FakeClient(['{"conflict": false}'])
    result = guarded_generate(
        worldless, None, generate=make_generate(["内容"], []), max_retries=1
    )
    check("阶段6 无世界观时可正常生成", result.content == "内容")
    check("阶段6 无世界观提示词有说明", "尚未导入" in worldless.validate_prompts[0])


# ----------------------------------------------------------------------
# 阶段 7：道具生成
# ----------------------------------------------------------------------


class _FakeItemClient:
    """假的 API 客户端，按调用类型分发预设回应。

    道具生成和世界观校验**都**走 json_mode，所以不能靠这个区分，
    要看 system 提示词的开头。
    """

    def __init__(self, generation: list, verdicts: list):
        self._generation = list(generation)
        self._verdicts = list(verdicts)
        self.generation_prompts: list[str] = []
        self.validate_prompts: list[str] = []

    def stream_chat(self, messages, **kwargs):
        from core.api_client import ChatResult, ApiError

        system = messages[0]["content"]
        if system.startswith("你是一个文字游戏的道具生成器"):
            self.generation_prompts.append(messages[-1]["content"])
            payload = self._generation.pop(0)
            if isinstance(payload, BaseException):
                raise payload
            return ChatResult(content=payload, model="fake", usage={"prompt_tokens": 10})

        self.validate_prompts.append(messages[-1]["content"])
        payload = self._verdicts.pop(0)
        if isinstance(payload, ApiError):
            raise payload
        return ChatResult(content=payload, model="fake", usage={"prompt_tokens": 5})


def test_items() -> None:
    from core.items import (
        ALL_TRIGGERS,
        TRIGGER_HINTS,
        ItemRequest,
        build_item_prompt,
        generate_items,
        parse_items,
    )
    from core.models import Item, WorldState
    from core.savegame import PlayerState
    from core.validator import ValidationExhausted
    from core.world import WorldDocument

    # ---------- 结构解析 ----------
    items, error = parse_items(
        '{"items":[{"name":"灰晶砂","category":"材料","rarity":"精良",'
        '"description":"d","lore":"l","effect":"e"}]}'
    )
    check(
        "阶段7 解析标准结构",
        error == "" and len(items) == 1 and items[0].name == "灰晶砂"
        and items[0].rarity == "精良" and items[0].category == "材料",
    )

    for label, raw in [
        ("代码块包裹", '```json\n{"items":[{"name":"A"}]}\n```'),
        ("前后有说明", '好的：{"items":[{"name":"B"}]} 以上'),
        ("单个对象而非数组", '{"items":{"name":"C"}}'),
    ]:
        parsed, err = parse_items(raw)
        check(f"阶段7 解析宽容 {label}", err == "" and len(parsed) == 1)

    for label, raw in [
        ("非 json", "我觉得应该给玩家一把剑"),
        ("items 为空", '{"items":[]}'),
        ("缺 items 字段", '{"data":[]}'),
        ("条目缺 name", '{"items":[{"category":"材料"}]}'),
        ("空字符串", ""),
    ]:
        parsed, err = parse_items(raw)
        check(f"阶段7 解析拒绝 {label}", parsed == [] and err != "")

    # 坏条目跳过，好条目保留
    mixed, err = parse_items('{"items":["字符串",{"name":"D"}]}')
    check("阶段7 混入坏条目时保留好的", err == "" and [i.name for i in mixed] == ["D"])

    # ---------- 稀有度归一 ----------
    from core.items import _normalize_rarity

    for raw, expected in [
        ("稀有", "稀有"),
        ("稀有度：史诗", "史诗"),      # 「稀有度」三字本身含「稀有」，不能先命中它
        ("稀有度: 传说", "传说"),
        ("品质：精良", "精良"),
        ("Legendary", "传说"),
        ("EPIC", "史诗"),
        ("uncommon", "精良"),
        ("很普通", "普通"),
        ("", "普通"),
        ("乱七八糟", "普通"),
    ]:
        got = _normalize_rarity(raw)
        check(f"阶段7 稀有度归一 {raw!r}", got == expected, f"得到 {got}")

    # ---------- 字段截断 ----------
    long_items, _ = parse_items(
        '{"items":[{"name":"' + "长" * 100 + '","lore":"' + "背" * 900 + '"}]}'
    )
    check(
        "阶段7 字段截断在限额内（含省略号）",
        len(long_items[0].name) == 40 and len(long_items[0].lore) == 500,
    )

    # ---------- 提示词 ----------
    for trigger in ALL_TRIGGERS:
        check(f"阶段7 触发场景有说明 {trigger}", TRIGGER_HINTS.get(trigger, "") != "")

    world = WorldDocument(name="灰烬纪元", text="灰晶遇水会失效。")
    inventory = [Item(name="火证", rarity="普通", category="信物")]
    prompt = build_item_prompt(
        world,
        ItemRequest(trigger="开箱", count=2, scene="码头帮仓库", rarity_hint="稀有"),
        player=PlayerState(name="凌昭"),
        state=WorldState(location="灰烬港·下城"),
        inventory=inventory,
    )
    check("阶段7 提示词含世界观", "灰晶遇水会失效" in prompt)
    check("阶段7 提示词含位置", "灰烬港·下城" in prompt)
    check("阶段7 提示词含玩家属性", "凌昭" in prompt)
    # 不带背包的话，AI 会反复生成玩家已经有的东西
    check("阶段7 提示词含现有背包", "火证" in prompt)
    check("阶段7 提示词禁止重复", "请勿生成与上述道具重复" in prompt)
    check("阶段7 提示词含触发场景", "开箱" in prompt and "码头帮仓库" in prompt)

    # ---------- 生成循环 ----------
    def make_client(generations, verdicts):
        return _FakeItemClient(generations, verdicts)

    good_json = '{"items":[{"name":"铁匣","rarity":"精良","category":"容器"}]}'
    ok_verdict = '{"conflict": false}'

    client = make_client([good_json], [ok_verdict])
    result = generate_items(
        client, world, ItemRequest(trigger="开箱", count=1), max_retries=3
    )
    check(
        "阶段7 一次生成成功",
        len(result.items) == 1 and result.items[0].name == "铁匣"
        and not result.had_conflicts,
    )
    check("阶段7 校验用的是可读文本", "名称：铁匣" in client.validate_prompts[0])

    # 格式错误 → 重试，且不该浪费一次校验调用
    client = make_client(["这不是 json", good_json], [ok_verdict])
    result = generate_items(
        client, world, ItemRequest(trigger="开箱", count=1), max_retries=3
    )
    check(
        "阶段7 解析失败会重试",
        len(result.items) == 1 and result.retries_used == 1
        and result.attempts[0].parse_error != "",
    )
    check(
        "阶段7 解析失败不消耗校验调用",
        len(client.validate_prompts) == 1,
    )
    # 格式错误必须以「格式」的形式回喂，而不是说成世界观冲突
    check(
        "阶段7 格式错误回喂给下一轮",
        "输出格式错误" in client.generation_prompts[1],
    )

    # 世界观冲突 → 重试
    conflict_verdict = '{"conflict": true, "reason": "冲突", "conflicts": ["点A"]}'
    client = make_client([good_json, good_json], [conflict_verdict, ok_verdict])
    result = generate_items(
        client, world, ItemRequest(trigger="开箱", count=1), max_retries=3
    )
    check("阶段7 冲突后重试成功", result.retries_used == 1 and len(result.items) == 1)
    check("阶段7 冲突点回喂给下一轮", "点A" in client.generation_prompts[1])

    # 连续失败
    client = make_client([good_json] * 3, [conflict_verdict] * 3)
    try:
        generate_items(client, world, ItemRequest(trigger="开箱", count=1), max_retries=3)
        check("阶段7 连续冲突抛异常", False)
    except ValidationExhausted as exc:
        check("阶段7 连续冲突抛异常", len(exc.attempts) == 3)

    # 全是格式错误时，提示语要说「格式」而不是「世界观冲突」
    client = make_client(["坏", "坏", "坏"], [])
    try:
        generate_items(client, world, ItemRequest(trigger="开箱", count=1), max_retries=3)
        check("阶段7 连续格式错误抛异常", False)
    except ValidationExhausted as exc:
        check("阶段7 连续格式错误抛异常", len(exc.attempts) == 3)
        check("阶段7 格式错误提示语准确", "格式" in str(exc) and "世界观设定冲突" not in str(exc))

    # 生成多了要按请求数量截断
    three = ('{"items":[{"name":"A"},{"name":"B"},{"name":"C"}]}')
    client = make_client([three], [ok_verdict])
    result = generate_items(
        client, world, ItemRequest(trigger="探索", count=2), max_retries=3
    )
    check("阶段7 超出数量被截断", len(result.items) == 2)

    # 用量回调：生成与校验两次调用都要上报
    reported: list[str] = []
    client = make_client([good_json], [ok_verdict])
    generate_items(
        client, world, ItemRequest(trigger="探索", count=1), max_retries=3,
        on_usage=lambda model, usage, reason: reported.append(reason),
    )
    check(
        "阶段7 生成与校验分别上报用量",
        sorted(reported) == ["世界观校验", "道具生成"],
        str(reported),
    )


# ----------------------------------------------------------------------
# 阶段 8：事件生成与毁灭约束
# ----------------------------------------------------------------------


def test_doom() -> None:
    from core.doom import (
        DOOM_MAX,
        MAX_DELTA_PER_TURN,
        DoomState,
        ceiling_for,
        level_name,
    )

    # 每个等级都要有明确的描写上限，含糊的约束模型不会遵守
    for level in range(DOOM_MAX + 1):
        check(f"阶段8 等级 {level} 有说明", bool(level_name(level)))
        check(f"阶段8 等级 {level} 有描写上限", len(ceiling_for(level)) > 20)

    doom = DoomState()
    check("阶段8 初始为 0 级", doom.level == 0 and not doom.evidence)
    check("阶段8 0 级禁止世界级威胁", "禁止" in doom.ceiling)

    # 有理由的推进被接受
    applied, note = doom.advance(1, "玩家执意开启了封存的门")
    check("阶段8 有理由的推进被接受", applied == 1 and doom.level == 1)
    check("阶段8 推进留下履历", len(doom.evidence) == 1 and "封存的门" in doom.evidence[0])

    # 无理由的推进必须被丢弃 —— 这是防止模型乱推的关键
    applied, note = doom.advance(1, "")
    check("阶段8 无理由的推进被丢弃", applied == 0 and doom.level == 1)
    check("阶段8 丢弃时给出说明", "没有给出理由" in note)

    applied, _ = doom.advance(1, "   ")
    check("阶段8 纯空白理由也被丢弃", applied == 0 and doom.level == 1)

    # 单回合增量封顶
    applied, _ = doom.advance(99, "一次性推满")
    check(
        f"阶段8 单回合增量封顶 {MAX_DELTA_PER_TURN}",
        applied == MAX_DELTA_PER_TURN and doom.level == 2,
    )

    # 推进到满级后不再累加
    while doom.level < DOOM_MAX:
        doom.advance(1, f"第 {doom.level} 次重大选择")
    check("阶段8 可达满级", doom.level == DOOM_MAX and doom.at_max)

    applied, note = doom.advance(1, "再来一次")
    check("阶段8 满级后不再累积", applied == 0 and "已满" in note)
    check("阶段8 满级后履历不再增长", len(doom.evidence) == DOOM_MAX)

    # 0 到满级至少需要 DOOM_MAX 次独立的有理由推进
    fresh = DoomState()
    turns = 0
    while not fresh.at_max and turns < 100:
        fresh.advance(1, f"选择 {turns}")
        turns += 1
    check("阶段8 满级所需的铺垫回合数", turns == DOOM_MAX, f"用了 {turns} 回合")

    # 序列化
    restored = DoomState.from_dict(doom.to_dict())
    check(
        "阶段8 进度往返",
        restored.level == doom.level and restored.evidence == doom.evidence,
    )
    check("阶段8 残缺数据兜底", DoomState.from_dict({}).level == 0)
    check("阶段8 越界数据被夹紧", DoomState.from_dict({"level": 999}).level == DOOM_MAX)

    # 提示词里必须写清上限和履历
    text = doom.describe()
    check("阶段8 描述含进度", f"{DOOM_MAX}/{DOOM_MAX}" in text)
    check("阶段8 描述含描写上限", "描写上限" in text)
    check("阶段8 描述含履历", "重大选择" in text)


def test_events() -> None:
    from core.doom import DOOM_MAX, DoomState
    from core.events import (
        StateChanges,
        apply_state_changes,
        doom_validation_rule,
        parse_event,
    )
    from core.models import WorldState
    from core.savegame import PlayerState

    good = (
        '{"narrative":"巷道尽头的铁门半开着。",'
        '"npc":"阿蘅","npc_dialog":"别往里走。",'
        '"options":["进去看看","退回去"],'
        '"doom_delta":0,"doom_reason":"",'
        '"state_changes":{"location":"仓库夹层","add_flags":["见过阿蘅"]}}'
    )
    event, error = parse_event(good)
    check(
        "阶段8 解析事件",
        error == "" and event is not None and event.narrative == "巷道尽头的铁门半开着。"
        and event.npc == "阿蘅" and len(event.options) == 2,
    )
    check("阶段8 解析状态变更", event.changes.location == "仓库夹层")
    check("阶段8 解析印记", event.changes.add_flags == ["见过阿蘅"])

    for label, raw in [
        ("缺 narrative", '{"options":["a","b"]}'),
        ("选项不足", '{"narrative":"x","options":["只有一个"]}'),
        ("非 json", "这个世界完了"),
        ("空", ""),
    ]:
        parsed, err = parse_event(raw)
        check(f"阶段8 拒绝 {label}", parsed is None and err != "")

    # 关系值不在白名单里要过滤掉，否则界面拿到无法着色的值
    bad_relation = (
        '{"narrative":"x","options":["a","b"],"state_changes":'
        '{"faction_changes":[{"name":"码头帮","relation":"很生气"},'
        '{"name":"王庭","relation":"敌对"}]}}'
    )
    parsed, _ = parse_event(bad_relation)
    check(
        "阶段8 非法势力关系被过滤",
        len(parsed.changes.faction_changes) == 1
        and parsed.changes.faction_changes[0]["name"] == "王庭",
    )

    # 正文保留段落，选项压缩空白
    multiline = (
        '{"narrative":"第一段。\\n\\n第二段。","options":["选项\\n  带换行  ","b"]}'
    )
    parsed, _ = parse_event(multiline)
    check("阶段8 正文保留换行", "\n" in parsed.narrative)
    check("阶段8 选项压缩空白", parsed.options[0] == "选项 带换行")

    # ---------- 状态应用 ----------
    state = WorldState(location="灰烬港", factions=[{"name": "码头帮", "relation": "中立"}])
    player = PlayerState(name="凌昭", status=["轻度灰化"])

    changes = StateChanges(
        location="仓库夹层",
        time="灰烬三十七年·冬",
        faction_changes=[
            {"name": "码头帮", "relation": "敌对"},
            {"name": "拾灰人", "relation": "友好"},
        ],
        add_flags=["见过阿蘅"],
        player_status_add=["左臂灼伤"],
        player_status_remove=["轻度灰化"],
        player_notes="被码头帮盯上了",
    )
    notes = apply_state_changes(changes, state, player)

    check("阶段8 位置已更新", state.location == "仓库夹层")
    check("阶段8 时间已更新", state.time == "灰烬三十七年·冬")
    check(
        "阶段8 势力关系更新而非重复追加",
        len(state.factions) == 2
        and next(f for f in state.factions if f["name"] == "码头帮")["relation"] == "敌对",
    )
    check("阶段8 新势力被追加", any(f["name"] == "拾灰人" for f in state.factions))
    check("阶段8 印记已加", "见过阿蘅" in state.flags)
    check("阶段8 状态增删", "左臂灼伤" in player.status and "轻度灰化" not in player.status)
    check("阶段8 处境已更新", player.notes == "被码头帮盯上了")
    check("阶段8 返回变更说明", len(notes) >= 5)

    # 重复印记不该重复追加
    apply_state_changes(StateChanges(add_flags=["见过阿蘅"]), state)
    check("阶段8 印记去重", state.flags.count("见过阿蘅") == 1)

    # 印记与状态数量要有上限，否则长局会无限膨胀进 prompt
    for i in range(40):
        apply_state_changes(StateChanges(add_flags=[f"印记{i}"]), state)
    check("阶段8 印记数量有上限", len(state.flags) <= 20, str(len(state.flags)))

    for i in range(30):
        apply_state_changes(StateChanges(player_status_add=[f"状态{i}"]), state, player)
    check("阶段8 玩家状态有上限", len(player.status) <= 10, str(len(player.status)))

    # ---------- 毁灭校验规则 ----------
    for level in (0, 2, DOOM_MAX):
        rule = doom_validation_rule(level)
        check(f"阶段8 毁灭规则含进度 {level}", f"{level}/{DOOM_MAX}" in rule)
    check("阶段8 0 级规则最严", "任何世界级威胁" in doom_validation_rule(0))
    check("阶段8 满级规则放开", "允许" in doom_validation_rule(DOOM_MAX))

    # ---------- 生成循环中的毁灭裁定 ----------
    class _FakeEventClient:
        def __init__(self, generation, verdicts):
            self._generation = list(generation)
            self._verdicts = list(verdicts)
            self.validate_prompts = []

        def stream_chat(self, messages, **kwargs):
            from core.api_client import ChatResult

            if messages[0]["content"].startswith("你是一个文字游戏的实时事件生成器"):
                return ChatResult(content=self._generation.pop(0), model="fake", usage={})
            self.validate_prompts.append(messages[-1]["content"])
            return ChatResult(content=self._verdicts.pop(0), model="fake", usage={})

    from core.events import generate_event
    from core.world import WorldDocument

    world = WorldDocument(name="测试", text="初火山在冰墙之外。")
    ok_verdict = '{"conflict": false}'

    # AI 提议推进但没给理由 → 框架驳回
    no_reason = (
        '{"narrative":"天地开始崩塌。","options":["逃","留"],'
        '"doom_delta":1,"doom_reason":""}'
    )
    client = _FakeEventClient([no_reason], [ok_verdict])
    state = WorldState(doom=DoomState())
    result = generate_event(client, world, "我走向初火山", state=state, max_retries=3)
    check(
        "阶段8 无理由的毁灭推进被框架驳回",
        result.applied_doom_delta == 0 and state.doom.level == 0 and result.doom_rejected,
    )

    # AI 提议且给了理由 → 接受
    with_reason = (
        '{"narrative":"封印松动了。","options":["继续","停手"],'
        '"doom_delta":1,"doom_reason":"玩家执意取走了初火之源"}'
    )
    client = _FakeEventClient([with_reason], [ok_verdict])
    state = WorldState(doom=DoomState())
    result = generate_event(client, world, "我取走初火之源", state=state, max_retries=3)
    check(
        "阶段8 有理由的推进被接受",
        result.applied_doom_delta == 1 and state.doom.level == 1
        and len(state.doom.evidence) == 1,
    )

    # 即使 AI 一次推满，也会被压到单回合上限
    huge = (
        '{"narrative":"世界毁灭了。","options":["a","b"],'
        '"doom_delta":5,"doom_reason":"一口气推满"}'
    )
    client = _FakeEventClient([huge], [ok_verdict])
    state = WorldState(doom=DoomState())
    result = generate_event(client, world, "x", state=state, max_retries=3)
    check(
        "阶段8 AI 一次推满会被压到单回合上限",
        state.doom.level == 1,
    )

    # 校验请求里必须带上毁灭规则
    check(
        "阶段8 校验请求含毁灭约束",
        "毁灭约束" in client.validate_prompts[0]
        and "描写上限" in client.validate_prompts[0],
    )


# ----------------------------------------------------------------------
# 阶段 10：调试日志
# ----------------------------------------------------------------------


def test_debuglog(tmp: pathlib.Path) -> None:
    from core.debuglog import (
        LOG,
        MAX_FIELD_CHARS,
        MAX_FILE_BYTES,
        MAX_LLM_CHARS,
        DebugLog,
    )

    log = DebugLog(path=tmp / "debug.log")

    # ---------- 默认关闭时不落盘，但内存仍记录 ----------
    check("阶段10 默认不落盘", not log.enabled)
    log.info("测试", "这条只应该在内存里")
    check("阶段10 关闭时仍入内存", len(log.entries()) == 1)
    check("阶段10 关闭时不写文件", not (tmp / "debug.log").exists())

    # ---------- 开启后落盘 ----------
    log.set_enabled(True)
    log.info("测试", "开启后的记录", "附加内容")
    text = (tmp / "debug.log").read_text(encoding="utf-8")
    check("阶段10 开启后写文件", "开启后的记录" in text)
    check("阶段10 附加内容一并写入", "附加内容" in text)
    check("阶段10 文件含级别标记", "[INFO ]" in text)

    # ---------- AI 收发记录 ----------
    before = len(log.entries())
    log.llm_request(
        "https://api.deepseek.com/chat/completions",
        "deepseek-flash",
        [{"role": "system", "content": "你是助手"}, {"role": "user", "content": "你好"}],
        stream=True,
        json_mode=True,
        max_tokens=100,
    )
    log.llm_response(
        "deepseek-flash", "回复内容", {"prompt_tokens": 10, "completion_tokens": 5},
        latency_ms=123, finish_reason="stop", streamed=True,
    )
    check("阶段10 请求已记录", len(log.entries()) == before + 2)

    request_entry = log.entries()[-2]
    check("阶段10 请求含完整消息", "你是助手" in request_entry.detail and "你好" in request_entry.detail)
    check("阶段10 请求含调用参数", "流式=True" in request_entry.message and "max_tokens=100" in request_entry.message)

    response_entry = log.entries()[-1]
    check("阶段10 返回含内容", "回复内容" in response_entry.detail)
    check("阶段10 返回含用量", "10+5" in response_entry.message and "123ms" in response_entry.message)

    # 关掉后不该再构造大字符串
    log.set_enabled(False)
    check(
        "阶段10 关闭时不记录 AI 收发",
        log.llm_request("u", "m", [{"role": "user", "content": "x"}]) is None
        and log.llm_response("m", "x") is None,
    )

    # ---------- 截断 ----------
    huge = DebugLog(path=tmp / "huge.log")
    huge.set_enabled(True)
    huge.info("测试", "短", "x" * 50000)
    check(
        "阶段10 普通字段按 MAX_FIELD_CHARS 截断",
        len(huge.entries()[-1].detail) < MAX_FIELD_CHARS + 40,
    )

    huge.llm_response("m", "y" * 60000)
    check(
        "阶段10 prompt 用更宽的 MAX_LLM_CHARS",
        len(huge.entries()[-1].detail) < MAX_LLM_CHARS + 40,
    )

    # ---------- 文件轮转 ----------
    rotate = DebugLog(path=tmp / "rotate.log")
    rotate.set_enabled(True)
    rotate.info("测试", "轮转前")
    # 直接把文件撑到上限之上
    (tmp / "rotate.log").write_text("x" * (MAX_FILE_BYTES + 1), encoding="utf-8")
    rotate.info("测试", "轮转后")
    check("阶段10 超限后轮转出备份", (tmp / "rotate.log.1").exists())
    check(
        "阶段10 轮转后新文件只含新记录",
        "轮转后" in (tmp / "rotate.log").read_text(encoding="utf-8"),
    )

    # ---------- 异常记录 ----------
    try:
        raise ValueError("模拟异常")
    except ValueError as exc:
        entry = rotate.exception("测试", "出错了", exc)
    check("阶段10 异常带完整堆栈", "Traceback" in entry.detail and "模拟异常" in entry.detail)

    # ---------- 清理 ----------
    rotate.clear()
    check("阶段10 清空内存", len(rotate.entries()) == 0)
    check("阶段10 删除文件", rotate.clear_file() and not rotate.path.exists())

    # 内存上限
    cap = DebugLog(path=tmp / "cap.log")
    for i in range(700):
        cap.info("测试", f"第 {i} 条")
    check("阶段10 内存有上限", len(cap.entries()) <= 500, str(len(cap.entries())))
    check("阶段10 保留的是最新的", "第 699 条" in cap.entries()[-1].message)

    # 全局日志器存在且默认关闭（不能悄悄记录玩家的游玩内容）
    check("阶段10 全局日志器默认关闭", not LOG.enabled)


def test_api_error_logging() -> None:
    """API 异常必须落进调试日志。

    「网络异常捕获」有两半：把异常翻译成可读文案，
    以及把异常记录下来。只翻译不记录的话，
    用户遇到间歇性网络问题时无从查起。
    """
    from core.api_client import (
        ApiError,
        _classify_http_error,
        _classify_network_error,
        _log_api_error,
    )
    from core.debuglog import LOG

    before = len(LOG.entries())

    network = _classify_network_error(OSError("boom"), "https://example.test/x")
    _log_api_error("https://example.test/x", network)
    network_entry = LOG.entries()[-1]
    check("阶段10 网络异常记为警告", network_entry.level == "warn")
    check("阶段10 网络异常带目标地址", "example.test" in network_entry.message)
    check("阶段10 网络异常带错误类型", "类型=network" in network_entry.message)

    auth = _classify_http_error(401, '{"error":{"message":"Authentication Fails"}}')
    _log_api_error("https://example.test/x", auth)
    auth_entry = LOG.entries()[-1]
    check("阶段10 鉴权失败记为错误", auth_entry.level == "error")
    check("阶段10 鉴权失败带状态码", "状态码=401" in auth_entry.message)
    check("阶段10 保留服务端原文", "Authentication Fails" in auth_entry.detail)

    check("阶段10 两次异常都记下了", len(LOG.entries()) == before + 2)

    # 各错误类型都要能翻译出可读文案，不能漏出原始异常
    cases = {
        402: "quota",
        429: "rate_limit",
        503: "server",
    }
    for status, kind in cases.items():
        error = _classify_http_error(status, "")
        check(
            f"阶段10 状态码 {status} 分类为 {kind}",
            error.kind == kind and len(error.message) > 10,
        )

    # 未分类的状态码也要给出可读文案，而不是空字符串
    unknown = _classify_http_error(418, "")
    check("阶段10 未知状态码有兜底文案", len(unknown.message) > 5)


def test_excepthook_installed() -> None:
    """入口模块必须安装全局兜底。

    实测：不装的话，PyQt6 会在槽函数抛未捕获异常时直接 abort 进程
    （退出码 127、无堆栈、无输出），用户看到的是窗口凭空消失。
    """
    import main as app_main
    from core.debuglog import LOG

    check("阶段10 入口暴露 install_excepthook", hasattr(app_main, "install_excepthook"))

    original = sys.excepthook
    original_thread = threading.excepthook
    original_box = app_main.QMessageBox

    # 必须把弹窗打桩掉：本测试进程里没有 QApplication，
    # 直接构造 QMessageBox 会让 Qt abort 掉整个测试进程。
    # 这也说明这个兜底的弹窗只能在真实应用里验证 —— 见下面的说明。
    class _StubBox:
        shown: list[str] = []

        @staticmethod
        def critical(*args, **kwargs):
            _StubBox.shown.append(str(args[2]) if len(args) > 2 else "")
            return None

    try:
        app_main.QMessageBox = _StubBox
        app_main.install_excepthook()

        check("阶段10 sys.excepthook 已被替换", sys.excepthook is not original)
        check("阶段10 线程兜底已安装", threading.excepthook is not original_thread)

        # 兜底本身不能抛异常，且要触发提示
        try:
            sys.excepthook(ValueError, ValueError("模拟崩溃"), None)
            check("阶段10 兜底处理异常不抛出", True)
        except BaseException:  # noqa: BLE001
            check("阶段10 兜底处理异常不抛出", False)

        check("阶段10 兜底弹窗带指引", bool(_StubBox.shown) and "调试日志" in _StubBox.shown[0])
        check("阶段10 崩溃写进了日志", any("模拟崩溃" in e.message for e in LOG.entries()))
    finally:
        sys.excepthook = original
        threading.excepthook = original_thread
        app_main.QMessageBox = original_box


# ----------------------------------------------------------------------
# 主题与配色
# ----------------------------------------------------------------------


def _relative_luminance(hex_color: str) -> float:
    """WCAG 相对亮度。"""
    value = hex_color.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]

    def linearize(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linearize(c) for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(fg: str, bg: str) -> float:
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def test_themes() -> None:
    """配色方案必须完整且可读。

    深色界面里颜色差一点点就会变成「看不清」，
    所以每套主题都要逐项算对比度，不能靠肉眼验收。
    """
    from ui import styles, themes

    check("主题数量", len(themes.THEMES) >= 4)

    for key, data in themes.THEMES.items():
        name = data["name"]
        colors = data["colors"]

        missing = [k for k in themes.REQUIRED_KEYS if k not in colors]
        check(f"主题 {name} 调色板完整", not missing, str(missing))

        weird = [
            k for k, v in colors.items()
            if not (isinstance(v, str) and v.startswith("#") and len(v) == 7)
        ]
        check(f"主题 {name} 颜色格式合法", not weird, str(weird))

        # 正文与阅读区是长时间盯着看的地方，要求最高
        checks = [
            ("正文/面板", colors["text"], colors["bg_panel"], 4.5),
            ("正文/输入区", colors["text"], colors["bg_input"], 4.5),
            ("正文/浮起层", colors["text"], colors["bg_elev"], 4.5),
            ("次要文字/面板", colors["text_dim"], colors["bg_panel"], 3.0),
            ("弱化文字/面板", colors["text_faint"], colors["bg_panel"], 2.2),
            ("主色/面板", colors["accent"], colors["bg_panel"], 4.0),
            ("警示/面板", colors["danger"], colors["bg_panel"], 3.5),
        ]
        # 稀有度标签画在浮起层上
        for rarity, color_key in themes.RARITY_KEYS.items():
            checks.append(
                (f"稀有度{rarity}", colors[color_key], colors["bg_elev"], 4.0)
            )

        for label, fg, bg, need in checks:
            got = _contrast(fg, bg)
            check(
                f"主题 {name} 对比度 {label}",
                got >= need,
                f"{got:.2f} < {need}",
            )


def test_theme_switching() -> None:
    from ui import styles, themes

    original = styles.current_theme()

    # 关键：切换主题必须**原地修改** COLORS。
    # 全项目有 80 多处直接读 COLORS[...]，如果重新赋值，
    # 那些已经持有引用的地方会一直用旧颜色。
    reference = styles.COLORS
    styles.set_theme("amber")
    check("切换主题原地更新 COLORS", styles.COLORS is reference)
    check("切换后颜色已变", styles.COLORS["accent"] == themes.AMBER["colors"]["accent"])
    check("切换后主题名正确", styles.current_theme() == "amber")

    # 未知主题名要回退，不能崩
    styles.set_theme("不存在的主题")
    check("未知主题回退到默认", styles.current_theme() == themes.DEFAULT_THEME)

    # 稀有度颜色跟着主题走
    styles.set_theme("jade")
    check(
        "稀有度颜色跟随主题",
        styles.rarity_color("传说") == themes.JADE["colors"]["r_legend"],
    )
    check("未知稀有度有兜底", styles.rarity_color("不存在的档位") == styles.COLORS["r_common"])

    styles.set_theme(original)


def test_background() -> None:
    from ui import styles

    original_bg = styles.background_image()

    # 不存在的图要当成没设，不能记下一个坏路径反复报错
    styles.set_background("C:/根本不存在的图片.png")
    check("不存在的背景图被忽略", not styles.has_background())
    check("背景路径被清空", styles.background_image() == "")

    styles.set_background("")
    check("传空可以清除背景", not styles.has_background())

    # 有背景图时必须输出 rgba，否则面板会把图盖死
    import tempfile

    styles.set_background(None)
    plain = styles.stylesheet()
    check("无背景图时窗口底色不透明", "QWidget#Root { background: #" in plain)
    check("无背景图时不出现 rgba", "rgba(" not in plain)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        # 写一个最小的合法 PNG 头就够了 —— styles 只检查文件是否存在
        handle.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        temp_path = handle.name

    try:
        styles.set_background(temp_path)
        check("背景图路径已记录", styles.has_background())

        with_bg = styles.stylesheet()
        check("有背景图时根容器透空", "QWidget#Root { background: transparent; }" in with_bg)
        check("有背景图时面板转为 rgba", with_bg.count("rgba(") >= 4)
    finally:
        import os

        styles.set_background(None)
        os.unlink(temp_path)

    # 有背景图时，正文在「最亮背景」下仍要可读。
    # 这是最坏情况推算：背景图先被压暗，面板再半透明叠上去。
    from ui import backdrop

    worst_backdrop = 255 * (1 - backdrop.DIM_ALPHA / 255)  # 纯白图片压暗后
    worst_lum = _relative_luminance(
        f"#{int(worst_backdrop):02x}{int(worst_backdrop):02x}{int(worst_backdrop):02x}"
    )
    for key, alpha in styles._OVERLAY_ALPHA.items():
        if alpha == 0:
            continue
        base = _relative_luminance(styles.COLORS[key])
        composite = (alpha / 255) * base + (1 - alpha / 255) * worst_lum
        text_lum = _relative_luminance(styles.COLORS["text"])
        hi, lo = max(text_lum, composite), min(text_lum, composite)
        ratio = (hi + 0.05) / (lo + 0.05)
        check(
            f"背景图下 {key} 的最坏对比度",
            ratio >= 4.5,
            f"{ratio:.2f} < 4.5",
        )

    styles.set_background(original_bg)


# ----------------------------------------------------------------------
# 世界观生成 agent
# ----------------------------------------------------------------------


def _fake_world_doc(sections: int = 5, body_repeat: int = 25, title: str = "# 测试世界") -> str:
    """造一份结构合法的文档，供检查逻辑使用。"""
    parts = [title]
    for i in range(sections):
        parts.append(f"## 第{i + 1}节 设定")
        parts.append("这是一段用于测试的世界观设定内容。" * body_repeat)
    return "\n\n".join(parts)


def test_worldgen() -> None:
    from core.worldgen import (
        MAX_CHARS,
        MIN_CHARS,
        MIN_SECTIONS,
        WorldGenError,
        build_generate_messages,
        check_structure,
        generate_world,
        revise_world,
    )

    # ---------- 结构检查 ----------
    ok, problem = check_structure(_fake_world_doc())
    check("生成 合格文档通过检查", ok, problem)

    cases = [
        ("空文档", "", "空"),
        ("太短", "# 世界\n\n## 甲\n\n短", "太短"),
        ("超长", _fake_world_doc(9, 260), "太长"),
        ("缺一级标题", _fake_world_doc().replace("# 测试世界", "测试世界", 1), "一级标题"),
        ("标题太少", _fake_world_doc(sections=2, body_repeat=120), "分节标题"),
        ("代码块包裹", "```\n" + _fake_world_doc() + "\n```", "一级标题"),
    ]
    for label, text, expect_in in cases:
        passed, why = check_structure(text)
        check(
            f"生成 拒绝 {label}",
            not passed and expect_in in why,
            f"passed={passed} why={why[:40]}",
        )

    # 边界：刚好达标要放过，差一点要拦下
    just_enough = _fake_world_doc(4, 22)
    passed, why = check_structure(just_enough)
    check(
        "生成 长度边界处理正确",
        passed == (len(just_enough.strip()) >= MIN_CHARS),
        f"{len(just_enough.strip())} 字 vs 下限 {MIN_CHARS}",
    )
    check("生成 分节下限为 4", MIN_SECTIONS == 4)

    # ---------- 提示词 ----------
    messages = build_generate_messages("一个全是沙漠的世界")
    check("生成 提示词含用户描述", "一个全是沙漠的世界" in messages[-1]["content"])
    check("生成 提示词声明是文件规范", "Markdown" in messages[0]["content"])
    # 这条最关键：不能把剧情当设定写，否则框架动态生成会与之冲突
    check(
        "生成 提示词要求写世界而非故事",
        "不是**故事**" in messages[0]["content"] and "不要写：主角" in messages[0]["content"],
    )
    check(
        "生成 提示词声明字数区间",
        str(MIN_CHARS) in messages[0]["content"] and str(MAX_CHARS) in messages[0]["content"],
    )

    with_feedback = build_generate_messages("x", ["文档太短了"])
    check("生成 反馈会写进提示词", "文档太短了" in with_feedback[-1]["content"])

    from core.worldgen import build_revise_messages

    revise_messages = build_revise_messages("旧文档内容", "多加点势力")
    check("生成 修改提示词含原文档", "旧文档内容" in revise_messages[-1]["content"])
    check("生成 修改提示词含要求", "多加点势力" in revise_messages[-1]["content"])

    # ---------- 生成循环 ----------
    class _FakeGenClient:
        """按脚本回应世界观生成请求。"""

        def __init__(self, responses: list):
            self._responses = list(responses)
            self.calls = 0
            #: 每次调用发出的消息，用来验证「重试时把问题回喂了」
            self.sent: list[str] = []

        def stream_chat(self, messages, **kwargs):
            from core.api_client import ChatResult

            self.calls += 1
            self.sent.append(messages[-1]["content"])
            payload = self._responses.pop(0)
            if isinstance(payload, BaseException):
                raise payload
            text, truncated = payload
            return ChatResult(
                content=text, model="fake", usage={"prompt_tokens": 10},
                finish_reason="length" if truncated else "stop",
                truncated=truncated,
            )

    good = _fake_world_doc()

    # 一次成功
    client = _FakeGenClient([(good, False)])
    result = generate_world(client, "一个世界")
    check("生成 一次成功", result.document.char_count == len(good.strip()))
    check("生成 文档名取自一级标题", result.document.name == "测试世界")
    check("生成 无重试", result.retries_used == 0)

    # 结构不合格会带原因重试
    client = _FakeGenClient([("太短了", False), (good, False)])
    result = generate_world(client, "一个世界")
    check("生成 结构不合格会重试", result.retries_used == 1 and client.calls == 2)
    # 不带原因的重试等于重新抽卡，很可能又踩同一个坑
    check(
        "生成 重试时把具体问题回喂",
        "太短" in client.sent[1],
        client.sent[1][-80:],
    )

    # 被 max_tokens 截断的必须重来 —— 半截文档比没有更糟
    client = _FakeGenClient([(good[:600], True), (good, False)])
    result = generate_world(client, "一个世界")
    check("生成 截断会触发重试", result.retries_used == 1)
    check(
        "生成 截断记录被标记",
        result.attempts[0].truncated and result.attempts[0].problem != "",
    )

    # 连续失败抛 WorldGenError，且提示要可操作
    client = _FakeGenClient([("坏", False)] * 3)
    try:
        generate_world(client, "一个世界", attempts=3)
        check("生成 连续失败抛异常", False)
    except WorldGenError as exc:
        check("生成 连续失败抛异常", len(exc.problems) == 3)
        check("生成 失败提示给建议", "建议" in str(exc) and "描述" in str(exc))

    # 空描述直接拦下，不浪费一次 API 调用
    client = _FakeGenClient([])
    try:
        generate_world(client, "   ")
        check("生成 空描述被拒", False)
    except Exception:
        check("生成 空描述被拒", client.calls == 0)

    # 修改模式
    client = _FakeGenClient([(good, False)])
    result = revise_world(client, "旧文档", "多点势力")
    check("生成 修改模式标记正确", result.revised)


# ----------------------------------------------------------------------
# 随包资源
# ----------------------------------------------------------------------


def test_assets() -> None:
    """随包发布的资源必须存在、可解析。

    打包脚本如果把 assets 漏了，用户点「导入示例」会报错 ——
    这条测试就是防这个的。
    """
    from core.paths import ASSETS_DIR
    from core.worldgen import check_structure

    check("资源目录存在", ASSETS_DIR.is_dir(), str(ASSETS_DIR))

    sample = ASSETS_DIR / "示例世界观-眠神纪.md"
    check("示例世界观存在", sample.is_file(), str(sample))

    if sample.is_file():
        document = import_world_file(sample)
        # 示例必须能被解析器接受，否则用户点了导入反而报错
        passed, problem = check_structure(document.text)
        check("示例世界观结构合规", passed, problem)
        check("示例世界观不需要压缩", not document.needs_compression())
        check("示例世界观有名字", bool(document.name))

        # 走一遍真实导入链路
        import tempfile as _tmp

        from core.world import WorldStore as _WS

        with _tmp.TemporaryDirectory() as folder:
            repo = _WS(directory=pathlib.Path(folder))
            saved = repo.save(document)
            restored = repo._load_path(saved)
            check(
                "示例世界观能存进仓库并读回",
                restored is not None and restored.text == document.text,
            )

    prompt_doc = ASSETS_DIR / "世界观生成提示词.md"
    check("提示词文档存在", prompt_doc.is_file(), str(prompt_doc))

    if prompt_doc.is_file():
        # 文档里的格式要求必须和程序实际用的约束一致，
        # 否则用户照着文档生成的文档会被程序拒收
        from core.worldgen import MAX_CHARS, MIN_CHARS, MIN_SECTIONS

        text = prompt_doc.read_text(encoding="utf-8-sig")
        check("提示词文档含字数区间", str(MIN_CHARS) in text and str(MAX_CHARS) in text)
        check("提示词文档含分节要求", f"至少 {MIN_SECTIONS} 个" in text)
        check("提示词文档说明必须 UTF-8", "UTF-8" in text)
        check(
            "提示词文档写明不写剧情",
            "不要写：主角" in text or "不要把剧情写进设定" in text,
        )


# ----------------------------------------------------------------------
# 模型
# ----------------------------------------------------------------------


def test_models() -> None:
    payload = Item(name="测试", rarity="史诗", effect="无").to_dict()
    check("模型 忽略未知字段", Item.from_dict({**payload, "未来字段": 1}).name == "测试")
    check("模型 缺失字段走默认", Item.from_dict({"name": "只有名字"}).rarity == "普通")

    state = WorldState(location="雾原", factions=[{"name": "拾灰人", "relation": "中立"}])
    check("模型 WorldState 往返", WorldState.from_dict(state.to_dict()).factions == state.factions)

    player = PlayerState.from_dict({"name": "凌昭", "attributes": "不是字典", "status": None})
    check("模型 玩家容器类型兜底", player.attributes == {} and player.status == [])


# ----------------------------------------------------------------------


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="aiworld-test-"))
    try:
        test_config(tmp)
        test_world(tmp)
        test_savegame(tmp)
        test_pricing()
        test_usage(tmp)
        test_validator()
        test_items()
        test_doom()
        test_events()
        test_debuglog(tmp)
        test_api_error_logging()
        test_excepthook_installed()
        test_themes()
        test_theme_switching()
        test_background()
        test_worldgen()
        test_assets()
        test_models()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [(name, detail) for name, ok, detail in _results if not ok]
    for name, ok, detail in _results:
        mark = "OK  " if ok else "FAIL"
        suffix = f"   <- {detail}" if detail and not ok else ""
        print(f"  [{mark}] {name}{suffix}")

    print(f"\n共 {len(_results)} 项，失败 {len(failed)} 项")
    if failed:
        print("失败项：" + "、".join(name for name, _ in failed))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
