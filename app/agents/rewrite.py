"""编辑流水线 agents：分类意图 + 内容优化。

- `run_classify_agent`：结构化输出用户意图（RewriteIntent）——判断改内容/改布局顺序/两者/都不是。
- `run_content_agent`：按用户请求在 resume JSON 上改内容（结构化输出）。
排版（layout）不是 LLM agent——固定模板渲染（render_resume 纯函数，见 §13bis + adr/0004）。

不知道图的存在，不碰 DB。结构化输出走 llm_service.call(response_format=...)。
"""

from langchain_core.messages import HumanMessage
from app.core.errors import EmptyOutputError
from app.core.logging import logger
from app.prompts import load_rewrite_classify_prompt, load_rewrite_content_prompt, load_rewrite_verify_prompt
from app.schemas.resume import resume_to_json
from app.schemas.rewrite import ContentEditResult, RewriteIntent, VerifyChangesResult
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
) -> tuple[str, str, str]:
    """按用户请求在 resume JSON 上改内容，返回 (完整新版 JSON 字符串, 版本名, 异议)。

    结构化输出：LLM 吐 ContentEditResult（resume 本体 + objection），版本名取 resume.summary。
    2026-09-01：facts = 资料集当前事实，暖态只补缺口（JSON 权威，facts 不覆盖）。
    2026-09-28：返回值加第三位 objection——落实门补改（request 里带「上次没落实」清单）时，
      content 若认为复核把「其实改了」的判成「没改」，在 objection 写一句异议，交回复核仲裁；
      其余情况恒为空串。
    2026-09-07（ADR 0012 合并）：target_role/preferences = 侧重信号（service 读出注入，
    Node 不碰 DB），只指导内容侧重、不写进成品。
    改坏了（整份空）→ EmptyOutputError。
    """
    prompt = load_rewrite_content_prompt(resume_json, user_request, facts, target_role, preferences)
    result: ContentEditResult = await llm_service.call([HumanMessage(content=prompt)], response_format=ContentEditResult)
    resume = result.resume
    if not resume.basics.name and not resume.work and not resume.projects and not resume.skills and not resume.education:
        raise EmptyOutputError("模型没改出内容，换个说法再试或换模型")
    logger.info("rewrite_content_done", json_chars=len(resume_to_json(resume)), summary=resume.summary, has_objection=bool(result.objection))
    return resume_to_json(resume), resume.summary, result.objection


async def run_verify_changes_agent(
    confirmed_changes: str,
    old_json: str,
    new_json: str,
    content_objection: str = "",
) -> VerifyChangesResult:
    """改动落实核对（2026-09-28 改动落实门）：逐条判 confirmed 改动在新稿里落实了没。

    这是「聊定了 → 出稿」的最后一道卡：confirmed 清单（用户拍板要改的）+ 旧稿 + 新稿，
    复核 LLM 逐条对「新稿有没有这条改动要的结果」。missed 非空 = 有遗漏，图据此回边
    content 补改（带上具体缺哪条）。判据是**语义级**的（看结果不看字面）——这正是它区别于
    已停用的 find_missing_facts（关键词子串、罚一切改写）的地方。

    content_objection：上一轮 content 对复核判定的异议（「这条其实改了/不用改」）。复核要
    重新看一眼——异议成立的（确实是误判）就把那条从 missed 撤掉，不成立才保留。这让
    「复核误判」有机会在图内被纠正，而不是把 content 按头反复改。
    """
    prompt = load_rewrite_verify_prompt(confirmed_changes, old_json, new_json, content_objection)
    result: VerifyChangesResult = await llm_service.call([HumanMessage(content=prompt)], response_format=VerifyChangesResult)
    logger.info("rewrite_changes_verified", missed=len(result.missed), had_objection=bool(content_objection))
    return result
