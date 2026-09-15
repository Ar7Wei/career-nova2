"""简历识别节点：图中的抽事实步骤，调 extract_facts agent。

职责单一：把 state 里的简历 Markdown 交给 agent 抽事实，把结果写回 state，走向 END。
不直接 import prompts 或 llm_service（那是 agent 的事），不碰 DB。
"""

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.graph.state import Command

from app.agents.extract_facts import run_extract_facts_agent
from app.core.logging import logger
from app.schemas.resume_graph import ResumeParseState

# 与 graphs/resume_parse.py 的 configurable 键对齐（取 cancel_event 传给 agent）。
_CONFIG_KEY = "cancel_event"


async def extract_facts_node(state: ResumeParseState, config: RunnableConfig) -> Command:
    """对简历 Markdown 抽事实并结束。cancel_event 从 config["configurable"] 取出透传。"""
    cancel_event = (config or {}).get("configurable", {}).get(_CONFIG_KEY)
    result = await run_extract_facts_agent(state.resume_markdown, cancel_event=cancel_event)
    logger.info("extract_facts_node_done", fact_count=len(result.facts))
    return Command(update={"facts": result.facts, "coverage": result.coverage}, goto=END)
