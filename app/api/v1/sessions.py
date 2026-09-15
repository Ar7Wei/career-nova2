"""sessions 路由：会话的创建、列表与消息读取。

纯 HTTP 收发，业务交给 Service 层。历史 session 只读查看（版本面板）；当前 session = id 最大。
错误由 service 层抛 AppError、全局 handler 统一收口。
"""

from fastapi import APIRouter

from app.core.errors import NotFoundError
from app.core.logging import logger
from app.schemas.sessions import (
    MessagesResponse,
    Session,
    SessionCreate,
    SessionsResponse,
)
from app.services.sessions import all_sessions, get_session_by_id, open_session, read_messages

router = APIRouter()


@router.get("/sessions", response_model=SessionsResponse)
async def sessions(document_id: int | None = None) -> SessionsResponse:
    """列会话（版本面板历史 session 只读查看用）；可选按文档 id 过滤。"""
    return SessionsResponse(sessions=await all_sessions(document_id=document_id))


@router.post("/sessions", response_model=Session, status_code=201)
async def create_session(req: SessionCreate) -> Session:
    """开新会话（版本变更——生成/回滚/应用建议——后前端调）。"""
    logger.info("session_created", document_id=req.document_id)
    return await open_session(document_id=req.document_id)


@router.get("/sessions/{session_id}", response_model=Session)
async def get_session(session_id: int) -> Session:
    """读一个会话（含消息数）。"""
    sess = await get_session_by_id(session_id)
    if sess is None:
        raise NotFoundError("会话不存在")
    return sess


@router.get("/sessions/{session_id}/messages", response_model=MessagesResponse)
async def messages(session_id: int) -> MessagesResponse:
    """读某 session 的完整消息（挂载恢复 + 历史只读查看）。"""
    if await get_session_by_id(session_id) is None:
        raise NotFoundError("会话不存在")
    return MessagesResponse(messages=await read_messages(session_id))
