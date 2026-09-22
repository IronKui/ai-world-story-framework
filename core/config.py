"""本地 JSON 配置：API Key、接口地址、模型、超时。

配置只存在本地 data/config.json，不上传任何服务器。
注意：DeepSeek API Key 按需求以明文存于本地 JSON，
      文件权限在 POSIX 下收紧到 0600；Windows 下依赖用户目录 ACL。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.paths import CONFIG_FILE, ensure_dirs

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
        if not self.exists():
            return AppConfig()

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 配置损坏不该让程序起不来，退回默认配置即可
            return AppConfig()

        if not isinstance(raw, dict):
            return AppConfig()
        return AppConfig.from_dict(raw)

    def save(self, config: AppConfig) -> None:
        ensure_dirs()
        payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2)

        # 原子写：先写同目录临时文件再 replace，中途断电不会留下半个 JSON
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".config-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            self._restrict_permissions(tmp_path)
            os.replace(tmp_path, self.path)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def _restrict_permissions(self, path: str | Path) -> None:
        """POSIX 下把含 Key 的文件权限收到仅本人可读写。"""
        if os.name == "posix":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
