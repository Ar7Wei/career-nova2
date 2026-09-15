"""统一错误契约：全站异常的唯一出口（错误出声层）。

背景（2026-08-08 架构健壮性讨论）：错误过去散落在各 Router 的 try/except +
``raise HTTPException``，前端每个 store 各写各的解析——失败要么进黑洞（``catch {}``），
要么真因被吞（只显示"发送失败"）。本模块定契约，让出口收敛：

- **service 层抛 ``AppError`` 子类**（带 code/status/message/retryable/action）。
- **全局 handler**（``register_error_handlers``）统一收口 → 固定 JSON 形态：
  ``{code, message, detail, retryable, action}``。Router 不再手写 try/except。
- **前端拦截器**（api.ts）读一次这个形态，全站生效——业务 catch 从"5 行解析"变 2 行。

坏答案识别边界：有确定产物的任务（抽取/生成/改写）在 service 层校验空输出 → 抛
``EmptyOutputError``；聊天是开放对话不判"坏"（只靠异常层出声）。

分层红线：本模块不碰 DB / LLM / 业务，纯异常定义 + 注册。AppError 子类按需新增，
message 是面向用户的文案（可含出路提示），detail 是真因（可含技术细节）。
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.logging import logger

# 系统气泡·错误红条落库（ADR 0017，2026-09-01）：全局 handler 收口每个 AppError 时，
# 顺带把「错误事件」写进当前 session（kind=error_*，红色系统气泡，留痕可回看）。
# 分层红线：本模块不直接 import services/repositories（会成环 + 越层）。改由 services 层
# （services/error_events.py）在 import 时调 set_error_event_recorder 注入记录函数——
# core 只定义挂载点，不知道 DB 存在；谁注入谁负责下钻。
_error_event_recorder = None


def set_error_event_recorder(fn) -> None:
    """注入错误事件记录函数（services/error_events.py 在 import 时调用）。

    fn 签名：async (content: str, kind: str) -> None，负责把错误事件落库到当前 session。
    """
    global _error_event_recorder
    _error_event_recorder = fn


def _map_error_kind(code: str) -> str:
    """错误码 → 系统气泡 error kind（前端按 kind 判定红条，不靠文案匹配）。

    按错误来源归类；未知的 AppError 归 error_generic 兜底。
    """
    if code in ("llm_unavailable", "empty_output"):
        return "error_llm"
    if code == "conflict":
        return "error_conflict"
    if code == "not_found":
        return "error_not_found"
    if code == "file_too_large":
        return "error_upload"
    return "error_generic"


# ---------------------------------------------------------------------------
# 契约
# ---------------------------------------------------------------------------


class AppError(Exception):
    """业务/领域异常基类。service 层抛它，全局 handler 统一收口。"""

    # 机器可读错误码（前端分支用，稳定契约）
    code: str = "error"
    # HTTP 状态码
    status: int = 400
    # 面向用户的文案（可含出路提示）
    message: str = "出错了"
    # 真因（可含技术细节，前端可展示/记日志）
    detail: str = ""
    # 是否落库为系统气泡·错误红条（ADR 0017）。默认 True——用户该看到的失败留痕。
    # 协作式信号（EpochChanged）/ 瞬时护栏（并发互斥 409）置 False：不是「用户的失败」，
    # 落库成红条会吓人、也给共享连接的在途请求添并发写。
    record_event: bool = True
    # 能否重试（驱动前端"重试"按钮）
    retryable: bool = False
    # 恢复动作：open_settings / retry / reupload / confirm / cancel …
    action: str | None = None

    def __init__(self, message: str | None = None, detail: str | None = None, record_event: bool | None = None) -> None:
        """构造异常：可覆盖类级默认 message / detail / record_event，类默认保留。"""
        if message is not None:
            self.message = message
        if detail is not None:
            self.detail = detail
        if record_event is not None:
            self.record_event = record_event
        super().__init__(self.message)


class LLMUnavailableError(AppError):
    """模型服务连不上/超时/全部模型耗尽。可重试，引导去设置检查配置。"""

    code = "llm_unavailable"
    status = 503
    message = "模型服务连不上，检查设置里的 LLM 配置"
    retryable = True
    action = "open_settings"


class EmptyOutputError(AppError):
    """模型返回 200 但没给出可用的内容（空输出/结构非法）。可重试。"""

    code = "empty_output"
    status = 422
    message = "模型没给出内容，换个说法再试"
    retryable = True
    action = "retry"


class NotFoundError(AppError):
    """资源不存在（404）。保留具体 message（如"还没有简历文档"）。"""

    code = "not_found"
    status = 404


class ConflictError(AppError):
    """资源状态冲突（409，如抽取已过期/简历已就绪）。"""

    code = "conflict"
    status = 409


class PayloadTooLargeError(AppError):
    """文件过大（413）。"""

    code = "file_too_large"
    status = 413
    message = "文件太大了（上限 20MB）"
    action = "reupload"


class EpochChangedError(AppError):
    """数据代次已变（核爆/重新开始），在途抓取结果作废丢弃（2026-08-21 方案②）。

    不是错误——是「世界被重置」的协作式取消信号：爬虫开抓后数据被核爆，
    后端在写事务里发现 epoch 变了 → 丢弃不回写，返回这个码让调用方安静收尾。
    """

    code = "epoch_changed"
    status = 409
    message = "数据已被重置，这批抓取结果已丢弃"
    retryable = False
    action = None
    record_event = False  # 协作式取消信号，不是「用户的失败」——不落库红条


# ---------------------------------------------------------------------------
# 全局 handler 注册
# ---------------------------------------------------------------------------


def register_error_handlers(app: FastAPI) -> None:
    """注册全局 handler（main.py 调一次，全部端点生效）。"""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        # 系统气泡·错误红条落库（ADR 0017）：把这次失败记成错误事件进当前 session。
        # 仅当 exc.record_event（用户该看到的失败）；协作式信号/瞬时护栏（False）跳过。
        # 尽力而为、不阻断响应（记录本身失败不影响错误契约返回）。
        if exc.record_event and _error_event_recorder is not None:
            try:
                await _error_event_recorder(exc.message, kind=_map_error_kind(exc.code))
            except Exception:  # noqa: BLE001 - 落库失败不阻断错误契约
                logger.exception("error_event_record_failed", path=request.url.path)
        return JSONResponse(
            status_code=exc.status,
            content={
                "code": exc.code,
                "message": exc.message,
                "detail": exc.detail or str(exc),
                "retryable": exc.retryable,
                "action": exc.action,
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """兜底 500：非 AppError 的意外异常（框架/底层 bug，如响应头编码、SQL 异常）。

        2026-08-10 修复（架构盲区）：这类异常此前掉进 Starlette 默认 500 handler →
        text/plain 裸文本，穿破 JSON 契约——前端拿不到 code/retryable/action，只能显示
        "请求失败(500)" 且无真因。现在统一收口为契约 JSON + 真因进 detail。
        - **必须 logger.exception 记真因**：这是真 bug，只给用户 retry 不够，要能查日志定位。
        - 只捕获 Exception（不含 BaseException）：SystemExit/KeyboardInterrupt 等不吞。
        """
        logger.exception("unhandled_exception_500", path=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "message": "服务器出了点问题，请再试一次",
                "detail": str(exc),
                "retryable": True,
                "action": "retry",
            },
        )
