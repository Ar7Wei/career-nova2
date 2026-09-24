"""出稿提案：「提案 → 用户回话 → 才许执行」的门（件 3，2026-09-24）。

**解决什么**：聊天 agent 以前可以在自己觉得「聊得差不多了」时直接调 `generate_resume` /
`apply_suggestions` 跑图出稿——用户毫无准备，UI 跳到预览确认态，没聊完的点晾在一边。
现在它**只能提案**；出稿必须等用户回过一句话。

**门怎么算「用户回过话」**：提案时记下当前 session 的 **user 消息条数**；执行时要求
条数**已经涨了**。因为 `handle_message` 是先 `record_user_message` 再跑 agent，
agent 在同一轮里无法自己把条数涨上去——"自问自答"物理上不可能。

**为什么卡在工具层**（不是 service / 图）：这只约束 **agent 的手**。面板按钮 / HTTP 端点
走 service 直调，不经这里——**用户点按钮本身就是明确同意**，不该再要一次提案。

**为什么不落库**：与 `direction._pending_disposal` 同型——单用户单在途对话，一把单例够用；
提案是瞬态信号（通常下一轮就被消费），落库要加表/列，不值得。
⚠️ 代价：提案后重启应用会丢提案，用户得重提一次（可接受）。

单例的复位在 conftest 的 fresh_db teardown（与 chat._current_cancel_event 同处）。
"""

from typing import Literal

from pydantic import BaseModel

from app.core.logging import logger
from app.repositories.sessions import list_messages
from app.services.sessions import current_session

# 出稿类型：generate 出稿（生成/改写）/ apply 开始改（应用已确认改动）。
ExecutionKind = Literal["generate", "apply"]


class ExecutionProposal(BaseModel):
    """一条待用户拍板的出稿提案。

    - kind：出稿类型（generate / apply）。
    - brief：agent 拟的完整指令——**摊给用户看的**（以前它隐式塞进工具参数，用户看不见，
      没法当场纠正「我不是这个意思」）。
    - session_id / user_turns：提案时的定位 + 「用户回过话没有」的判据（见模块 docstring）。
    """

    kind: ExecutionKind
    brief: str = ""
    session_id: int = 0
    user_turns: int = 0


_proposal: ExecutionProposal | None = None


def _reset_for_tests() -> None:
    """测试用：清掉待决提案（免跨测试误判『有提案』）。"""
    global _proposal
    _proposal = None


async def _user_turn_count(session_id: int) -> int:
    """该 session 的 user 消息条数（「用户说过几句话」——判『提案之后用户回过话没有』）。"""
    return sum(1 for m in await list_messages(session_id) if m.role == "user")


async def propose(kind: ExecutionKind, brief: str = "") -> ExecutionProposal:
    """记下一条出稿提案（记当前 session + 用户消息条数）。返回提案本体。"""
    sess = await current_session()
    session_id = sess.id if sess is not None and sess.id is not None else 0
    turns = await _user_turn_count(session_id) if session_id else 0
    proposal = ExecutionProposal(kind=kind, brief=brief.strip(), session_id=session_id, user_turns=turns)
    global _proposal
    _proposal = proposal
    logger.info("execution_proposed", kind=kind, session_id=session_id, user_turns=turns)
    return proposal


def clear() -> None:
    """清掉待决提案（执行放行后 / 换代时）。"""
    global _proposal
    _proposal = None


async def check(kind: ExecutionKind) -> str | None:
    """执行前的门：放行返回 None，拦住返回**给 agent 看的**可读原因。

    两道判据：
    1. **有提案且类型对得上**——没有提案 / 提的是另一种出稿 → 拦。
    2. **用户回过话**——当前 user 消息条数 > 提案时的条数。
    """
    proposal = _proposal
    if proposal is None:
        return (
            "还没跟用户确认出稿。**先调 propose_execution 提案**，把「会带上哪些、这版不带哪些」"
            "讲给用户听，问他「现在出一版，还是接着聊」——用户回话后再调本工具。"
        )
    if proposal.kind != kind:
        return (
            f"当前待确认的是另一种出稿（{proposal.kind}），跟这次要做的（{kind}）不是一回事。"
            f"先确认清楚用户要的是哪种，必要时用 propose_execution 重新提案。"
        )
    sess = await current_session()
    session_id = sess.id if sess is not None and sess.id is not None else 0
    turns = await _user_turn_count(session_id) if session_id else 0
    if turns <= proposal.user_turns:
        return (
            "刚提完案，用户还没回话——**不能自己就把稿出了**。"
            "等用户答复（出一版 / 接着聊 / 改哪里），下一轮再执行。"
        )
    return None


def current() -> ExecutionProposal | None:
    """当前待决提案（供 chat.py 透传前端 / prompt 注入）。"""
    return _proposal


async def render_execution_plan() -> str:
    """渲染提案清单（给 agent **念给用户听**）：这版会带上哪些、不带哪些。

    由代码从改动记录算，不让 agent 复述——复述会丢、会编（同 ADR 0015 把建议交给代码渲染的理由）。
    落一句「不带」比什么都重要：用户看到「这条会被落下」才有机会说「先别出，把它聊完」。
    """
    from app.services.optimization import pending_records

    include: list[str] = []
    skip: list[str] = []
    for r in await pending_records():
        for c in r.changes:
            if c.status == "confirmed":
                include.append(f"{c.target or '（未定位）'}（{r.reason}）")
            elif c.status in ("pending", "discussing"):
                skip.append(f"{c.target or '（未定位）'}（{r.reason}·{c.status}）")

    lines = [
        "**这版会带上的改动点**：" + ("；".join(include) if include else "（没有已确认的改动点）"),
        "**这版不带（留到以后）**：" + ("；".join(skip) if skip else "（没有待定/正在聊的点）"),
    ]
    if _proposal is not None and _proposal.brief:
        lines.append(f"**你拟的指令（给用户看，确认没夹带私货）**：{_proposal.brief}")
    return "\n".join(lines)
