"""配色方案。

每套主题是一份完整的调色板（22 个键），不是把默认主题转个色相 ——
深色界面里颜色差一点点就会显得脏或者看不清，逐套手调过。

所有主题都是深色系：游戏是长时间阅读的场景，浅色界面在暗环境下刺眼。
每套都保证正文与面板的对比度足够高。
"""

from __future__ import annotations

#: 调色板需要的键，缺一个渲染就会出问题
REQUIRED_KEYS = (
    "bg_window", "bg_panel", "bg_elev", "bg_input", "bg_hover",
    "border", "border_soft",
    "text", "text_dim", "text_faint",
    "accent", "accent_hover", "accent_dim",
    "danger", "danger_hover", "success", "warning",
    "r_common", "r_good", "r_rare", "r_epic", "r_legend",
)

#: 稀有度 → 调色板键
RARITY_KEYS = {
    "普通": "r_common",
    "精良": "r_good",
    "稀有": "r_rare",
    "史诗": "r_epic",
    "传说": "r_legend",
}

DEFAULT_THEME = "deep_blue"


# ----------------------------------------------------------------------
# 内置主题
# ----------------------------------------------------------------------

DEEP_BLUE = {
    "name": "深蓝",
    "description": "默认配色。冷调深蓝，对比清晰",
    "colors": {
        "bg_window": "#0f1117",
        "bg_panel": "#171a23",
        "bg_elev": "#1e2230",
        "bg_input": "#12141c",
        "bg_hover": "#262b3b",
        "border": "#2a3044",
        "border_soft": "#212636",
        "text": "#d8dce6",
        "text_dim": "#8b94a8",
        "text_faint": "#5d6579",
        "accent": "#6c8cff",
        "accent_hover": "#839eff",
        "accent_dim": "#3d4d8f",
        "danger": "#e05c6e",
        "danger_hover": "#f0707f",
        "success": "#4fbf8b",
        "warning": "#d9a441",
        "r_common": "#9aa3b5",
        "r_good": "#4fbf8b",
        "r_rare": "#5aa9f0",
        "r_epic": "#a97bf0",
        "r_legend": "#e8a13c",
    },
}

INK = {
    "name": "墨夜",
    "description": "中性灰黑配暗金，像旧书房的灯下",
    "colors": {
        "bg_window": "#0c0c0e",
        "bg_panel": "#151517",
        "bg_elev": "#1e1e21",
        "bg_input": "#101012",
        "bg_hover": "#28282c",
        "border": "#2e2e33",
        "border_soft": "#222226",
        "text": "#dcd9d3",
        "text_dim": "#918d86",
        "text_faint": "#63605a",
        "accent": "#c9a227",
        "accent_hover": "#ddb63c",
        "accent_dim": "#5c4c1c",
        "danger": "#d9564f",
        "danger_hover": "#ec6a63",
        "success": "#5aa87a",
        "warning": "#d99a3c",
        "r_common": "#9d9992",
        "r_good": "#5aa87a",
        "r_rare": "#5b93c4",
        "r_epic": "#9c7ac4",
        "r_legend": "#d9a441",
    },
}

AMBER = {
    "name": "暖褐",
    "description": "棕褐与琥珀，炉火旁的暖意",
    "colors": {
        "bg_window": "#14100c",
        "bg_panel": "#1e1813",
        "bg_elev": "#282019",
        "bg_input": "#171310",
        "bg_hover": "#332a21",
        "border": "#3d3226",
        "border_soft": "#2b241c",
        "text": "#e6dccd",
        "text_dim": "#a3957f",
        "text_faint": "#6f6455",
        "accent": "#d9a441",
        "accent_hover": "#e8b857",
        "accent_dim": "#6b4f1e",
        "danger": "#d96a4f",
        "danger_hover": "#ec7d63",
        "success": "#7aa85a",
        "warning": "#e0a83c",
        "r_common": "#a89b86",
        "r_good": "#7aa85a",
        "r_rare": "#5f9dc4",
        "r_epic": "#b086c4",
        "r_legend": "#e8b34c",
    },
}

JADE = {
    "name": "青碧",
    "description": "青绿冷调，像山间的水汽",
    "colors": {
        "bg_window": "#0a1214",
        "bg_panel": "#121e21",
        "bg_elev": "#1a282c",
        "bg_input": "#0e1719",
        "bg_hover": "#22343a",
        "border": "#274046",
        "border_soft": "#1c2f34",
        "text": "#d2e2e0",
        "text_dim": "#84a09e",
        "text_faint": "#566e6d",
        "accent": "#4fc4b0",
        "accent_hover": "#66d6c2",
        "accent_dim": "#276259",
        "danger": "#e0685c",
        "danger_hover": "#f07c70",
        "success": "#5fc48d",
        "warning": "#d9b04c",
        "r_common": "#93a8a6",
        "r_good": "#5fc48d",
        "r_rare": "#55a8d9",
        "r_epic": "#a48ad9",
        "r_legend": "#e0b04c",
    },
}

VIOLET = {
    "name": "夜紫",
    "description": "深紫配青莲，偏梦幻的夜色",
    "colors": {
        "bg_window": "#100e18",
        "bg_panel": "#191624",
        "bg_elev": "#221e30",
        "bg_input": "#13111c",
        "bg_hover": "#2c273c",
        "border": "#332c47",
        "border_soft": "#262135",
        "text": "#ddd8e8",
        "text_dim": "#948cab",
        "text_faint": "#625b78",
        "accent": "#9b7bf0",
        "accent_hover": "#ad91f5",
        "accent_dim": "#4c3b85",
        "danger": "#e0627f",
        "danger_hover": "#f07692",
        "success": "#54bf95",
        "warning": "#d9a04c",
        "r_common": "#9d96b0",
        "r_good": "#54bf95",
        "r_rare": "#5aa9f0",
        "r_epic": "#c07bf0",
        "r_legend": "#e8a13c",
    },
}

THEMES: dict[str, dict] = {
    "deep_blue": DEEP_BLUE,
    "ink": INK,
    "amber": AMBER,
    "jade": JADE,
    "violet": VIOLET,
}


def theme_names() -> list[tuple[str, str, str]]:
    """返回 [(key, 显示名, 说明), ...]，供设置界面使用。"""
    return [
        (key, data["name"], data["description"])
        for key, data in THEMES.items()
    ]


def get_colors(key: str) -> dict[str, str]:
    """取某套主题的颜色表。未知 key 回退到默认主题。"""
    theme = THEMES.get(key) or THEMES[DEFAULT_THEME]
    return dict(theme["colors"])
