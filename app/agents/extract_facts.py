"""抽事实 Agent：把简历 Markdown 交给 LLM，按基本分类抽取出用户信息事实清单。

职责：组 prompt（含简历正文 + 覆盖率要求）→ 经 llm_service 结构化输出 → 返回事实列表。
不知道图的存在，不碰 DB。结构化输出走 llm_service.call(response_format=...)。
"""

import asyncio

from langchain_core.messages import HumanMessage
from app.core.logging import logger
from app.prompts import load_extract_facts_prompt
from app.schemas.facts import ExtractedFacts
from app.services.llm import llm_service


async def run_extract_facts_agent(resume_markdown: str, cancel_event: asyncio.Event | None = None) -> ExtractedFacts:
    """对简历 Markdown 抽取用户信息事实（含覆盖率自述）。

    cancel_event：客户端断开信号，透传给 llm_service 用于中途取消 LLM 调用。
    """
    prompt = load_extract_facts_prompt(resume_markdown)
    result = await llm_service.call(
        [HumanMessage(content=prompt)],
        response_format=ExtractedFacts,
        cancel_event=cancel_event,
    )
    logger.info("facts_extracted", fact_count=len(result.facts))
    return result
