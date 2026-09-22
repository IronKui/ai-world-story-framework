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
