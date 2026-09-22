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

    check("阶段2 缺失文件回退默认", store.load().model == "deepseek-chat")

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
