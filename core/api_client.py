"""DeepSeek API 客户端。

阶段 2 只实现：连通性探测 + 错误分类（把 HTTP/网络异常翻译成玩家看得懂的话）。
阶段 5 会复用这里的 _request()，往上叠 chat 对话封装与 JSON 结构化输出。

依赖只用标准库 urllib，不引入 requests —— 少一个依赖，网络出问题也少一层黑盒。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
import warnings
from dataclasses import dataclass, field

from core.config import DEFAULT_BASE_URL, DEFAULT_MODEL

# Python 3.14 读取 Windows 证书存储时会对个别第三方根证书报这个警告。
# 默认校验路径本身是好的（握手能成功），这里只是压掉噪音。
warnings.filterwarnings(
    "ignore", message="Bad certificate in Windows certificate store"
)

#: 探测连通性用的最小请求，不消耗任何 token
MODELS_PATH = "/models"
CHAT_PATH = "/chat/completions"


class ApiError(Exception):
    """统一的 API 异常。

    message 已经是给玩家看的文案，调用方可以直接塞进弹窗。
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "unknown",
        status: int | None = None,
        detail: str = "",
    ):
        super().__init__(message)
        self.message = message
        self.kind = kind          # auth / quota / rate_limit / network / server / format
        self.status = status
        self.detail = detail      # 原始响应片段，调试日志用

    def __str__(self) -> str:
        return self.message


@dataclass
class TestResult:
    """连通性测试结果，直接驱动界面展示。"""

    ok: bool
    title: str
    message: str
    detail: str = ""
    latency_ms: int = 0
    models: list[str] = field(default_factory=list)
    cost_hint: str = ""


def _classify_http_error(status: int, body: str) -> ApiError:
    """把 HTTP 状态码翻译成可读文案。

    状态码含义参考 DeepSeek 官方错误码表。
    """
    # 尽量从响应体里挖出更精确的服务端说明
    server_msg = ""
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            err = parsed.get("error")
            if isinstance(err, dict):
                server_msg = str(err.get("message", ""))
            elif isinstance(err, str):
                server_msg = err
            server_msg = server_msg or str(parsed.get("message", ""))
    except (json.JSONDecodeError, AttributeError):
        pass

    table = {
        400: ("format", "请求格式错误", "发送给 AI 的请求格式有问题，通常是程序内部错误。"),
        401: ("auth", "API Key 无效", "请检查 Key 是否填写正确、是否已被删除或重置。"),
        402: ("quota", "账户余额不足", "DeepSeek 账户余额不足，请先充值后再试。"),
        403: ("auth", "没有访问权限", "该 API Key 无权访问此接口。"),
        404: ("format", "接口地址不存在", "请检查 Base URL 是否填写正确。"),
        422: ("format", "请求参数不合法", "模型名或参数不被服务端接受。"),
        429: ("rate_limit", "请求过于频繁", "触发了服务端限流，请稍后再试。"),
        500: ("server", "服务端内部错误", "DeepSeek 服务器出现问题，请稍后重试。"),
        503: ("server", "服务端繁忙", "DeepSeek 服务器当前负载过高，请稍后重试。"),
    }

    kind, title, advice = table.get(
        status,
        ("server" if status >= 500 else "unknown", f"服务端返回 {status}", "请稍后重试。"),
    )

    message = advice
    if server_msg:
        message = f"{advice}\n\n服务端说明：{server_msg}"

    return ApiError(message, kind=kind, status=status, detail=body[:500])


def _classify_network_error(exc: Exception, url: str = "") -> ApiError:
    """网络层异常 → 可读文案。

    url 用于在提示里带上真实目标地址 —— Base URL 是可配置的，
    文案里写死 api.deepseek.com 会在用户改过地址后产生误导。
    """
    target = _host_of(url) if url else ""

    if isinstance(exc, socket.timeout) or isinstance(exc, TimeoutError):
        where = f"访问 {target} " if target else "连接"
        return ApiError(
            f"{where}超时。\n\n"
            "可能是网络不稳定，或当前网络需要代理才能访问该地址。",
            kind="network",
            detail=repr(exc),
        )

    reason = getattr(exc, "reason", None)
    if isinstance(reason, socket.gaierror):
        what = f"无法解析域名 {target}" if target else "无法解析域名"
        return ApiError(
            f"{what}。\n\n请检查网络连接与 DNS 设置。",
            kind="network",
            detail=repr(exc),
        )

    if isinstance(reason, ConnectionRefusedError):
        return ApiError(
            "连接被拒绝。\n\n如果配置了代理，请确认代理地址和端口是否正确。",
            kind="network",
            detail=repr(exc),
        )

    cert_error = _find_ssl_error(exc)
    if cert_error is not None:
        return ApiError(
            "SSL 证书校验失败。\n\n如果使用了抓包工具或公司代理，可能需要配置其根证书。",
            kind="network",
            detail=repr(exc),
        )

    return ApiError(
        f"网络请求失败：{exc}", kind="network", detail=repr(exc)
    )


def _host_of(url: str) -> str:
    """从 URL 里取出主机名，取不到就返回空串。"""
    try:
        from urllib.parse import urlparse

        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def _find_ssl_error(exc: BaseException) -> BaseException | None:
    """在异常链里找 ssl.SSLError。"""
    import ssl

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLError):
            return current
        current = current.__cause__ or current.__context__
    return None


class DeepSeekClient:
    """DeepSeek 接口客户端。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: int = 60,
    ):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.timeout = max(int(timeout or 60), 1)

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------

    def _request(
        self, path: str, payload: dict | None = None, *, timeout: int | None = None
    ) -> dict:
        """发一次请求并返回解析后的 JSON。

        所有失败路径统一抛 ApiError，调用方不需要碰 urllib 的异常体系。
        """
        if not self.api_key:
            raise ApiError(
                "尚未配置 API Key。\n\n请先在「设置 → API 设置」中填入你的 DeepSeek API Key。",
                kind="auth",
            )

        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # 不带 UA 的话部分网络环境会直接掐连接
            "User-Agent": "ai-world-story-framework/0.1",
        }

        body = None
        method = "GET"
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            method = "POST"

        request = urllib.request.Request(
            url, data=body, headers=headers, method=method
        )

        try:
            with urllib.request.urlopen(
                request, timeout=timeout or self.timeout
            ) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            # HTTPError 也是合法响应，先读 body 再分类
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - 读不到 body 不影响主流程
                pass
            raise _classify_http_error(exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise _classify_network_error(exc, url) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise _classify_network_error(exc, url) from exc
        except OSError as exc:
            raise _classify_network_error(exc, url) from exc

        if not raw.strip():
            return {}

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(
                "服务端返回的内容不是合法 JSON。\n\n"
                "常见原因是网络中间有拦截页面，或 Base URL 指向了非 API 地址。",
                kind="format",
                detail=raw[:500],
            ) from exc

        return parsed if isinstance(parsed, dict) else {"data": parsed}

    # ------------------------------------------------------------------
    # 连通性测试
    # ------------------------------------------------------------------

    def test_connection(self) -> TestResult:
        """零成本鉴权探测：拉取模型列表，验证 Key 与网络。

        不会产生任何 token 消耗，账单上不会有这次调用。
        """
        started = time.perf_counter()
        try:
            data = self._request(MODELS_PATH)
        except ApiError as exc:
            latency = int((time.perf_counter() - started) * 1000)
            title = {
                "auth": "认证失败",
                "network": "网络连接失败",
                "rate_limit": "被限流",
                "server": "服务端异常",
            }.get(exc.kind, "连接失败")
            return TestResult(
                ok=False,
                title=title,
                message=exc.message,
                detail=exc.detail,
                latency_ms=latency,
            )

        latency = int((time.perf_counter() - started) * 1000)
        models = self._extract_models(data)

        model_note = ""
        if models and self.model not in models:
            model_note = f"\n\n注意：当前配置的模型「{self.model}」不在返回列表中，请确认模型名是否正确。"

        return TestResult(
            ok=True,
            title="连接成功",
            message=(
                f"API Key 有效，网络连通正常。\n\n"
                f"接口地址：{self.base_url}\n"
                f"当前模型：{self.model}\n"
                f"可用模型：{'、'.join(models) if models else '（服务端未返回列表）'}"
                f"{model_note}"
            ),
            latency_ms=latency,
            models=models,
            cost_hint="本次测试未消耗任何 token",
        )

    def test_completion(self) -> TestResult:
        """完整测试：真的发一次对话请求，验证模型可调用。

        会产生极少量 token 消耗（约 10~20 token）。
        """
        started = time.perf_counter()
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
            "temperature": 0,
        }

        try:
            data = self._request(CHAT_PATH, payload)
        except ApiError as exc:
            latency = int((time.perf_counter() - started) * 1000)
            return TestResult(
                ok=False,
                title="调用失败",
                message=exc.message,
                detail=exc.detail,
                latency_ms=latency,
            )

        latency = int((time.perf_counter() - started) * 1000)

        choices = data.get("choices") or []
        if not choices:
            return TestResult(
                ok=False,
                title="返回内容异常",
                message="服务端返回成功，但没有 choices 字段，无法确认模型是否真的可用。",
                detail=json.dumps(data, ensure_ascii=False)[:500],
                latency_ms=latency,
            )

        usage = data.get("usage") or {}
        total_tokens = usage.get("total_tokens", "?")

        return TestResult(
            ok=True,
            title="调用成功",
            message=(
                f"模型「{self.model}」可正常调用。\n\n"
                f"接口地址：{self.base_url}\n"
                f"本次消耗 token：{total_tokens}"
            ),
            latency_ms=latency,
            cost_hint=f"本次测试消耗约 {total_tokens} token",
        )

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_models(data: dict) -> list[str]:
        """从 /models 响应里取出模型 id 列表。"""
        entries = data.get("data")
        if not isinstance(entries, list):
            return []

        models: list[str] = []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id"):
                models.append(str(entry["id"]))
            elif isinstance(entry, str):
                models.append(entry)
        return models
