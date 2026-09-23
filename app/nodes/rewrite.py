"""编辑流水线节点：classify / content / validate_content（机器门）/ layout / validate_html。

每个节点调一个 agent（或确定性纯函数/校验），把结果写回 state。
红线：节点不碰 DB（写库是 service 的活）；validate_content / validate_html 只做确定性校验
（不调 LLM）。

2026-08-28 结构化转向（docs/adr/0004）：
- content：content/both → 在 resume JSON 上改内容（结构化输出）；layout/none → 原样透传。
- layout：**不再 LLM 排版**——固定模板 render_resume 从 resume JSON 确定性渲染 HTML（纯函数）。
  任何意图只要内容变了，都重渲染 HTML（HTML 是 JSON 的派生视图，永远一致）。
- validate_html：跑到这的所有情况都校验 new_html。
- none：已在图里短路，到不了这。

2026-09-07（ADR 0012 机器自愈门，第 2 块）：
- validate_content：content 后插「事实覆盖」确定性校验（find_missing_facts 返回缺失清单）。
  漏了沿 content_problems 喂回 content 补回（content 读它当硬性补回指令），MAX_ITERATIONS
  兜底。只拦「整条事实连影子都没进成品」的硬丢失；软质量是 LLM 的活，不归机器门。
"""

from collections.abc import Awaitable, Callable

from langgraph.graph.state import Command
from langgraph.types import interrupt

from app.agents.rewrite import run_classify_agent, run_content_agent
from app.core.logging import logger
from app.schemas.resume import resume_from_json
from app.schemas.rewrite import RewriteState
from app.utils.facts import find_missing_facts
from app.utils.html import validate_html
from app.utils.render import render_resume

# 冷启动生成器（facts → 结构化 resume JSON）：实现在 services/resume_edit.generate_json_from_facts
# （service 层，读 facts/preferences + 调 LLM）。nodes 直接 import services 会循环
# （services/rewrite import graphs/rewrite import nodes/rewrite），故 service 在装配时注入真实现，
# 测试可 patch。未注入即被调到属装配 bug。
generate_json_from_facts: Callable[..., Awaitable[str]] | None = None


async def classify_node(state: RewriteState) -> Command:
    """识别用户编辑意图。"""
    intent = await run_classify_agent(state.current_json, state.user_request)
    logger.info("rewrite_classified", intent=intent.text)
    return Command(update={"intent": intent})


async def cold_start_node(state: RewriteState) -> Command:
    """冷启动生成：无结构化 JSON（上传原件）→ facts + 侧重 + 用户请求生成第一版。

    用户的「改简历/生成」请求在冷启动下就是「生成第一版」的指令（改进要求注入生成 prompt）。
    产物落 new_json，直下 layout 渲染 → 人门。意图标 both（内容全新生成 + 待渲染），
    供下游机器门/渲染统一按「内容变了」处理。
    """
    if generate_json_from_facts is None:
        raise RuntimeError("cold_start_node 的 generate_json_from_facts 未注入（service 装配遗漏）")
    new_json = await generate_json_from_facts(
        target_role=state.target_role or None,
        instruction=state.user_request,
        preferences=state.preferences,
    )
    logger.info("rewrite_cold_start_generated", json_chars=len(new_json))
    return Command(update={
        "new_json": new_json,
        "summary": f"按你的要求生成第一版：{state.user_request[:20]}",
        "intent": state.intent.model_copy(update={"text": "both", "content_request": state.user_request}),
    })


async def content_node(state: RewriteState) -> Command:
    """内容优化：意图含 content → 在 resume JSON 上改内容；否则原样。

    S8（2026-08-14）：run_content_agent 返回 (json, summary)——summary 是版本名，
    一并写回 state 供 service 落库。无内容优化时 summary 保持空（service 回退规则名）。
    2026-09-07（ADR 0012）：content_problems 非空 = 机器门回边——把它当「硬性补回清单」
    喂 LLM，且本轮基于上一轮产出的 new_json 继续改（不是从 current_json 重来）。
    iterations +1（MAX_ITERATIONS 兜底防死循环，路由层读）。
    """
    intent = state.intent
    # 人门 revise 回边（feedback 非空）优先于首轮 intent：用户对人门草稿拍了「带意见重改」，
    # 本轮修改请求 = feedback——即使首轮是 layout 意图（content 原样透传过），这条意见也要照改，
    # 否则 feedback 会被 intent 守卫静默吞掉（改了个寂寞，再挂人门还是同一版）。
    if state.feedback or intent.text in ("content", "both"):
        # 人门 feedback 当本轮修改请求（区别于机器门 content_problems 的硬性补回清单——
        # 一个主观意见、一个确定性缺失）。
        request = state.feedback or intent.content_request or state.user_request
        base_json = state.new_json or state.current_json
        feedback = "\n".join(f"- {m}" for m in state.content_problems)
        new_json, summary = await run_content_agent(
            base_json, request, state.facts_text, feedback, state.target_role, state.preferences
        )
        return Command(update={"new_json": new_json, "summary": summary, "iterations": state.iterations + 1})
    # 无内容优化（首轮 layout 且未 revise）：内容层不动（交接原 JSON 给渲染），不增量（没有内容改写可回边）
    return Command(update={"new_json": state.current_json})


async def validate_content_node(state: RewriteState) -> Command:
    """机器门：事实覆盖校验（确定性，不调 LLM）。

    new_json 里每个 active 且 on_resume 的事实都必须有影子；漏了写回 content_problems
    （路由层据此回边 content 补回）。只对 content/both 生效——layout/none 无内容改写，
    机器门不适用（current_json 是上一版已通过校验的产物，无漏检风险；也不回边 content
    以免 content_node 对 layout 意图不做改写而空转死循环）。
    """
    if state.intent.text not in ("content", "both"):
        return Command(update={"content_problems": []})
    json_text = state.new_json or state.current_json
    missing = find_missing_facts(state.facts_text, resume_from_json(json_text)) if json_text.strip() else []
    logger.info("rewrite_content_validated", missing=len(missing), iterations=state.iterations)
    return Command(update={"content_problems": missing})


async def layout_node(state: RewriteState) -> Command:
    """渲染：固定模板从 resume JSON 渲染 HTML（纯函数，不调 LLM）。

    内容变了（或请求布局调整）→ 用新 JSON 重渲染。layout 顺序调整在 LLM 里体现为
    resume.layout 列表的改动（content 节点已产出新 JSON），这里只做确定性渲染。
    none 意图已在图里 classify 后短路到 END，到不了本节点；真到这属图路由 bug。
    """
    intent = state.intent
    if intent.text not in ("content", "layout", "both"):
        raise ValueError(f"layout_node 收到意外意图：{intent.text!r}（none 应在图里短路）")
    json_text = state.new_json or state.current_json
    if not json_text.strip():
        raise ValueError("layout_node 没有内容可渲染（resume JSON 为空）")
    resume = resume_from_json(json_text)
    new_html = render_resume(resume, typography=state.typography)
    return Command(update={"new_html": new_html})


async def validate_node(state: RewriteState) -> Command:
    """HTML 结构校验：问题写回 state（service 决定重试/抛错）。"""
    problems = validate_html(state.new_html) if state.new_html else []
    logger.info("rewrite_html_validated", problems=problems)
    return Command(update={"problems": problems})


async def human_gate_node(state: RewriteState) -> Command:
    """人门（ADR 0012 第 3 块）：渲染好的成品在这挂起，等人拍板——不落库由人定。

    `interrupt()` 把挂起态交给 checkpointer 持久化（抗重启），图在这冻结。payload 给人看的
    是草稿预览（new_json/new_html/summary + 机器门残留 content_problems）。恢复时
    `Command(resume={"decision": ..., "feedback": ...})` 的 resume 值成为 interrupt 返回值：
    confirm → decision=confirm（路由 END，service 落库 vN+1）；revise → 带 feedback 回 content 重改。
    """
    answer = interrupt({
        "new_json": state.new_json,
        "new_html": state.new_html,
        "summary": state.summary,
        "content_problems": state.content_problems,  # 机器门超限残留（补不齐的交人拍板）
    })
    decision = answer.get("decision", "") if isinstance(answer, dict) else str(answer)
    feedback = answer.get("feedback", "") if isinstance(answer, dict) else ""
    logger.info("rewrite_human_gate_resumed", decision=decision, has_feedback=bool(feedback))
    return Command(update={"decision": decision, "feedback": feedback})
