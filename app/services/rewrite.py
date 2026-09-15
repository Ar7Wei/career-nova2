"""「出简历」统一流水线 service（ADR 0012 合并：生成与改写一个入口、同一条图）。

生成（无当前 JSON，冷启动 facts→生成）与改写（有当前 JSON，暖态增量改）是同一件事的
两半——都是「出草稿 → 人看一眼 → 确认落库」。本 service 把外层收敛成「人门两段式」：

- `generate_preview`：跑统一图到 human_gate 挂起（产出经 checkpointer 落挂起态），**不写库**。
  挂起草稿从 checkpointer 按派生 thread_id 查回（重启不丢），preview 端点 / chat 据此读。
  冷/暖由图入口判（`_route_entry`：无 current_json → cold_start facts 生成；有 → classify 暖态）。
  侧重信号（target_role/preferences）与目标岗位（target_role 存 facts 槽位）在这层注入/落库。
- `generate_confirm`：人拍板。confirm → `Command(resume=)` 恢复图拿最终产物 → 落库 vN+1
  （结清/开 session/引导长尾原地不动，只挪触发点）→ 清草稿；revise → 带 feedback 回图重改、
  再挂人门（不写库）。

分层红线：graph 纯推理不碰 DB；写库/结清/开 session/落 facts 在本层（service）。
"""

import asyncio

from app.core.errors import ConflictError, EmptyOutputError
from app.core.logging import logger
from app.graphs.rewrite import clear_draft, peek_intent, read_draft, resume_gate, run_preview
from app.services.llm import run_with_cancel
from app.nodes import rewrite as rewrite_nodes
from app.repositories.documents import latest_document
from app.repositories.facts import list_facts, update_fact
from app.schemas.facts import FactCreate, FactUpdate
from app.schemas.resume import Typography
from app.schemas.rewrite import RewriteState
from app.services.documents import save_document, document_task
from app.services.facts import confirm_facts
from app.services.opening import persist_opening
from app.repositories.optimization import list_suggestions
from app.schemas.optimization import PendingSuggestion
from app.services.optimization import clear_settled_for_new_version
from app.services.resume_edit import build_facts_text, build_focus, generate_json_from_facts
from app.services.sessions import open_session, record_current_event

# 冷启动生成器注入（nodes 不直接 import services 防循环：services → graphs → nodes）。
rewrite_nodes.generate_json_from_facts = generate_json_from_facts

# 当前在途「重新生成」（人门 revise 回边）的取消信号（真停止）。与 chat.py 的
# _current_cancel_event 同型：单用户同一时刻最多一条 revise 在跑（前端 confirming 已锁
# 防重复点），一个模块级引用即可。revise 跑图（content 节点 + LLM）在 chat 循环之外，
# 不经 /chat/stop，所以要有自己的 stop 端点单独点它；confirm 纯写库（快）、不用取消。
_generate_cancel_event: asyncio.Event | None = None


def stop_generation() -> bool:
    """真停止：取消当前在途的「重新生成」（revise 回边，重新生成按钮触发）。

    set 当前 cancel_event → run_with_cancel 里 task.cancel() 整段图重改（含 in-flight LLM）
    → 停烧 token 的源头。无在途 revise → 返回 False（幂等，不误伤）。
    """
    global _generate_cancel_event
    ev = _generate_cancel_event
    if ev is None:
        return False
    ev.set()
    logger.info("generate_stop_requested")
    return True


def _suggestions_text(suggestions: list[PendingSuggestion]) -> str:
    """把一批已确认建议渲染成指令文本（落进 user_request 槽，进图）。

    ADR 0015：「开始改」= 应用已确认建议，是原料本体——渲染成文本与口头请求同槽，
    冷启动自然变成 cold_start 的 instruction、暖态自然变成 content 的请求，不加新字段。
    """
    return "\n".join(
        f"- [{s.type}] {s.target}: {s.original} → {s.suggested}（{s.reason}）" for s in suggestions
    )


async def save_target_role(target_role: str) -> None:
    """目标岗位作为事实（basic 类）存 user_facts——单值槽位，旧的先 superseded 再写新。

    方向槽位不上简历（ADR 0011）：on_resume=False，生成 prompt / 机器门都不带它（只作侧重）。
    """
    role = target_role.strip()
    if not role:
        return
    role_fact = f"目标岗位：{role}"
    for f in await list_facts(category="basic", status="active"):
        if f.title.startswith("目标岗位：") and f.title != role_fact:
            await update_fact(f.id, FactUpdate(status="superseded"))
    await confirm_facts([FactCreate(category="basic", title=role_fact, on_resume=False)], source="manual", detect_conflict=True, auto_adjudicate=True)


async def generate_preview(user_request: str, target_role: str | None = None, apply_confirmed: bool = False) -> tuple[str, str]:
    """出草稿：跑统一流水线到人门挂起，返回 (结构化 resume JSON, 渲染 HTML)，**不写库**。

    target_role：显式给的目标岗位（冷启动生成常用）——先落 facts 槽位（幂等），再进图。
    apply_confirmed：真 = 「开始改」——把已确认建议渲染成文本并进 user_request（ADR 0015
    两个入口等价同路，建议是原料本体、必带无条件）。
    意图 none（与改简历无关）→ ConflictError。新一轮 preview 覆盖同一条 thread 的旧草稿
    （单用户单在途）。用户请求 = 改进要求（冷启动作生成指令 / 暖态作修改请求）。
    2026-09-07：**零文档可生成**——连上传都没有时，从 facts 直接冷启动出第一版
    （Typography 默认）；有文档则继承其排版配置。
    """
    async with document_task():
        if target_role:
            await save_target_role(target_role)
        request = user_request
        if apply_confirmed:
            # 「开始改」：已确认建议渲染成指令文本，与用户原话拼接进同一 request 槽。
            confirmed = await list_suggestions(status="confirmed")
            if not confirmed:
                raise ConflictError("没有已确认的建议——先在左栏确认或聊完后确认")
            suggestions_text = _suggestions_text(confirmed)
            request = f"{user_request}\n\n应用以下已确认的优化建议：\n{suggestions_text}" if user_request.strip() else f"应用以下已确认的优化建议：\n{suggestions_text}"
        doc = await latest_document()
        focus_role, preferences = await build_focus()
        state = RewriteState(
            current_json=doc.resume_json if doc is not None else "",
            current_html=doc.html if doc is not None else "",
            user_request=request,
            facts_text=await build_facts_text(),
            typography=doc.typography if doc is not None else Typography(),  # 继承当前版排版；无文档 → 默认
            target_role=focus_role,  # 侧重信号（service 读方向槽位注入，Node 不碰 DB）
            preferences=preferences,  # 侧重信号（service 读 custom 偏好注入）
        )
        await run_preview(state)

        # none 分流先于 None 守卫：接真 checkpointer 时 none 经 classify 短路 END、图跑完
        # 无挂起 → read_draft() 返回 None；若先判 None，none 会被通用兜底吞掉，用户看不到
        # 「这个请求不是改简历」的具体提示。故先探图里的分类意图（peek_intent 读 checkpoint 最新值）。
        intent_text = await peek_intent()
        if intent_text == "none":
            raise ConflictError("这个请求不是改简历——告诉我你想改简历的哪些内容或布局")

        draft = await read_draft()
        if draft is None:
            # 图跑穿到 END 且非 none：无 checkpointer（内存跑）→ 探不到意图也读不回挂起态，
            # 或意图已短路但 intent 探针未命中（防御）。统一兜底。
            raise ConflictError("这次没能产出待确认的草稿——请换个说法再试")
        if draft.intent.text == "none":
            # 无 checkpointer 内存跑时 none 的兜底（此时 peek_intent 为 None，靠 draft 判）。
            raise ConflictError("这个请求不是改简历——告诉我你想改简历的哪些内容或布局")
        if draft.content_problems:
            # 机器门超限残留：补不齐的交人门拍板（人门 payload 已带），这里只留痕。
            logger.info("rewrite_content_problems_unresolved", missing=len(draft.content_problems), iterations=draft.iterations)
        logger.info("generate_preview_staged", cold_start=doc is None or not doc.resume_json.strip(), json_chars=len(draft.new_json))
        return draft.new_json, draft.new_html


async def get_preview() -> str:
    """当前挂起草稿的结构化真身 JSON（空串 = 无挂起）。preview 端点 / chat preview_pending 用。"""
    draft = await read_draft()
    return draft.new_json if draft is not None else ""


async def get_preview_html() -> str:
    """当前挂起草稿的渲染快照 HTML（空串 = 无挂起）。"""
    draft = await read_draft()
    return draft.new_html if draft is not None else ""


async def generate_confirm(decision: str = "confirm", feedback: str = "") -> tuple[int, str, str] | None:
    """人门拍板：confirm → 落库 vN+1 返回 (版本号, json, html)；revise → 回图重改返回 None。

    confirm：恢复图拿最终产物 → 落库（结清/开 session/引导长尾原地不动，只挪触发点）→ 清草稿。
    revise：带 feedback 回 content 重改、再挂人门，不写库（返回 None，前端再拉预览）。
    无挂起草稿（对着空/旧预览拍板）→ ConflictError。
    """
    async with document_task():
        draft = await read_draft()
        if draft is None:
            raise ConflictError("没有待确认的简历草稿——先出一版再确认")

        if decision == "revise":
            # 真停止（2026-09-08）：revise 回图重改（content + LLM）包进 run_with_cancel，
            # cancel_event 先到 → 整段图重改被取消（停烧 token）。CancelledError 是
            # BaseException 会绕过 AppError handler，故在 service 层捕获干净收尾——
            # 与 chat.py handle_message 的取消收尾同型。取消会打断 resume 中途的图
            # （human_gate 的 interrupt 已消费、content 没跑完），挂起态变坏，故清草稿
            # （前端 refreshPreview 读到空 → 收起预览条，回落到上一版已保存文档）。
            global _generate_cancel_event
            cancel_event = asyncio.Event()
            _generate_cancel_event = cancel_event
            try:
                await run_with_cancel(resume_gate("revise", feedback), cancel_event)
            except asyncio.CancelledError:
                await clear_draft()
                logger.info("generate_revise_stopped")
                return None
            finally:
                if _generate_cancel_event is cancel_event:
                    _generate_cancel_event = None
            logger.info("generate_revise_reentered", feedback=feedback[:30])
            return None

        # confirm：恢复图跑完，拿最终产物落库（渲染/校验在图里已做，这里直接用产物）。
        final = await resume_gate("confirm")
        new_json = final.new_json or draft.new_json
        if not new_json.strip():
            raise EmptyOutputError("草稿内容为空——重新出一版再确认")
        summary = final.summary or "出新版简历"
        new_html = final.new_html or draft.new_html

        # 写库（resume_json + html 双写）+ 版本变更统一结清 + 开新 session（长尾原地不动）
        saved = await save_document(markdown="", source="generated", html=new_html, summary=summary, resume_json=new_json, typography=draft.typography)
        await clear_draft()  # 落库后清草稿：下一轮 preview 从干净 thread 起
        counts = await clear_settled_for_new_version(saved.id)
        if any(counts.values()):
            logger.info("pending_suggestions_cleared_on_confirm", cleared=counts)
        await open_session(document_id=saved.id)
        await record_current_event(f"已确认，简历保存为新版本（第 {saved.version} 稿）；新一轮对话开始", kind="generated")
        await persist_opening("generated")
        logger.info("resume_confirm_saved", version=saved.version, json_chars=len(new_json), html_chars=len(new_html))
        return saved.version, new_json, new_html
