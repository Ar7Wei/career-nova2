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
from pydantic import ValidationError

from app.agents.rewrite import run_classify_agent, run_content_agent, run_verify_changes_agent
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
    2026-09-23：机器门回边已停（见 MAX_ITERATIONS），本节点不再收到「硬性补回清单」——
    `{facts_feedback}` 段恒为空。
    """
    intent = state.intent
    # 人门 revise 回边（feedback 非空）优先于首轮 intent：用户对人门草稿拍了「带意见重改」，
    # 本轮修改请求 = feedback——即使首轮是 layout 意图（content 原样透传过），这条意见也要照改，
    # 否则 feedback 会被 intent 守卫静默吞掉（改了个寂寞，再挂人门还是同一版）。
    if state.feedback or state.missed_changes or state.structure_problems or intent.text in ("content", "both"):
        # 结构塌回边（structure_problems 非空）优先级最高：上一版产出根本无法解析/渲染，
        # 本轮不是「优化」而是「重出一份结构完好的」——明确告诉它塌在哪、必须出合法结构。
        if state.structure_problems:
            collapsed_lines = "\n".join(f"- {p}" for p in state.structure_problems)
            request = (
                f"你上一版输出的简历 JSON 结构塌了，无法解析渲染：\n{collapsed_lines}\n\n"
                "这次**必须**输出结构完整、合法的简历 JSON：basics 是对象、work/education/skills/projects "
                "等是对象数组（每项都是对象，不是字符串）、字段类型正确。"
                "内容语义保持不变，只把结构修正成合法形态。\n\n"
                f"原始修改请求：{state.feedback or intent.content_request or state.user_request}"
            )
        # 改动落实门回边（missed_changes 非空）优先级其次：这是机器判出的「聊定了但没落实」的
        # 硬缺漏，本轮必须把这几条补改进去——渲染成具体指令（缺哪条、该改成什么、现在是什么样），
        # 不是笼统的「你再改改」。其次才是人门 feedback / 首轮请求。
        elif state.missed_changes:
            missed_lines = "\n".join(
                f"- {m.target}：应改为「{m.suggested}」（现在还是：{m.note or '未改'}）" for m in state.missed_changes
            )
            request = (
                f"以下几条改动是用户已确认要改、但你上一版没落实到位的，**这次必须改到**：\n{missed_lines}\n\n"
                "如果你认为其中某条其实**已经改对了**（复核看错了），照样把它保持正确即可，并在 "
                "objection 字段用一句话说明「这条无需再改，因为……」——你的异议会被复核再看一次。"
                "真有遗漏的才动手改。\n\n"
                f"在此基础上，其余部分保持不变。原始修改请求：{state.feedback or intent.content_request or state.user_request}"
            )
        else:
            # 人门 feedback 当本轮修改请求（用户的主观意见，区别于已停用的机器门硬性补回清单）。
            request = state.feedback or intent.content_request or state.user_request
        base_json = state.new_json or state.current_json
        # 事实覆盖门回边已停（2026-09-23），没有「必须补回事实」的硬清单了。
        new_json, summary, objection = await run_content_agent(
            base_json, request, state.facts_text, state.target_role, state.preferences
        )
        # structure_problems 一次性消费：本轮补改完就清掉，下轮 validate_structure 重新检测——
        # 不残留到下一轮被误读成「还塌着」。
        return Command(update={"new_json": new_json, "summary": summary, "iterations": state.iterations + 1, "content_objection": objection, "structure_problems": []})
    # 无内容优化（首轮 layout 且未 revise）：内容层不动（交接原 JSON 给渲染），不增量
    return Command(update={"new_json": state.current_json})


async def validate_structure_node(state: RewriteState) -> Command:
    """结构完整性门（2026-09-30）：content 产出后、渲染前，校验 resume JSON 没塌。

    判据 = 能不能干净 `resume_from_json`（确定性 pydantic 校验，不调 LLM、零误报）。
    「硬塌」——work 元素是 str、basics 是 str、work 非 list、顶层非 dict——pydantic 无法归一，
    `model_validate_json` 直接抛 ValidationError（实测：extra:"ignore" 只会让「软塌/字段异名」
    静默丢空，那种由 content 的全空守卫拦；真正漏网、渲染成代码的就是这里的硬塌）。

    干净 → structure_problems 置空、不耗预算；塌 → 记下塌在哪个字段 + structure_iterations+1，
    供路由回边 content 补改（超限由路由抛 EmptyOutputError，不渲染）。
    """
    json_text = state.new_json or state.current_json
    try:
        resume_from_json(json_text)
    except ValueError as e:
        problems = _collapse_locations(e)
        logger.info("rewrite_structure_collapsed", problems=problems, structure_iterations=state.structure_iterations)
        return Command(update={
            "structure_problems": problems,
            "structure_iterations": state.structure_iterations + 1,
        })
    logger.info("rewrite_structure_ok", structure_iterations=state.structure_iterations)
    return Command(update={"structure_problems": [], "structure_iterations": state.structure_iterations})


def _collapse_locations(error: ValueError) -> list[str]:
    """从 pydantic ValidationError 提取「塌在哪个字段」的可读清单（供 content 定位补改）。

    取每条错误的 loc（如 work.0 / basics）拼成「work[0] 不是合法对象」式描述；提取失败兜底
    一句通用描述，绝不因格式化再抛一次（这里是错误处理路径，自己不能崩）。
    """
    try:
        if not isinstance(error, ValidationError):
            return [f"resume JSON 无法解析：{str(error)[:120]}"]
        problems: list[str] = []
        for err in error.errors()[:5]:  # 最多列 5 条，防一份全塌的稿子刷几十条
            loc = ".".join(str(p) for p in err.get("loc", ()))
            label = loc or "顶层"
            # work.0 → work[0]，更贴近 content 补改时的定位习惯
            if "." in label:
                head, *rest = label.split(".")
                label = head + "".join(f"[{p}]" if p.isdigit() else f".{p}" for p in rest)
            problems.append(f"{label} 结构塌了（{err.get('msg', '类型不符')}）")
        return problems or ["resume JSON 结构不完整"]
    except Exception:  # noqa: BLE001 - 错误格式化路径自身绝不抛
        return ["resume JSON 结构不完整"]


async def validate_content_node(state: RewriteState) -> Command:
    """机器门（**已停用回边**，只留痕）：事实覆盖校验（确定性，不调 LLM）。

    new_json 里每个 active 且 on_resume 的事实都应"有影子"；没影子的写进 content_problems
    **仅供留痕**（人门 payload / 日志），路由层不再据此回边——见 graphs/rewrite.py 的
    MAX_ITERATIONS 注释。

    为什么停回边（2026-09-23）：判据是「title 的 token 有没有在成品全文出现」，太糙——
    专名保住就放行、专名被改写（正是"美化"的常态）就报，跟"内容丢没丢"关系不大。
    当天 7 轮出稿 21 次校验只有 3 次干净放行、4 次打满 3 轮把模型逼回去。判据待重做
    （按条目比对 / 只查硬字段），这之前宁可放宽不可误伤。
    """
    if state.intent.text not in ("content", "both"):
        return Command(update={"content_problems": []})
    json_text = state.new_json or state.current_json
    missing = find_missing_facts(state.facts_text, resume_from_json(json_text)) if json_text.strip() else []
    logger.info("rewrite_content_validated", missing=len(missing), iterations=state.iterations, enforced=False)
    return Command(update={"content_problems": missing})


async def verify_changes_node(state: RewriteState) -> Command:
    """改动落实门（2026-09-28）：逐条核对「已确认改动」在新稿里落实了没（LLM 语义复核）。

    与上面的「事实覆盖门」是**两道不同的门**：那道管「资料集事实丢没丢」（判据糙、回边已停），
    本门管「用户拍板要改的 confirmed 改动落实了没」——判据是 LLM 看结果不看字面，所以敢回边。

    只在有 confirmed 改动（state.confirmed_changes_text 非空，即「开始改」apply_confirmed）时跑；
    纯 generate / 口头改写没有 confirmed 清单可核 → missed 置空、不调用 LLM（不空转、不误拦）。
    复核产物 missed_changes 写回 state：非空 → 路由回 content 补改；空 / 超限 → 往下走 layout。
    """
    if not state.confirmed_changes_text.strip():
        return Command(update={"missed_changes": []})
    old_json = state.current_json
    new_json = state.new_json or state.current_json
    result = await run_verify_changes_agent(state.confirmed_changes_text, old_json, new_json, state.content_objection)
    logger.info(
        "rewrite_changes_verify_node",
        missed=len(result.missed),
        skipped=len(result.skipped),  # 可核率信号：长期大量 skipped = 上游收录改动写得太虚
        verify_iterations=state.verify_iterations,
        had_objection=bool(state.content_objection),
    )
    # objection 一次性消费：本轮仲裁完就清掉，免得残留到下一轮复核被误读成「还有新异议」。
    # 落实门回边预算走独立 verify_iterations（与人门 revise 的 iterations 分开，见路由注释）。
    return Command(update={
        "missed_changes": result.missed,
        "content_objection": "",
        "verify_iterations": state.verify_iterations + (1 if result.missed else 0),
    })


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
        "content_problems": state.content_problems,  # 事实门超限残留（补不齐的交人拍板）
        # 改动落实门超限残留（2026-09-28）：聊定了但补到上限还没落实的改动，交人门知情拍板。
        "missed_changes": [f"{m.target}：应改为「{m.suggested}」" for m in state.missed_changes],
    })
    decision = answer.get("decision", "") if isinstance(answer, dict) else str(answer)
    feedback = answer.get("feedback", "") if isinstance(answer, dict) else ""
    logger.info("rewrite_human_gate_resumed", decision=decision, has_feedback=bool(feedback))
    return Command(update={"decision": decision, "feedback": feedback})
