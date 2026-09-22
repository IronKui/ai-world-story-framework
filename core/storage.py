"""本地 JSON 读写：原子写入 + 容错读取。

配置、世界观、存档三处都要落盘，这里统一收口，避免把
「临时文件 + os.replace」的原子写逻辑抄三遍。

两条硬要求：
  · 写入必须原子 —— 中途断电/崩溃不能留下半个 JSON 文件
  · 读取必须容错 —— 单个文件损坏不能让整个程序起不来
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def write_json_atomic(
    path: Path, payload: dict, *, restrict: bool = False
) -> None:
    """把 payload 原子地写到 path。

    restrict=True 时在 POSIX 下把权限收到 0600（用于含 API Key 的文件）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if restrict:
            _restrict_permissions(tmp_name)
        os.replace(tmp_name, path)
    except BaseException:
        # 任何失败都要清掉临时文件，否则会在目录里越堆越多
        Path(tmp_name).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> dict | None:
    """读 JSON 对象。文件不存在、损坏、或顶层不是对象时返回 None。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _restrict_permissions(path: str | Path) -> None:
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
