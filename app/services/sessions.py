"""sessions service：会话存储的读写编排。

纯数据读写（无 LLM），Service 直接走 Repository（不绕 Graph）。
会话语义见 docs/design/resume.md §11。1.2/1.3 的聊天 agent 接入后，
service 会加"读历史 + 跑 agent + append 回复"的编排（见 app/services/chat.py）。
"""

from app.core.config import settings
from app.repositories.sessions import (
    append_message,
    create_session,
    delete_last_user_message,
    get_session,
    latest_session_id,
    list_messages,
    list_sessions,
)
from app.schemas.chat import Message
from app.schemas.sessions import Session

# 种子会话的欢迎语（2026-08-30 落库保留）：与前端 i18n resume.welcome 逐字一致——
# 无 session 时前端本地显示这份文案兜底，一旦开种子 session 落库为第一条 assistant 消息，
# loadChatHistory 用后端消息覆盖时欢迎语才不消失。两份文案同一份事实，改任一处需同步改另一处。
_WELCOME_ZH = "你好，我是你的简历助手。可以先上传一份现有简历让我了解你，也可以直接跟我聊聊你的经历——我边听边记。"
_WELCOME_EN = "Hi, I'm your resume assistant. Upload an existing resume so I can learn about you, or just tell me about your experience — I'll take notes as we talk."


def _welcome_text() -> str:
    """按当前语言返回种子会话欢迎语（后端唯一真相，文案与前端 i18n 对齐）。"""
    return _WELCOME_ZH if settings.APP_LANGUAGE != "en" else _WELCOME_EN


async def open_session(document_id: int | None = None) -> Session:
    """开新会话（版本变更——生成/回滚/应用建议——时调用，见 resume.md §11.1）。

    document_id = 会话开始时的当前文档 id（唯一身份，A3）；None = 种子会话。
    种子会话（document_id=None，尚无简历）落库欢迎语作为第一条 assistant 消息——
    否则前端 loadChatHistory 用后端消息覆盖时，本地欢迎语（纯前端、不落库）会消失。
    """
    sess = await create_session(document_id=document_id)
    if document_id is None:
        assert sess.id is not None
        await record_assistant_message(sess.id, _welcome_text())
    return sess


async def current_session() -> Session | None:
    """当前会话 = id 最大的那个；无会话返回 None。"""
    sid = await latest_session_id()
    if sid is None:
        return None
    return await get_session(sid)


async def get_session_by_id(session_id: int) -> Session | None:
    """按 id 读会话（历史 session 只读查看用）。"""
    return await get_session(session_id)


async def all_sessions(document_id: int | None = None) -> list[Session]:
    """列会话（版本面板历史 session 只读查看用）；可选按文档 id 过滤。"""
    return await list_sessions(document_id=document_id)


async def read_messages(session_id: int) -> list[Message]:
    """读某 session 的完整消息（挂载恢复 + 历史查看 + agent 上下文）。

    ADR 0017：kind/ref_document_id 透传（仅 event 行有值），前端按 kind 判定系统气泡
    三档呈现；注入 LLM 上下文前 chat.py 会过滤 event 行。
    """
    rows = await list_messages(session_id)
    return [Message(role=r.role, content=r.content, kind=r.kind, ref_document_id=r.ref_document_id) for r in rows]


async def record_user_message(session_id: int, content: str) -> None:
    """持久化一条用户消息（seq 自动递增）。"""
    await append_message(session_id, "user", content)


async def retract_last_user_message(session_id: int) -> str | None:
    """撤回某 session 最后一条 user 消息，返回原文（无 user 消息返回 None）。

    「取消重新输入」（2026-09-09）：停止后撤回 + 回填。薄封装 repo，守分层——
    chat.py 不直接碰 repo。
    """
    return await delete_last_user_message(session_id)


async def record_assistant_message(session_id: int, content: str) -> None:
    """持久化一条助手回复。"""
    await append_message(session_id, "assistant", content)


async def record_event_message(session_id: int, content: str, kind: str = "", ref_document_id: int | None = None) -> None:
    """持久化一条系统事件消息（上传/确认/回滚等，见 resume.md §11.3）。

    事件以 **event 角色**存储（区别于 user/assistant）：前端据此渲染系统气泡
    （灰底居中、不回显用户气泡）；chat.py 注入 agent 前过滤 event——系统事件
    不污染 LLM 上下文（agent 无需处理，回看时仍能看到）。
    kind：结构化动作类型（§11.8，upload/generated/rolled_back…），阶段判定读它不匹配文案。
    ref_document_id：事件引用的文档 id（rolled_back = 被覆盖稿，§11.8 精确溯源）；其余 None。
    """
    await append_message(session_id, "event", content, kind=kind, ref_document_id=ref_document_id)


async def record_current_event(content: str, kind: str = "", ref_document_id: int | None = None) -> int:
    """统一事件落库入口：确保有当前 session（无则开种子），写事件，返回 session id。

    所有「有确定产物的动作」（上传/确认/回滚/生成/应用建议）在 service 层调它，
    事件归进当前 session 的发送序列。回滚/生成等开新 session 的动作，先 open_session
    再调本函数（事件进新 session）。kind = 结构化动作类型（§11.8 阶段判定用）。
    ref_document_id：事件引用的文档 id（回滚 = 被覆盖稿，供 §11.8 精确溯源）。
    """
    sess = await current_session()
    if sess is None:
        sess = await open_session()
        assert sess.id is not None
    await record_event_message(sess.id, content, kind=kind, ref_document_id=ref_document_id)
    return sess.id
