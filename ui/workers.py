"""后台线程：所有会阻塞的网络请求都通过这里跑，避免界面卡死。

阶段 5 之后 AI 生成、校验等耗时调用也复用 Worker 基类。
"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from core.api_client import DeepSeekClient, TestResult


class ApiTestThread(QThread):
    """在后台执行 API 连通性测试。

    QThread 自带 finished 信号，这里换个名字避免覆盖。
    """

    #: 测试结束，携带 TestResult
    done = pyqtSignal(object)

    def __init__(
        self,
        client: DeepSeekClient,
        mode: str = "connection",
        parent=None,
    ):
        super().__init__(parent)
        self._client = client
        self._mode = mode
        #: 线程跑完后的原始异常，供调试日志记录
        self.error: BaseException | None = None

    def run(self) -> None:  # noqa: D102 - QThread 入口
        try:
            if self._mode == "completion":
                result = self._client.test_completion()
            else:
                result = self._client.test_connection()
        except BaseException as exc:  # noqa: BLE001
            # test_* 内部已把网络异常转成 TestResult，
            # 走到这里说明是预料外的 bug，也不能让线程静默死掉
            self.error = exc
            result = TestResult(
                ok=False,
                title="内部错误",
                message=f"测试过程中出现未预期的错误：{type(exc).__name__}: {exc}",
            )

        self.done.emit(result)
