"""本地 JSON 配置：API Key、接口地址、模型、超时。

配置只存在本地 data/config.json，不上传任何服务器。
注意：DeepSeek API Key 按需求以明文存于本地 JSON，
      文件权限在 POSIX 下收紧到 0600；Windows 下依赖用户目录 ACL。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.paths import CONFIG_FILE, ensure_dirs
from core.storage import read_json, write_json_atomic

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

#: 界面上可选的模型（允许用户手填其它模型名）
KNOWN_MODELS = ["deepseek-chat", "deepseek-reasoner"]


@dataclass
class AppConfig:
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: int = 60
    debug_log: bool = False
    #: 世界观一致性校验的最大重试次数，需求锁定为 3
    max_validate_retries: int = 3

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        """宽松解析：未知字段忽略、缺失字段取默认值、类型不对就回退默认值。"""
        cfg = cls()
        for key, default in asdict(cfg).items():
            if key not in data:
                continue
            value = data[key]
            if isinstance(default, bool):
                setattr(cfg, key, bool(value))
            elif isinstance(default, int):
                try:
                    setattr(cfg, key, int(value))
                except (TypeError, ValueError):
                    pass
            else:
                setattr(cfg, key, str(value))
        return cfg

    # ---------- 派生状态 ----------

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key.strip())

    def masked_key(self) -> str:
        """用于界面展示，绝不回显完整 Key。"""
        key = self.api_key.strip()
        if not key:
            return "未配置"
        if len(key) <= 10:
            return key[:2] + "*" * (len(key) - 2)
        return f"{key[:6]}{'*' * 6}{key[-4:]}"

    def normalized_base_url(self) -> str:
        url = self.base_url.strip() or DEFAULT_BASE_URL
        return url.rstrip("/")


@dataclass
class ConfigStore:
    """config.json 的读写。写入走「临时文件 + 原子替换」，避免写坏配置。"""

    path: Path = field(default_factory=lambda: CONFIG_FILE)

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> AppConfig:
        """读取配置。文件不存在或损坏时回退默认值，绝不抛异常打断启动。"""
        raw = read_json(self.path)
        if raw is None:
            # 配置损坏不该让程序起不来，退回默认配置即可
            return AppConfig()
        return AppConfig.from_dict(raw)

    def save(self, config: AppConfig) -> None:
        ensure_dirs()
        # restrict=True：这个文件里是 API Key，权限要收紧
        write_json_atomic(self.path, config.to_dict(), restrict=True)
