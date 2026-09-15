"""分阶段开场引导 service（§11.8，2026-08-20；2026-08-23 改「动作时刻生成+落库」）。

聊天开场是「分阶段的主动引导」——阶段判断硬（后端）、话术软（agent 生成）。
**生成时机 = 动作发生的时刻**（确认入库/生成/改写/应用建议/修复排版/回滚），生成后
立即落库为**当前 session 的一条 assistant 消息**（§11.8.3 原设计：新 session 的第一条
助手消息 = 该阶段的主动引导，替代静态欢迎语）。前端 loadChatHistory 只「读」历史，
不再触发生成——避免每次切页回来都重新生成（2026-08-23 修的漏洞）。

分层：各动作 service → 本 service（编排）→ repositories + llm_service。冷启动不经过
本 service（无 session 可挂靠，由前端 i18n 静态欢迎语兜底）。

失败策略（诚实出声）：引导词不是用户要求的动作，动作本身已成功。生成失败（没配
key / 网络断）**不抛异常**（否则动作接口返回失败、前端误判"确认失败"，脏状态），
而是落一条 assistant 说明——让用户知道「引导没生成 + 为什么」，且不掩盖"LLM 没配好"。
"""

import asyncio

from langchain_core.messages import HumanMessage
from app.core.errors import EmptyOutputError, LLMUnavailableError
from app.core.logging import logger
from app.prompts import load_opening_prompt
from app.repositories.documents import latest_document
from app.repositories.facts import list_facts
from app.repositories.optimization import list_applied_for_document
from app.services.facts import compute_info_gaps, render_info_gaps
from app.services.llm import llm_service
from app.services.sessions import current_session, record_assistant_message
from app.utils.facts import flatten_facts
from app.utils.resume_text import resume_prompt_text

# 各阶段该说的重点（注入 opening prompt 的 stage_hint）。
_STAGE_HINTS: dict[str, str] = {
    "uploaded": "用户刚上传并确认了简历（信息已抽进资料集）。**先别急着点评简历或给优化建议**——求职方向还没聊透。先跟用户聊求职方向（想投什么岗位、目标城市、技术方向），方向聊透了，再提之后的优化讨论范围。",
    "generated": "用户刚生成/改出一版新简历。告诉他这版做了哪些调整（看下面的改动清单），评价一下当前这版简历，然后问他觉得改得怎么样——先别主动开口提新的优化点，等他表态了再顺着聊。",
    "rolled_back": "用户刚回滚到一个旧版本。告诉他回到了哪一稿、之前在被覆盖的版本里做过哪些改动（看下面的改动清单）现在撤回了，问他这版想怎么调整。",
}

# LLM 生成失败时的诚实说明（落库为 assistant 消息，不弹通知不阻断动作）。
_FALLBACK = (
    "这次没能自动整理开场白：模型服务连不上（或还没配置 API key）。"
    "你可以先去设置里检查 LLM 配置，或者直接开始提问，我们照样能聊。"
)

# 当前在途开场引导的取消信号（真暂停用，对齐 chat.py 的 _current_cancel_event）。
# 单用户本地应用，同一时刻至多一个后台开场引导在跑（fire-and-forget），一把引用即可。
# 独立于聊天 / 抽取的 event——stop_opening 只点开场这把，不误伤其它在途任务。
_opening_cancel_event: asyncio.Event | None = None


async def _facts_text() -> str:
    """Active facts 展平（与对话 agent 注入同格式）。"""
    return flatten_facts(await list_facts(status="active"), bullet_title=True)


async def _recent_changes_text(document_id: int | None) -> str:
    """「这版改了哪些点」：查某稿应用的 confirmed 建议（§12.3 软标记）。

    generated：当前版应用的 confirmed 建议；rolled_back：被覆盖稿（调用方已按
    ref_document_id 精确给出，不再事后反推）。无建议改动时回退说明文字。
    """
    if document_id is None:
        return "（无）"
    applied = await list_applied_for_document(document_id)
    if not applied:
        return "（整份生成 / 无逐条优化建议记录）"
    return "\n".join(f"- [{s.type}] {s.target}：{s.original} → {s.suggested}（{s.reason}）" for s in applied)


async def generate_opening_content(stage: str, covered_document_id: int | None = None, cancel_event: asyncio.Event | None = None) -> str:
    """生成引导文本（不落库）。

    stage：uploaded / generated / rolled_back。covered_document_id：仅 rolled_back 用
    （被覆盖稿——回滚把当前稿标 superseded 后，查"这版改了什么"得指向被覆盖的那版）。
    cancel_event：真暂停信号（stop_opening set），透传给 llm_service 用于中途取消调用。

    失败抛 LLMUnavailableError / EmptyOutputError，由 persist_opening 接住转诚实说明。
    """
    doc = await latest_document()
    facts = await list_facts(status="active")
    gaps = render_info_gaps(compute_info_gaps(facts))
    # 改动清单的文档：rolled_back 用被覆盖稿（调用方当场握有 ref_document_id）；
    # 其余阶段用当前稿。
    changes_doc = covered_document_id if stage == "rolled_back" else (doc.id if doc else None)
    changes = await _recent_changes_text(changes_doc)
    prompt = load_opening_prompt(
        stage_hint=_STAGE_HINTS.get(stage, ""),
        facts=await _facts_text(),
        resume_document=resume_prompt_text(doc),
        info_gaps=gaps,
        recent_changes=changes,
    )
    result = await llm_service.call([HumanMessage(content=prompt)], cancel_event=cancel_event)
    content = result.content if isinstance(result.content, str) else str(result.content)
    if not content.strip():
        raise EmptyOutputError("开场引导生成空输出")
    logger.info("opening_generated", stage=stage, chars=len(content))
    return content.strip()


async def persist_opening(stage: str, covered_document_id: int | None = None, session_id: int | None = None, cancel_event: asyncio.Event | None = None) -> None:
    """动作时刻：生成引导并落库为当前 session 的一条 assistant 消息（§11.8）。

    生成成功 → 个性化引导；LLM 失败 → 诚实说明（不抛异常，不阻断动作）。
    被取消（stop_opening）→ 不落任何消息，静默返回（用户主动停了，不该留半截/说明）。
    无当前 session（理论上各动作点都先 record_current_event 开了种子/新 session）→ 记日志跳过。
    session_id：调用方已钉定的目标 session（后台调度用，见 schedule_opening）；
    None = 运行时取 current_session()。
    cancel_event：真暂停信号，透传给 generate_opening_content。
    """
    try:
        content = await generate_opening_content(stage, covered_document_id, cancel_event=cancel_event)
    except asyncio.CancelledError:
        # 被 stop_opening 取消：用户主动停了开场引导。CancelledError 是 BaseException，
        # 不能靠下方 except Exception 接——这里显式接住，不落消息、不向上抛（否则
        # fire-and-forget 任务冒「Task exception was never retrieved」刷日志）。
        logger.info("opening_generation_cancelled", stage=stage)
        return
    except (LLMUnavailableError, EmptyOutputError) as e:
        logger.warning("opening_generation_failed", stage=stage, error=str(e))
        content = _FALLBACK
    if session_id is not None:
        await record_assistant_message(session_id, content)
        logger.info("opening_persisted", session_id=session_id, stage=stage)
        return
    sess = await current_session()
    if sess is None or sess.id is None:
        logger.warning("opening_persist_no_session", stage=stage)
        return
    await record_assistant_message(sess.id, content)
    logger.info("opening_persisted", session_id=sess.id, stage=stage)


async def _persist_opening_safe(stage: str, covered_document_id: int | None, session_id: int | None, cancel_event: asyncio.Event | None) -> None:
    """后台任务兜底：未 await 的任务不能静默爆炸。

    persist_opening 已自兜 LLM 失败与取消，但 DB 等意外异常不该让 fire-and-forget 任务
    抛「Task exception was never retrieved」刷日志——这里统一接住并记录。
    """
    try:
        await persist_opening(stage, covered_document_id, session_id=session_id, cancel_event=cancel_event)
    except Exception:  # noqa: BLE001 - fire-and-forget 必须兜底，否则异常无人收
        logger.exception("opening_schedule_failed", stage=stage)


def schedule_opening(stage: str, covered_document_id: int | None = None, session_id: int | None = None) -> None:
    """fire-and-forget 版 persist_opening：动作接口秒回，引导在后台生成后落库。

    确认入库（confirm_extract）这类「无版本变更」的动作，不该被「生成开场引导」的
    一次 LLM 往返拖住整个确认请求（用户反馈：确认卡片后等太久）。这里把引导丢后台，
    落库后由前端短轮询聊天历史带回来。
    版本变更动作（生成/回滚/应用建议）仍走同步 persist_opening——那些动作本就在等
    新稿落定，引导作为新 session 第一条消息同步落库、顺序确定，不改成后台。
    session_id：钉定「调度时刻」的当前 session，避免后台任务跑完时 current_session
    已因后续动作切到别的 session（引导落错轮）。

    真暂停（2026-08-30）：为本次后台引导新建独立 cancel_event 存入 _opening_cancel_event，
    stop_opening set 它 → generate_opening_content 的 LLM 调用被中途取消、不落消息。
    """
    global _opening_cancel_event
    cancel_event = asyncio.Event()
    _opening_cancel_event = cancel_event
    asyncio.create_task(_persist_opening_safe(stage, covered_document_id, session_id, cancel_event))


def stop_opening() -> bool:
    """真暂停：取消当前在途的后台开场引导（暂停按钮触发）。

    set 当前 cancel_event → generate_opening_content 里的 llm_service.call 被取消 →
    persist_opening 接住 CancelledError 不落消息。无在途开场 → 返回 False（幂等，不误伤）。
    """
    global _opening_cancel_event
    ev = _opening_cancel_event
    if ev is None:
        return False
    ev.set()
    _opening_cancel_event = None
    logger.info("opening_generation_stop_requested")
    return True
