"""简历识别图：定义并编译"抽事实"工作流。

职责：StateGraph 定义 + 节点挂载 + 编译 + 对外提供 parse。
红线：本层不碰 DB。当前为单节点直链  entry -> extract_facts -> END，
为后续加合规过滤、收录判定等节点留位（见 docs/design/resume.md §1）。
"""

import asyncio

from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.config import settings
from app.core.logging import logger
from app.nodes import extract_facts_node
from app.schemas.facts import ExtractedFacts, ParseCoverage
from app.schemas.resume_graph import ResumeParseState

_graph: CompiledStateGraph | None = None

# LangGraph RunnableConfig 的开放通道：自定义运行时参数（此处 cancel_event）放这里，
# 顶层裸字段会被 ensure_config 丢白名单外键。node 侧从 config["configurable"] 取。
_CANCEL_EVENT_CONFIG_KEY = "cancel_event"


def get_resume_parse_graph() -> CompiledStateGraph:
    """返回编译好的简历识别图（惰性单例）。"""
    global _graph
    if _graph is None:
        builder = StateGraph(ResumeParseState)
        builder.add_node("extract_facts", extract_facts_node)
        builder.set_entry_point("extract_facts")
        builder.set_finish_point("extract_facts")
        # **不带 checkpointer**（2026-09-29）：单节点直链图、无中断（人确认走聊天里的
        # ExtractCard，不经图 resume），结果直接返回调用方。旧实现挂了 checkpointer 且
        # thread_id 是静态的 `resume-parse:current` → 每次上传都往同一 thread 追加、永不清理
        # （同 analysis 图那个 1.5GB 膨胀的成因，只是量小）。**checkpointer 只给真正需要
        # 「中断 → 恢复」的图**（当前仅 rewrite：挂起等人门确认）。
        _graph = builder.compile(name=f"{settings.PROJECT_NAME} resume-parse")
        logger.info("graph_created", graph_name=_graph.name)
    return _graph


async def parse(resume_markdown: str, cancel_event: asyncio.Event | None = None) -> ExtractedFacts:
    """对简历 Markdown 跑一遍识别图，返回事实清单 + 覆盖率。

    cancel_event：客户端断开信号，经 config["configurable"] 传进节点。
    """
    graph = get_resume_parse_graph()
    state = ResumeParseState(resume_markdown=resume_markdown)
    # 无 checkpointer → 无 thread_id（图内存跑）。cancel_event 仍经 configurable 传进节点。
    config: RunnableConfig = (
        {"configurable": {_CANCEL_EVENT_CONFIG_KEY: cancel_event}} if cancel_event is not None else {}
    )
    result = await graph.ainvoke(state, config=config)
    return ExtractedFacts(
        facts=result["facts"],
        coverage=result.get("coverage", ParseCoverage()),
    )
