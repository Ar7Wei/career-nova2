"""chat_sessions / chat_messages repository：会话的读写。唯一碰这两张表的地方。

不知道 LLM 存在（分层红线）；返回 schemas 层模型（RORO），不裸表行。
会话语义（见 docs/design/resume.md §11）：每版本一 session，版本变更各开新 session，
当前 session = id 最大的那个；旧 session 只读（回看模式，§11bis）。消息 seq 保证顺序。
"""

from sqlmodel import func, select

from app.models.session import ChatMessage, ChatSession
from app.repositories.base import async_session_maker
from app.schemas.sessions import Message as SessionMessage
from app.schemas.sessions import Session

def _to_session(row: ChatSession, message_count: int) -> Session:
    """表行 → 传输模型。"""
    return Session(
        id=row.id or 0,
        document_id=row.document_id,
        message_count=message_count,
        created_at=row.created_at.isoformat(),
    )


def _to_message(row: ChatMessage) -> SessionMessage:
    """表行 → 传输模型（ADR 0017：kind/ref_document_id 透传，前端按 kind 判定系统气泡三档）。"""
    return SessionMessage(role=row.role, content=row.content, kind=row.kind, ref_document_id=row.ref_document_id)  # type: ignore[arg-type]


async def create_session(document_id: int | None = None) -> Session:
    """新建会话；返回带 id 的完整模型。document_id = 会话开始时的当前文档 id（唯一身份）。"""
    async with async_session_maker() as session:
        row = ChatSession(document_id=document_id)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_session(row, 0)


async def get_session(session_id: int) -> Session | None:
    """按 id 读一个会话；不存在返回 None。"""
    async with async_session_maker() as session:
        row = await session.get(ChatSession, session_id)
        if row is None:
            return None
        count = (
            await session.exec(
                select(func.count(ChatMessage.id)).where(ChatMessage.session_id == session_id)  # type: ignore[arg-type]
            )
        ).one()
        return _to_session(row, int(count))


async def list_sessions(document_id: int | None = None) -> list[Session]:
    """列会话（按 id 升序 = 时间顺序）；可选按文档 id 过滤。"""
    async with async_session_maker() as session:
        stmt = select(ChatSession).order_by(ChatSession.id)  # type: ignore[arg-type]
        if document_id is not None:
            stmt = stmt.where(ChatSession.document_id == document_id)
        rows = (await session.exec(stmt)).all()
        out: list[Session] = []
        for row in rows:
            count = (
                await session.exec(
                    select(func.count(ChatMessage.id)).where(ChatMessage.session_id == row.id)  # type: ignore[arg-type]
                )
            ).one()
            out.append(_to_session(row, int(count)))
        return out


async def append_message(
    session_id: int,
    role: str,
    content: str,
    kind: str = "",
    ref_document_id: int | None = None,
) -> SessionMessage:
    """给会话追加一条消息（seq = 会话内当前最大值 + 1）；返回带 id 的完整模型。

    kind：event 行的结构化动作类型（upload/generated/rolled_back…，§11.8）；非 event 行空串。
    ref_document_id：event 行引用的文档 id（rolled_back = 被覆盖稿，§11.8 精确溯源）；其余 None。
    """
    async with async_session_maker() as session:
        max_seq = (
            await session.exec(
                select(func.max(ChatMessage.seq)).where(ChatMessage.session_id == session_id)  # type: ignore[arg-type]
            )
        ).one_or_none() or 0
        row = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            kind=kind,
            ref_document_id=ref_document_id,
            seq=int(max_seq) + 1,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_message(row)


async def latest_event_kind(session_id: int) -> str | None:
    """读某 session 最近一条 event 消息的结构化动作类型 kind（§11.8 阶段判定用）。

    无 event / event 无 kind 返回 None。不匹配文案——读结构化 kind 列（apply.md §7.3 铁律）。
    """
    async with async_session_maker() as session:
        row = (
            await session.exec(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .where(ChatMessage.role == "event")
                .order_by(ChatMessage.seq.desc())  # type: ignore[attr-defined]
            )
        ).first()
        if row is None or not row.kind:
            return None
        return row.kind


async def latest_event_ref_document(session_id: int) -> int | None:
    """读某 session 最近一条 event 消息的 ref_document_id（§11.8 回滚精确溯源）。

    回滚 event 记了「被覆盖稿」id——回滚引导据此精确说「上一版改了什么」。无 event /
    event 无 ref_document_id（非回滚事件或旧数据）返回 None（回退兜底）。
    """
    async with async_session_maker() as session:
        row = (
            await session.exec(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .where(ChatMessage.role == "event")
                .where(ChatMessage.ref_document_id.is_not(None))  # type: ignore[union-attr]
                .order_by(ChatMessage.seq.desc())  # type: ignore[attr-defined]
            )
        ).first()
        if row is None:
            return None
        return row.ref_document_id


async def list_messages(session_id: int) -> list[SessionMessage]:
    """读某 session 的全部消息（seq 升序）。"""
    async with async_session_maker() as session:
        rows = (
            await session.exec(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.seq)  # type: ignore[arg-type,call-overload]
            )
        ).all()
        return [_to_message(r) for r in rows]


async def latest_session_id() -> int | None:
    """当前 session = id 最大那个；无会话返回 None。"""
    async with async_session_maker() as session:
        max_id = (await session.exec(select(func.max(ChatSession.id)))).one_or_none()
        return int(max_id) if max_id is not None else None


async def delete_last_user_message(session_id: int) -> str | None:
    """删某 session 最后一条 user 消息，返回其原文（无 user 消息返回 None）。

    「取消重新输入」（2026-09-09）：停止后撤回最后一条 user 消息，原文回填输入框。
    只删 user（不动 assistant/event），按 seq 降序取首条 = 最后一条。
    """
    async with async_session_maker() as session:
        row = (
            await session.exec(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .where(ChatMessage.role == "user")
                .order_by(ChatMessage.seq.desc())  # type: ignore[attr-defined]
            )
        ).first()
        if row is None:
            return None
        content = row.content
        await session.delete(row)
        await session.commit()
        return content


async def delete_all_sessions() -> int:
    """清空会话**连同其消息**（重置简历连聊天一起清时用）。返回删除的会话数。

    S1-6（2026-08-13）：原实现只删 session 不删 message → 孤儿消息。先删全部消息
    再删会话，保证不留悬空行。
    """
    async with async_session_maker() as session:
        msgs = (await session.exec(select(ChatMessage))).all()
        for m in msgs:
            await session.delete(m)
        rows = (await session.exec(select(ChatSession))).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)
