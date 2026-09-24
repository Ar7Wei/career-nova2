"""编辑流水线 agents：分类意图 + 内容优化。

- `run_classify_agent`：结构化输出用户意图（RewriteIntent）——判断改内容/改布局顺序/两者/都不是。
- `run_content_agent`：按用户请求在 resume JSON 上改内容（结构化输出）。
排版（layout）不是 LLM agent——固定模板渲染（render_resume 纯函数，见 §13bis + adr/0004）。

不知道图的存在，不碰 DB。结构化输出走 llm_service.call(response_format=...)。
"""

from langchain_core.messages import HumanMessage
from app.core.errors import EmptyOutputError
from app.core.logging import logger
from app.prompts import load_rewrite_classify_prompt, load_rewrite_content_prompt
from app.schemas.resume import Resume, resume_to_json
from app.schemas.rewrite import RewriteIntent
from app.services.llm import llm_service


async def run_classify_agent(resume_json: str, user_request: str) -> RewriteIntent:
    """识别用户对简历的编辑意图（content/layout/both/none）。"""
    prompt = load_rewrite_classify_prompt(resume_json, user_request)
    result = await llm_service.call(
        [HumanMessage(content=prompt)],
        response_format=RewriteIntent,
    )
    logger.info("rewrite_intent_classified", intent=result.text, has_layout=bool(result.layout_request))
    return result


async def run_content_agent(
    resume_json: str,
    user_request: str,
    facts: str = "",
    target_role: str = "",
    preferences: str = "",
) -> tuple[str, str]:
    """按用户请求在 resume JSON 上改内容，返回 (完整新版 JSON 字符串, 版本名)。

    结构化输出：LLM 直接吐 Resume，version 名走 resume.summary 字段。
    2026-09-01：facts = 资料集当前事实，暖态只补缺口（JSON 权威，facts 不覆盖）。
    2026-09-23：机器门回边已停，`facts_feedback` 参数随之删除（没有生产者了）。
    2026-09-07（ADR 0012 合并）：target_role/preferences = 侧重信号（service 读出注入，
    Node 不碰 DB），只指导内容侧重、不写进成品。
    改坏了（整份空）→ EmptyOutputError。
    """
    prompt = load_rewrite_content_prompt(resume_json, user_request, facts, target_role, preferences)
    resume: Resume = await llm_service.call([HumanMessage(content=prompt)], response_format=Resume)
    if not resume.basics.name and not resume.work and not resume.projects and not resume.skills and not resume.education:
        raise EmptyOutputError("模型没改出内容，换个说法再试或换模型")
    logger.info("rewrite_content_done", json_chars=len(resume_to_json(resume)), summary=resume.summary)
    return resume_to_json(resume), resume.summary
