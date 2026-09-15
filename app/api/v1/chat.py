"""chat 路由：仅做 HTTP 收发与参数校验，业务交给 Service 层（红线：Router 不写业务）。

1.2/1.3 会话模型（docs/design/resume.md §11.3）：**增量式**——客户端只发一条新消息，
服务端持有唯一历史（读 session 全量 → append → 跑 agent → append 回复）。
错误由 service 层抛 AppError、全局 handler 统一收口（Router 不写 try/except）。
2026-08-14：POST /chat/stop 真停止（取消在途 generation，停烧 token 源头）。
"""

from fastapi import APIRouter, Body

from app.schemas.sessions import ChatRequestIn, ChatSessionResponse, ChatStopRequest, ChatStopResponse
from app.services.chat import handle_message, stop_chat
from app.services.opening import stop_opening

router = APIRouter()


@router.post("/chat", response_model=ChatSessionResponse)
async def chat(req: ChatRequestIn) -> ChatSessionResponse:
    """增量式聊天：处理一条用户消息，返回该 session 的完整历史（含新增回复）。"""
    return await handle_message(req.session_id, req.message.content)


@router.post("/chat/stop", response_model=ChatStopResponse)
async def stop(req: ChatStopRequest = Body(default=ChatStopRequest())) -> ChatStopResponse:
    """真停止：取消当前在途的聊天 generation（停止按钮触发）。

    retract=True（2026-09-09 取消重新输入）：顺带撤回最后一条 user 消息、原文回填输入框。
    缺省 False = 纯停止（兜底调用 cancelUpload/removeResume/loadChatHistory 复用）。
    """
    stopped = await stop_chat(retract=req.retract)
    return stopped


@router.post("/chat/opening/stop", response_model=ChatStopResponse)
async def stop_opening_route() -> ChatStopResponse:
    """真暂停：取消当前在途的后台开场引导（fire-and-forget，见 opening.stop_opening）。"""
    stopped = stop_opening()
    return ChatStopResponse(stopped=stopped)
