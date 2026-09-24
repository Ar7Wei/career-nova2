"""编辑流水线图：定义并编译「改简历」工作流。

职责：StateGraph 定义 + 节点挂载 + 编译 + 对外提供 run_rewrite。
红线：本层不碰 DB、不写库——编排（含校验重试）由 services/rewrite.py 负责，
graph 只做「分类 → 文字 → 内容机器门 ⇄ 排版 → 校验」的节点串联（§13bis 先文字后排版）。

图结构（2026-09-07，ADR 0012 机器自愈门）：
- 骨架：classify → content ⇄ validate_content（机器门回边）→ layout → validate_html。
- 唯一的真路径分叉是 none：与改简历无关 → classify 后直接短路 END，不跑 content/layout
  的无意义准备。这个分叉用条件边表达在图上；content/layout 内部的 intent 分派（改不改
  文字、surgical 还是 recompose）是**节点职责**，留在节点里，不搬上图（否则为一个"两步里
  半步可选"的结构引入更多样板，图反而难读）。
- 机器门：validate_content 发现「漏事实」且未超 MAX_ITERATIONS → 回边 content 带缺失清单
  补回；干净或超限 → 往下走 layout。超限放行 + content_problems 留痕（service 落库）。
- 意图 none 的产出为空（new_markdown/new_html 保持默认空），service 凭 intent.text 抛
  ConflictError，行为与短路前一致。
"""

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.core.config import settings
from app.core.logging import logger
from app.graphs.checkpoint import get_checkpointer, graph_config, thread_id
from app.nodes.rewrite import (
    classify_node,
    cold_start_node,
    content_node,
    human_gate_node,
    layout_node,
    validate_content_node,
    validate_node,
)
from app.schemas.rewrite import RewriteIntent, RewriteState

# 机器门暂停（2026-09-23）：validate_content 仍跑、仍留痕，但**不再回边**。
# 判据（find_missing_facts：facts_text 的 token 是否在成品全文出现）太糙——
# 它惩罚一切改写（美化/重命名/并组都会让它报），2026-09-23 当天 7 轮出稿里
# 21 次校验只有 3 次干净放行、4 次打满 3 轮。判据待重做（按条目/按硬字段），
# 这之前「改写丢内容」靠 rewrite_content.md 的「只动用户请求指向的内容」自律 +
# 人门肉眼复核兜。详见 nodes/rewrite.py:validate_content_node。
MAX_ITERATIONS = 3

_graph: CompiledStateGraph | None = None


def _route_after_classify(state: RewriteState) -> str:
    """Classify 后的唯一分叉：none 短路 END，其余进 content 线性主干。"""
    return END if state.intent.text == "none" else "content"


def _route_entry(state: RewriteState) -> str:
    """图入口冷/暖分叉（ADR 0012 合并）。

    无 current_json（上传原件 / 零文档）→ 冷启动 facts 生成；有 → 暖态 classify。
    生成与改写同一条流水线，入口判冷暖。
    """
    return "cold_start" if not state.current_json.strip() else "classify"


def _route_after_validate_content(state: RewriteState) -> str:
    """机器门路由：**恒走 layout**（2026-09-23 起不回边）。

    回边（content_problems → content 重改）已停——判据太糙、罚一切改写，见 MAX_ITERATIONS
    上方注释。保留本函数与 content_problems 的产出是为了留痕（人门 payload / 日志），
    判据重做后在这里恢复回边即可。
    """
    return "layout"


def _route_after_human_gate(state: RewriteState) -> str:
    """人门路由：confirm → END（service 落库）；revise → 带 feedback 回 content 重改。"""
    return "content" if state.decision == "revise" else END


def get_rewrite_graph() -> CompiledStateGraph:
    """返回编译好的编辑流水线图（惰性单例）。"""
    global _graph
    if _graph is None:
        builder = StateGraph(RewriteState)
        builder.add_node("classify", classify_node)
        builder.add_node("cold_start", cold_start_node)
        builder.add_node("content", content_node)
        builder.add_node("validate_content", validate_content_node)
        builder.add_node("layout", layout_node)
        builder.add_node("validate", validate_node)
        builder.add_node("human_gate", human_gate_node)
        # 冷/暖入口（ADR 0012 合并）：无 current_json → 冷启动 facts 生成；有 → 暖态 classify。
        builder.add_conditional_edges(
            "__start__",
            _route_entry,
            {"cold_start": "cold_start", "classify": "classify"},
        )
        # 冷启动生成的内容也要过机器门（覆盖校验）——与暖态 content 汇合进 validate_content。
        builder.add_edge("cold_start", "validate_content")
        builder.add_conditional_edges(
            "classify",
            _route_after_classify,
            {END: END, "content": "content"},
        )
        builder.add_edge("content", "validate_content")
        builder.add_conditional_edges(
            "validate_content",
            _route_after_validate_content,
            {"content": "content", "layout": "layout"},
        )
        builder.add_edge("layout", "validate")
        # 人门（ADR 0012 第 3 块）：validate → human_gate 挂起等人拍板；
        # confirm → END（service 落库），revise → 回 content 带 feedback 重改。
        builder.add_edge("validate", "human_gate")
        builder.add_conditional_edges(
            "human_gate",
            _route_after_human_gate,
            {END: END, "content": "content"},
        )
        # 统一持久化图基建（ADR 0012）：编译时注入 checkpointer（未装配时为 None，图内存跑）。
        _graph = builder.compile(name=f"{settings.PROJECT_NAME} rewrite", checkpointer=get_checkpointer())
        logger.info("graph_created", graph_name=_graph.name)
    return _graph


async def run_rewrite(state: RewriteState) -> RewriteState:
    """对一组输入跑一遍编辑流水线，返回产出状态（含 intent / new_markdown / new_html / problems）。"""
    graph = get_rewrite_graph()
    result = await graph.ainvoke(state, config=graph_config("rewrite"))
    return RewriteState(**result)


# --- 人门挂起态原语（ADR 0012 第 3 块）---
# preview 跑到人门挂起、confirm 用 Command(resume=) 恢复；挂起草稿经 checkpointer 持久化，
# 预览/重启恢复都按派生 thread_id 从 checkpointer 查回（不落业务库、不叠内存单例）。


async def run_preview(state: RewriteState) -> None:
    """跑流水线到人门挂起（产出经 checkpointer 落挂起态，service 另读）。无 checkpointer 时图跑穿到 END。"""
    graph = get_rewrite_graph()
    await graph.ainvoke(state, config=graph_config("rewrite"))


async def read_draft() -> RewriteState | None:
    """按派生 thread_id 从 checkpointer 查回当前挂起的草稿；无挂起（跑完/无 checkpointer）→ None。"""
    if get_checkpointer() is None:
        return None  # 无 checkpointer（:memory: 测试/未装配）→ 无挂起态可查
    graph = get_rewrite_graph()
    snap = await graph.aget_state(graph_config("rewrite"))
    if not snap.next:  # next 空 = 图已跑完（或无这条 thread），无挂起草稿
        return None
    return RewriteState(**snap.values)


async def peek_intent() -> str | None:
    """从 checkpointer 读最近一条 thread 的分类意图（none 短路探针）。

    none 意图经 _route_after_classify 短路 END、图跑完无挂起 → read_draft() 返回 None，
    service 无从区分「none 短路」与「没跑到人门的其他情况」。本原语读 checkpoint 最新值
    （无视 next 是否为空），让 service 在 None 守卫之前先把 none 分流出去。
    无 checkpointer（内存跑）→ None（探不到）。
    """
    if get_checkpointer() is None:
        return None
    graph = get_rewrite_graph()
    snap = await graph.aget_state(graph_config("rewrite"))
    intent = snap.values.get("intent")
    return intent.text if isinstance(intent, RewriteIntent) else None


async def resume_gate(decision: str, feedback: str = "") -> RewriteState:
    """人门拍板恢复：`Command(resume=)` 把 decision/feedback 喂回 interrupt，图继续跑。

    confirm → human_gate 路由 END，图跑完；revise → 带 feedback 回 content 重改、再挂人门。
    返回恢复后的 state（confirm 时即最终态，含 new_json/new_html/summary 供落库）。
    """
    graph = get_rewrite_graph()
    result = await graph.ainvoke(Command(resume={"decision": decision, "feedback": feedback}), config=graph_config("rewrite"))
    return RewriteState(**result)


async def clear_draft() -> None:
    """清掉当前 thread 的全部 checkpoint（confirm 落库后清草稿，让下一轮 preview 从干净 thread 起）。"""
    checkpointer = get_checkpointer()
    if checkpointer is None:
        return
    await checkpointer.adelete_thread(thread_id("rewrite"))
