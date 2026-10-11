"""投递页分析 agents：analyze（综合叙事 + 自由配图）。

`run_batch_analysis_agent` 是分析 graph 里 analyze 节点的内部实现（2026-09-15 方向定稿）。
形态 = **带工具位的 Agent**：本版工具集克制（不挂重工具），行为退化为「一次结构化调用」，
但接口留好「挂工具升级 deep」的门——将来要「边分析边查市场/查公司情报」时挂工具即可，
节点本身不动（进可攻退可守）。

不知道图的存在，不碰 DB。结构化输出走 llm_service.call(response_format=...)。
"""

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.core.errors import EmptyOutputError
from app.core.logging import logger
from app.prompts import load_analysis_batch_prompt, load_analysis_daily_prompt
from app.schemas.report_block import ReportBlock, TextBlock
from app.services.llm import llm_service


def _has_text(blocks: list[ReportBlock]) -> bool:
    """块流里有没有实际文字（section 标题不算——只有标题没正文等于空报告）。"""
    return any(isinstance(b, TextBlock) and b.md.strip() for b in blocks)


class ProposalOut(BaseModel):
    """投递提案（ADR 0023）：分析出的**可执行**动作，非报告正文。

    - drop_job_ids：从本轮岗位里挑出的偏岗 id（如「成都·销售」这类跟方向不符的）。
      **必须是上方「本批岗位」里列出的 id**（越界 id 会被 service 丢）；理由写进正文「提案」段。
    - refine_hint：检索词诊断——哪组词捞歪了、该往哪收（如「『AI』那组捞进一堆传统信息化岗，
      建议改成『大模型应用』」）。空 = 检索词无需动。会喂给 refine 重新提炼方向供人拍板。
    """

    drop_job_ids: list[int] = Field(default_factory=list)
    refine_hint: str = ""


class BatchAnalysisOut(BaseModel):
    """未处理批次分析的 Agent 产出：总览报告（块流）+ 投递提案。

    2026-10-11（ADR 0023）**砍掉 prescriptions**：投递分析在上游、只分析爬来的岗位供给，
    不再产出「改简历」的处方（技能缺口类处方无数据支撑，会一路污染进简历）。改进建议仍由
    聊天 agent 的 `suggest_improvements` 产出，不受影响。批次分析出「报告」+「投递提案」。

    blocks = 报告正文的**有序块流**（text / chart 穿插）：Agent 按叙述顺序产出，图自然
    嵌在段落之间。图表 0~4 张、宁缺勿滥：从给定统计里挑数据配，只许 6 种图型、series ≤ 2、
    data 必须来自给定统计。不配 = 全 text 块（前端纯文本渲染，不勉强）。
    proposal = 可执行载体（剔除偏岗 id + 检索词诊断），**与报告分开**——报告照读、提案照点。
    """

    headline: str = ""
    blocks: list[ReportBlock] = Field(default_factory=list)
    proposal: ProposalOut = Field(default_factory=ProposalOut)


async def run_batch_analysis_agent(
    *,
    facts: str,
    round_summary: str,
    tone_note: str,
    stats_summary: str,
    jobs_index: str = "",
) -> BatchAnalysisOut:
    """综合一批岗位：前置统计 + 岗位素材 + 岗位 id 索引 → 总览报告 + 投递提案 + 可选配图。

    素材由调用方（node）备好：stats_summary = compute_stats 的统计文本、round_summary =
    岗位摘要、jobs_index = 「id | 标题」索引（Agent 据此填 proposal.drop_job_ids）。坏答案
    （整份空）→ EmptyOutputError。
    """
    prompt = load_analysis_batch_prompt(
        facts=facts,
        round_summary=round_summary,
        tone_note=tone_note,
        stats_summary=stats_summary,
        jobs_index=jobs_index,
    )
    result = await llm_service.call([HumanMessage(content=prompt)], response_format=BatchAnalysisOut)
    if not (result.headline.strip() or _has_text(result.blocks)):
        raise EmptyOutputError("批次分析产出空报告")
    charts = sum(1 for b in result.blocks if b.kind == "chart")
    logger.info("analysis_batch_agent_done", charts=charts, drop_ids=len(result.proposal.drop_job_ids))
    return result


class DailyAnalysisOut(BaseModel):
    """已投递日报的 Agent 产出：块流正文 + **可选配图**。

    日报是回望和洞察，不是简历改动清单。blocks 同 batch——按叙述顺序产出的 text/chart 块
    （图表 0~4 张、宁缺勿滥）。
    """

    headline: str = ""
    blocks: list[ReportBlock] = Field(default_factory=list)


async def run_daily_analysis_agent(
    *,
    summary: str,
    stats_summary: str,
    tone_note: str,
) -> DailyAnalysisOut:
    """写一份投递日报：投递活动素材 + 前置统计 → 块流正文（无处方）。

    素材由调用方（node）备好：stats_summary = compute_daily_stats 的统计文本、summary =
    近期投递活动摘要。坏答案（整份空）→ EmptyOutputError。
    """
    prompt = load_analysis_daily_prompt(summary=summary, stats_summary=stats_summary, tone_note=tone_note)
    result = await llm_service.call([HumanMessage(content=prompt)], response_format=DailyAnalysisOut)
    if not (result.headline.strip() or _has_text(result.blocks)):
        raise EmptyOutputError("投递日报产出空报告")
    charts = sum(1 for b in result.blocks if b.kind == "chart")
    logger.info("analysis_daily_agent_done", charts=charts)
    return result
