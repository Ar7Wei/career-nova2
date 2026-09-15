"""系统气泡·错误红条落库（ADR 0017，2026-09-01）：把 AppError 失败记成错误事件进当前 session。

统一错误契约（core/errors.py）的全局 handler 收口每个 AppError 时，调本模块注入的
record_error_event 把失败留痕成「错误红条」（kind=error_*，红色系统气泡）——
与成功事件（灰条）同一真相源（后端 chat_messages），不再是「成功后端记、失败前端拼」的双源。

分层：core/errors.py 不碰 DB（红线），故由本模块（services 层，合法下钻 repositories）
在 import 时把记录函数注入 core 的挂载点（set_error_event_recorder）。
谁 import 本模块，注入即生效——main.py 启动链路上 import 一次即可。
"""

from app.core.errors import set_error_event_recorder
from app.core.logging import logger
from app.services.sessions import current_session, record_event_message


async def record_error_event(content: str, kind: str) -> None:
    """把一条错误事件写进当前 session（kind=error_*）。

    无当前 session 时跳过（错误事件挂在会话上；冷启动无会话可挂时，错误已由
    前端 toast/气泡当下提示，不落库不丢关键信息）。
    """
    sess = await current_session()
    if sess is None or sess.id is None:
        logger.info("error_event_skipped_no_session", kind=kind)
        return
    await record_event_message(sess.id, content, kind=kind)


# 注入 core 挂载点：import 本模块即生效（main.py 启动链路 import 一次）。
set_error_event_recorder(record_error_event)
