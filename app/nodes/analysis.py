"""分析 graph 节点：load → prepare → compute_stats → analyze → persist。

红线：节点不碰 DB、不做重活——取数（load_jobs）、素材准备（prepare_materials，含 job51
逐岗 JD 提炼这种逐岗 LLM）、落库（persist）都由 **service 注入**（照 rewrite 的
generate_json_from_facts 先例），节点只调 agent 或确定性纯函数（compute_stats/render_stats_summary）。
analyze 走 `run_batch_analysis_agent`（带工具位的 Agent，本版克制）。
"""

from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph.state import Command

from app.agents.analysis import run_batch_analysis_agent, run_daily_analysis_agent
from app.core.logging import logger
from app.schemas.analysis_graph import AnalysisState
from app.schemas.jobs import Job
from app.services.analysis_daily_stats import compute_daily_stats, render_daily_stats_summary
from app.services.analysis_stats import compute_stats, render_stats_summary

# service 装配时注入（避免 nodes import services 循环）。未注入即被调到属装配 bug。
# prepare_materials(jobs, scope) -> {"facts","round_summary","tone_note"}：喂 analyze 的素材，
# 含 job51 逐岗 JD 提炼（逐岗 LLM，重活留在 service）。
# load_daily(scope_key) -> {"events","as_of","window_days"}：daily 活动素材（service 从 repo 取好）。
load_jobs_fn: Callable[[int], Awaitable[list[Job]]] | None = None
prepare_materials_fn: Callable[[list[Job], str], Awaitable[dict[str, str]]] | None = None
persist_fn: Callable[[AnalysisState], Awaitable[None]] | None = None
load_daily_fn: Callable[[str], Awaitable[dict]] | None = None


async def load_node(state: AnalysisState) -> Command:
    """取数（按 scope 分流）：batch 按 round_id 取岗位；daily 取窗口内投递活动。

    两条路径取的都是「待分析素材」（batch=岗位列表 / daily=事件+岗位对），由 service 注入。
    """
    if state.scope == "daily":
        if load_daily_fn is None:
            raise RuntimeError("load_node 的 load_daily_fn 未注入（service 装配遗漏）")
        material = await load_daily_fn(state.scope_key)
        logger.info("analysis_loaded", scope=state.scope, count=len(material.get("events", [])))
        return Command(update=material)
    if load_jobs_fn is None:
        raise RuntimeError("load_node 的 load_jobs_fn 未注入（service 装配遗漏）")
    jobs = await load_jobs_fn(state.round_id) if state.round_id is not None else []
    logger.info("analysis_loaded", scope=state.scope, count=len(jobs))
    return Command(update={"jobs": jobs})


async def prepare_node(state: AnalysisState) -> Command:
    """备料：facts + 岗位摘要（含 job51 JD 提炼）+ 口径注。空批次短路（不调 service）。"""
    if not state.jobs:
        return Command(update={})
    if prepare_materials_fn is None:
        raise RuntimeError("prepare_node 的 prepare_materials_fn 未注入（service 装配遗漏）")
    materials = await prepare_materials_fn(state.jobs, state.scope)
    return Command(update=materials)


async def compute_stats_node(state: AnalysisState) -> Command:
    """前置统计（确定性代码）：batch → 岗位画像 StatsPack；daily → 投递活动 DailyStatsPack。"""
    if state.scope == "daily":
        if state.daily_events is None:
            return Command(update={})
        daily_stats = compute_daily_stats(state.daily_events, as_of=state.daily_as_of, window_days=state.daily_window_days)
        return Command(update={"daily_stats": daily_stats})
    if not state.jobs:
        return Command(update={})
    batch_stats = compute_stats(state.jobs)
    return Command(update={"stats": batch_stats})


async def analyze_node(state: AnalysisState) -> Command:
    """Analyze Agent（按 scope 分流）：batch → 报告+处方；daily → 日报（无处方）。空输入短路。"""
    if state.scope == "daily":
        if state.daily_stats is None or state.daily_stats.total_events == 0:
            return Command(update={})
        daily_result = await run_daily_analysis_agent(
            summary=state.round_summary,
            stats_summary=render_daily_stats_summary(state.daily_stats),
            tone_note=state.tone_note,
        )
        daily_charts = sum(1 for b in daily_result.blocks if b.kind == "chart")
        logger.info("analysis_analyzed", scope="daily", charts=daily_charts)
        return Command(update={"headline": daily_result.headline, "blocks": daily_result.blocks})

    if not state.jobs or state.stats is None:
        return Command(update={})
    batch_result = await run_batch_analysis_agent(
        facts=state.facts,
        round_summary=state.round_summary,
        tone_note=state.tone_note,
        stats_summary=render_stats_summary(state.stats),
    )
    meta: dict[str, Any] = {
        "total": state.stats.total,
        "liepin": state.stats.liepin,
        "job51": state.stats.job51,
        "with_jd": state.stats.with_jd,
    }
    batch_charts = sum(1 for b in batch_result.blocks if b.kind == "chart")
    logger.info("analysis_analyzed", scope="batch", charts=batch_charts, prescriptions=len(batch_result.prescriptions))
    return Command(
        update={
            "headline": batch_result.headline,
            "blocks": batch_result.blocks,
            "prescriptions": [p.model_dump() for p in batch_result.prescriptions],
            "meta": meta,
        }
    )


async def persist_node(state: AnalysisState) -> Command:
    """落库（service 注入）：batch/daily 都走 persist（报告 + 块流）。空输入短路（不写空报告）。"""
    if state.scope == "daily":
        empty = state.daily_stats is None or state.daily_stats.total_events == 0
    else:
        empty = not state.jobs
    if empty:
        return Command(update={})
    if persist_fn is None:
        raise RuntimeError("persist_node 的 persist_fn 未注入（service 装配遗漏）")
    await persist_fn(state)
    return Command(update={})
