"""简历识别图：定义并编译"抽事实"工作流。

职责：StateGraph 定义 + 节点挂载 + 编译 + 对外提供 parse。
红线：本层不碰 DB。当前为单节点直链  entry -> extract_facts -> END，
为后续加合规过滤、收录判定等节点留位（见 docs/design/resume.md §1）。
"""

import asyncio

from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.config import settings
from app.core.logging import logger
from app.graphs.checkpoint import get_checkpointer, graph_config
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
        # 统一持久化图基建（ADR 0012）：编译时注入 checkpointer（未装配时为 None，图内存跑）。
        _graph = builder.compile(name=f"{settings.PROJECT_NAME} resume-parse", checkpointer=get_checkpointer())
        logger.info("graph_created", graph_name=_graph.name)
    return _graph


async def parse(resume_markdown: str, cancel_event: asyncio.Event | None = None) -> ExtractedFacts:
    """对简历 Markdown 跑一遍识别图，返回事实清单 + 覆盖率。

    cancel_event：客户端断开信号，经 config["configurable"] 传进节点。
    """
    graph = get_resume_parse_graph()
    state = ResumeParseState(resume_markdown=resume_markdown)
    config = graph_config("resume-parse")
    if cancel_event is not None:
        config["configurable"][_CANCEL_EVENT_CONFIG_KEY] = cancel_event
    result = await graph.ainvoke(state, config=config)
    return ExtractedFacts(
        facts=result["facts"],
        coverage=result.get("coverage", ParseCoverage()),
    )
