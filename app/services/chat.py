"""chat 服务：Router 与对话 agent 之间的粘合层（分层铁律要求的正确位置）。

Router 不直接调 graph。1.2/1.3 的会话模型（docs/design/resume.md §11）：
- **增量式**：客户端只发一条新消息；服务端是历史的唯一持有者。
  读 session 全量历史 → append 用户消息 → 跑 agent → append 助手回复 → 返回完整历史。
- **对话 agent 是简历采集/优化姿态**（app/graphs/chat.py：工具循环住图，ADR 0016）。
- **发送序列 = 当前 session 消息**（UI 干净轮回，§11.1 修订）；event 角色注入前过滤（§11.3）。
- **真停止（2026-08-14）**：run_chat_agent 整个包进 run_with_cancel（复用 llm_service 的取消
  竞争原语），stop_chat set event 掐断在途 generation（停烧 token 的源头，不只是前端
  不收回复）。单用户 + generating 已挡并发发送 → 一个模块级 cancel_event 引用即可。

对话记录双投影（ADR 0016）：`chat_messages` 表 = 前端 UI 真相源（原文/系统气泡，Append-only）；
checkpointer（graphs/chat.py，thread_id=chat:{session_id}）= agent 工作记忆。二者同生共死——
撤回时两侧同删最后一条 user 消息。
"""

import asyncio

from app.core.errors import NotFoundError
from app.core.logging import logger
from app.graphs.chat import retract_last_user_message_from_graph, run_chat_agent
from app.schemas.sessions import ChatSessionResponse, ChatStopResponse
from app.services import chat_tools
from app.services.chat_tools._assembly import begin_turn
from app.services.direction import clear_pending_disposal, get_pending_disposal, get_direction_changed, clear_direction_changed
from app.services.llm import run_with_cancel
from app.services.optimization import count_open_records
from app.services.rewrite import get_preview
from app.services.sessions import (
    current_session,
    open_session,
    read_messages,
    record_assistant_message,
    record_event_message,
    record_user_message,
    retract_last_user_message,
)


async def _run_agent(sid: int, text: str) -> tuple[str, bool]:
    """跑对话 deep agent 图（工具循环住图，ADR 0016）。

    run_chat_agent 吃单条新消息，历史从 checkpointer 恢复，摘要靠 SummarizationMiddleware。
    version_changed 恒 False——出稿走人门挂起预览，不写版本（确认走独立端点）。
    装配注入（工具 + 重建 prompt 函数）在跑前调一次（幂等，service → graph 单向）。
    """
    chat_tools.inject_into_graph()
    reply = await run_chat_agent(sid, text)
    return reply, False


# 当前在途对话的取消信号（真停止用）。单用户本地应用同一时刻最多一个在途对话
# （前端 generating 已挡并发发送），一个模块级引用即可——不是共享全局 event，是
# 「每轮生成新建一把只属于它自己的 event」，stop_chat 只点聊天这把，不误伤抽取等。
_current_cancel_event: asyncio.Event | None = None

# 当前在途对话的「发送 session id」（撤回定位用，2026-09-09）。与 _current_cancel_event
# 同型：单用户单在途，一个引用即可。stop_chat(retract=True) 用它判断「发送时的 session
# 是否仍是当前 session」——apply_suggestions 中途 open_session 会切走当前 session，
# 此时撤回应静默降级为纯停止（撤不到，不回填）。
_current_send_session_id: int | None = None


def _clear_current_cancel_event() -> None:
    """清空当前在途取消信号引用（测试/finally 用）。"""
    global _current_cancel_event
    _current_cancel_event = None


async def handle_message(session_id: int | None, text: str) -> ChatSessionResponse:
    """处理一条用户消息：增量式。

    1. 无 session → 开种子会话（document_version=None）。
    2. 持久化用户消息。
    3. 跑对话 agent（工具循环住图，历史在 checkpointer）→ 助手回复。
    4. 持久化助手回复，返回 session 完整历史。

    session_id 指向的会话不存在时抛 NotFoundError（Router 转 404）。

    真停止：run_chat_agent 包进 run_with_cancel（cancel_event 先到 → 整段工具循环被取消）。
    stop_chat 由停止按钮触发，set event → 这里抛 CancelledError 向上（前端已作废该响应）。
    """
    global _current_cancel_event
    global _current_send_session_id

    if session_id is None:
        sid = (await open_session()).id
        assert sid is not None
    else:
        sid = session_id

    # 校验会话存在（历史只读会话不可续聊——由前端保证不调，后端兜底）
    sess = await current_session()
    if sess is None or sess.id != sid:
        raise NotFoundError("会话不存在或已不是当前会话，刷新后重试")

    await record_user_message(sid, text)

    # 注册在途信号：位置在 record_user_message 之后（stop_chat 撤回要读到已落库的 user 消息）。
    cancel_event = asyncio.Event()
    _current_cancel_event = cancel_event
    _current_send_session_id = sid
    # 记本轮开跑时间点：render_panel 据此标「← 你刚改的」行（本轮被动过的建议）。
    begin_turn()
    try:
        # deep：历史在 checkpointer（thread_id=chat:sid），只发单条新消息；摘要靠
        # SummarizationMiddleware 循环内折叠。不需要读 chat_messages 全量历史。
        reply_text, version_changed = await run_with_cancel(_run_agent(sid, text), cancel_event)
    except asyncio.CancelledError:
        # 被停止（stop_chat set event）：干净收尾，不让 CancelledError 穿透到 ASGI 层
        # （它是 BaseException 不是 Exception，会绕过 FastAPI 的兜底 handler 一路冒到
        # uvicorn，把请求以异常结束）。这里补「已停止」事件气泡 + 正常返回当前消息。
        await record_event_message(sid, "已停止回复")
        logger.info("chat_generation_stopped", session_id=sid)
        return ChatSessionResponse(session_id=sid, messages=await read_messages(sid))
    finally:
        # 无论正常完成还是被取消，都清掉引用（已无在途对话）
        if _current_cancel_event is cancel_event:
            _current_cancel_event = None
        if _current_send_session_id == sid:
            _current_send_session_id = None

    # 孤儿回复修复（2026-08-25 §12.6 D4）：工具（apply_suggestions 等写库工具）
    # 可能在跑的过程中开新 session（版本变更 = 新 session）。agent 的最终回复属于
    # **新** session，不是进来时的旧 sid——旧实现写进旧 sid，前端 loadChatHistory 一刷
    # 就再也看不到（每轮版本变更白烧一次 LLM）。这里重读当前 session，把回复写进去。
    sess_after = await current_session()
    reply_sid = sess_after.id if sess_after is not None else sid
    await record_assistant_message(reply_sid, reply_text)

    # 本轮产出的改动记录数（suggest_improvements/record_change 已落库，前端据此提示「去面板查看」）
    suggestion_count = await count_open_records()
    # ADR 0017（2026-09-01 系统气泡三档）：建议提示落库为「事件灰条」（kind=suggestion_hint），
    # 不再由前端硬编码 assistant 文本气泡——系统动作与对话内容分开呈现，且留痕可回看。
    # 文案用系统口吻「已放 N 条」（非对话口吻「给你 N 条」）。
    if suggestion_count > 0:
        await record_event_message(reply_sid, f"已放 {suggestion_count} 条建议进优化点，去面板确认或聊一聊", kind="suggestion_hint")
    # 是否有待确认的简历草稿（agent 出稿工具挂起后，前端据此显示「确认保存」入口）
    preview_pending = bool((await get_preview()).strip())
    # agent 落定方向后待处置的未处理岗位数（refine_direction_tool 只读不暂存，commit 后
    # 存单例 → 这里读走透传前端就地弹「留/删」确认 → 清空单例，避免下轮重复弹）。
    pending_disposal = get_pending_disposal() or 0
    if pending_disposal:
        clear_pending_disposal()
    # agent 是否落定了方向（commit_direction 后 set → 这里读走透传前端 direction_changed
    # → 前端刷新 directionStore 跨页同步方向标签 → 清空，避免下轮重复触发）。
    direction_changed = get_direction_changed()
    if direction_changed:
        clear_direction_changed()
    logger.info(
        "chat_message_handled",
        session_id=reply_sid,
        suggestion_count=suggestion_count,
        preview_pending=preview_pending,
        pending_disposal=pending_disposal,
        direction_changed=direction_changed,
        version_changed=version_changed,
    )
    return ChatSessionResponse(
        session_id=reply_sid,
        messages=await read_messages(reply_sid),
        suggestion_count=suggestion_count,
        preview_pending=preview_pending,
        pending_disposal_count=pending_disposal,
        direction_changed=direction_changed,
        version_changed=version_changed,
    )


async def stop_chat(retract: bool = False) -> ChatStopResponse:
    """真停止：取消当前在途的对话 generation（停止按钮触发）。

    set 当前 cancel_event → run_with_cancel 里 task.cancel() 整段工具循环 → 烧 token
    的源头被掐断。无在途对话 → 返回 stopped=False（幂等，不误伤）。

    retract=True（取消重新输入，2026-09-09）：顺带撤回最后一条 user 消息，原文回填
    输入框。撤回**仅当**「发送时的 session 仍是当前 session」才做——apply_suggestions
    中途 open_session 会把当前 session 切走，此时撤回没意义，静默降级为纯停止
    （retracted=False）。撤回在 set event 之前快照 send session，避免与 handle_message
    的 finally 清理竞态。

    收尾（补「已停止」事件气泡）由 handle_message 的 CancelledError 分支做——它知道
    当前 session，且能干净返回响应，不让取消异常穿透到 ASGI 层。
    """
    global _current_cancel_event
    ev = _current_cancel_event
    if ev is None:
        return ChatStopResponse(stopped=False, retracted=False, retracted_text="")
    # set event 之前快照 send session（撤回定位）；避免 finally 已清引用时读到 None
    send_sid = _current_send_session_id

    # 先撤回（若需要且 session 未切）：此时 handle_message 仍 in-flight（run_chat_agent 挂起），
    # 无并发 DB 写——撤回串行在「已停止回复」事件写入之前，避免两个 async_session 并发写
    # 同一张表（SQLite 内存库锁冲突）。
    retracted_text = ""
    if retract and send_sid is not None:
        sess = await current_session()
        if sess is not None and sess.id == send_sid:
            text = await retract_last_user_message(send_sid)
            if text is not None:
                retracted_text = text
                # 双投影一致（甲方案，2026-09-09）：表删了，checkpointer 的 thread 也得删
                # 同一条 user 消息——否则 agent 下次恢复上下文还看得到已撤回的话，表与
                # agent 记忆脱钩。
                await retract_last_user_message_from_graph(send_sid)

    # 再 set event 触发取消（handle_message 收尾写「已停止回复」事件）
    ev.set()
    logger.info("chat_generation_stop_requested", retract=retract)
    if retracted_text:
        logger.info("chat_message_retracted", session_id=send_sid)
        return ChatStopResponse(stopped=True, retracted=True, retracted_text=retracted_text)
    return ChatStopResponse(stopped=True, retracted=False, retracted_text="")
