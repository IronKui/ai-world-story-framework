"""版本与产品信息。

打包脚本、安装包脚本、关于窗口、日志抬头都从这里取，
不要在各处写死字符串，改版本号时容易漏。
"""

from __future__ import annotations

#: 语义化版本号。
#: 0.x 表示「能用了，但还在快速迭代」—— 长局稳定性尚未验证，
#: 后续还会有破坏性改动（例如角色槽位系统）。等这些稳定下来再进 1.0。
APP_VERSION = "0.1.0"

APP_NAME = "动态世界观文字游戏框架"

#: 打包时用的英文名，避免中文路径在某些环境下出问题
APP_SLUG = "AIWorldStoryFramework"

APP_DESCRIPTION = "导入自定义世界观文档，由 AI 实时生成剧情、道具与事件的桌面文字游戏框架"

#: 用户需要自备 DeepSeek API Key
API_KEY_HELP_URL = "https://platform.deepseek.com/api_keys"
API_KEY_SIGNUP_URL = "https://platform.deepseek.com"


def version_string() -> str:
    return f"v{APP_VERSION}"
