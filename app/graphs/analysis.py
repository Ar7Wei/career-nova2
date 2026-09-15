"""分析 graph：定义并编译「投递页分析」工作流（2026-09-15 方向定稿：批次分析 graph 化）。

职责：StateGraph 定义 + 节点挂载 + 编译 + 对外提供 run_analysis。
红线：本层不碰 DB、不写库——取数/备料/落库由 service 注入节点（见 nodes/analysis.py）。

骨架（线性，节点即扩展点）：load → prepare → compute_stats → analyze → persist。
- 一个 graph 按 scope 分流：scope 是 state 字段，入口分流到对应取数/统计/分析。**batch（未处理）
  与 daily（已投递）都已接通**（2026-09-15）——batch 取本轮岗位、算岗位画像统计、产报告+处方；
  daily 取窗口内投递活动、算活动统计、产日报（无处方）。scope 分流体现在 load/compute_stats/
  analyze/persist 注入函数的选择上，图上保持线性（「半步可选」留节点里，不搬上图——照 ADR 0012）。
- 空批次/空窗口在各节点短路：不 analyze、不落库，state.headline 留空。
- 节点即扩展点：将来加「查市场/公司情报/人门」直接往图里插节点或把 analyze 换成 deep agent，
  骨架不动（这正是 graph 化的目的）。
"""

from collections.abc import Awaitable, Callable

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.config import settings
from app.core.logging import logger
from app.graphs.checkpoint import get_checkpointer, graph_config
from app.nodes import analysis as analysis_nodes
from app.nodes.analysis import (
    analyze_node,
    compute_stats_node,
    load_node,
    persist_node,
    prepare_node,
)
from app.schemas.analysis_graph import AnalysisState
from app.schemas.jobs import Job

_graph: CompiledStateGraph | None = None


def get_analysis_graph() -> CompiledStateGraph:
    """返回编译好的分析图（惰性单例）。线性骨架，节点即扩展点。"""
    global _graph
    if _graph is None:
        builder = StateGraph(AnalysisState)
        builder.add_node("load", load_node)
        builder.add_node("prepare", prepare_node)
        builder.add_node("compute_stats", compute_stats_node)
        builder.add_node("analyze", analyze_node)
        builder.add_node("persist", persist_node)
        builder.add_edge("__start__", "load")
        builder.add_edge("load", "prepare")
        builder.add_edge("prepare", "compute_stats")
        builder.add_edge("compute_stats", "analyze")
        builder.add_edge("analyze", "persist")
        builder.add_edge("persist", END)
        _graph = builder.compile(name=f"{settings.PROJECT_NAME} analysis", checkpointer=get_checkpointer())
        logger.info("graph_created", graph_name=_graph.name)
    return _graph


async def run_analysis(
    state: AnalysisState,
    *,
    load_jobs: Callable[[int], Awaitable[list[Job]]] | None = None,
    prepare_materials: Callable[[list[Job], str], Awaitable[dict[str, str]]] | None = None,
    persist: Callable[[AnalysisState], Awaitable[None]] | None = None,
    load_daily: Callable[[str], Awaitable[dict]] | None = None,
) -> AnalysisState:
    """跑一遍分析图，返回终态。

    取数/备料/落库经参数注入节点全局（service 装配真实现，测试注入 fake）——节点不 import
    service，守「graph 不碰 DB」红线。每次调用前覆盖装配，保证本次用的是本次的实现。
    """
    if load_jobs is not None:
        analysis_nodes.load_jobs_fn = load_jobs
    if prepare_materials is not None:
        analysis_nodes.prepare_materials_fn = prepare_materials
    if persist is not None:
        analysis_nodes.persist_fn = persist
    if load_daily is not None:
        analysis_nodes.load_daily_fn = load_daily
    graph = get_analysis_graph()
    result = await graph.ainvoke(state, config=graph_config("analysis", state.scope))
    return AnalysisState(**result)


def reset_analysis_graph_for_tests() -> None:
    """测试复位：清惰性单例 + 节点注入（避免测试间串实现）。"""
    global _graph
    _graph = None
    analysis_nodes.load_jobs_fn = None
    analysis_nodes.prepare_materials_fn = None
    analysis_nodes.persist_fn = None
    analysis_nodes.load_daily_fn = None
