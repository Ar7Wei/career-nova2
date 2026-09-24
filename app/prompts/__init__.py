"""Prompt 层：集中管理所有 prompt，只被 agents 层引用（红线：不散落各处）。

模板在模块加载时读一次，不做 per-request 文件 IO。
"""

import os
from datetime import datetime

from app.core.config import settings

_PROMPTS_DIR = os.path.dirname(__file__)

with open(os.path.join(_PROMPTS_DIR, "extract_facts.md"), encoding="utf-8") as _f:
    _EXTRACT_FACTS_TEMPLATE = _f.read()

with open(os.path.join(_PROMPTS_DIR, "resume_agent_deep.md"), encoding="utf-8") as _f:
    _RESUME_AGENT_DEEP_TEMPLATE = _f.read()

with open(os.path.join(_PROMPTS_DIR, "generate_resume.md"), encoding="utf-8") as _f:
    _GENERATE_RESUME_TEMPLATE = _f.read()

with open(os.path.join(_PROMPTS_DIR, "rewrite_classify.md"), encoding="utf-8") as _f:
    _REWRITE_CLASSIFY_TEMPLATE = _f.read()

with open(os.path.join(_PROMPTS_DIR, "rewrite_content.md"), encoding="utf-8") as _f:
    _REWRITE_CONTENT_TEMPLATE = _f.read()

with open(os.path.join(_PROMPTS_DIR, "refine_direction.md"), encoding="utf-8") as _f:
    _REFINE_DIRECTION_TEMPLATE = _f.read()

with open(os.path.join(_PROMPTS_DIR, "opening.md"), encoding="utf-8") as _f:
    _OPENING_TEMPLATE = _f.read()

with open(os.path.join(_PROMPTS_DIR, "preview_pending.md"), encoding="utf-8") as _f:
    _PREVIEW_PENDING_HINT = _f.read().strip()

# 投递页分析（apply.md §11.7，2026-09-14）：批次总览出处方 / 逐岗 JD 提炼 / 已投递日报 /
# 单场面试准备。
with open(os.path.join(_PROMPTS_DIR, "analysis_batch.md"), encoding="utf-8") as _f:
    _ANALYSIS_BATCH_TEMPLATE = _f.read()

with open(os.path.join(_PROMPTS_DIR, "analysis_distill_jd.md"), encoding="utf-8") as _f:
    _ANALYSIS_DISTILL_JD_TEMPLATE = _f.read()

with open(os.path.join(_PROMPTS_DIR, "analysis_daily.md"), encoding="utf-8") as _f:
    _ANALYSIS_DAILY_TEMPLATE = _f.read()

with open(os.path.join(_PROMPTS_DIR, "analysis_interview.md"), encoding="utf-8") as _f:
    _ANALYSIS_INTERVIEW_TEMPLATE = _f.read()


def _language_instruction() -> str:
    """按当前语言生成「回答语言」指令，注入 system prompt。"""
    if settings.APP_LANGUAGE == "en":
        return "Always respond in English, regardless of the user's language."
    return "始终用中文回答，无论用户用什么语言提问。"


def _current_date_line() -> str:
    """当前日期行（2026-09-21）：注入吃事实/时间的 prompt，让非对话 agent 能换算「至今」、判时长。

    对话 agent（deep）每轮重建 prompt 已带精确时间戳（load_resume_agent_deep_prompt），且另有
    calc_timeline 工具做灵活计算；这里给一次性 prompt（生成/改写/分析/开场）补一个日期锚点。
    """
    return f"今天是 {datetime.now().strftime('%Y-%m-%d')}。看到「至今」按今天换算实际时长；看到时间段留意距今多久、相邻经历间有无空窗/重叠。"


def load_extract_facts_prompt(resume_markdown: str) -> str:
    """加载简历抽事实的 prompt（简历正文 + 覆盖率要求）。"""
    return _EXTRACT_FACTS_TEMPLATE.format(
        resume_markdown=resume_markdown,
        language_instruction=_language_instruction(),
    )


def load_resume_agent_deep_prompt(
    *,
    facts: str,
    preferences: str,
    resume_document: str,
    change_records: str,
    info_gaps: str = "",
) -> str:
    """加载 deep agent 的瘦身版 system prompt（前台接待员定位 + 按需查技能）。

    与 legacy 版区别：去掉了已拆进技能的四块（grill/采集/优化/方向）的全文，改为
    「场景技法」段指向技能清单，按需 read_file 读全文（渐进披露省 token）。
    change_records = 当前改动记录面板（原因 + 子改动点，含各自状态）。
    """
    return _RESUME_AGENT_DEEP_TEMPLATE.format(
        agent_name=settings.PROJECT_NAME,
        facts=facts or "（暂无已收录事实）",
        preferences=preferences or "（暂无持久决策）",
        resume_document=resume_document or "（暂无简历，通过聊天了解用户）",
        change_records=change_records or "（暂无改动记录）",
        info_gaps=info_gaps,
        current_date_and_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        language_instruction=_language_instruction(),
    )


def load_generate_resume_prompt(
    target_role: str,
    facts: str,
    preferences: str = "",
    instruction: str = "",
) -> str:
    """加载整份生成 prompt（文档任务，非对话）。

    2026-08-28 结构化转向（docs/adr/0004）：产出 = 结构化 resume JSON（schema 见
    app/schemas/resume.py，JSON-Resume 词表 + layout），不是自由 Markdown。
    页数不再由闭环收敛——由 scale 阶梯 + A4 预览人工调。
    2026-09-01：instruction = 冷启动「开始改/改简历」注入的改进要求（生成时一并满足）；
    纯生成（无指令）时留空。
    """
    return _GENERATE_RESUME_TEMPLATE.format(
        target_role=target_role or "（无指定目标岗位，生成通用版）",
        preferences=preferences or "（无持久决策）",
        instruction=instruction or "（无）",
        facts=facts or "（没有可用事实，请询问用户补充）",
        current_date=_current_date_line(),
        language_instruction=_language_instruction(),
    )


def load_rewrite_classify_prompt(resume_json: str, user_request: str) -> str:
    """加载编辑意图识别 prompt（编辑流水线 classify 节点）。"""
    return _REWRITE_CLASSIFY_TEMPLATE.format(
        resume_markdown=resume_json or "（暂无简历）",
        user_request=user_request,
        language_instruction=_language_instruction(),
    )


def load_rewrite_content_prompt(
    resume_json: str,
    user_request: str,
    facts: str = "",
    target_role: str = "",
    preferences: str = "",
) -> str:
    """加载内容优化 prompt（编辑流水线 content 节点）。

    2026-09-01：facts = 资料集当前事实，暖态编辑只补缺口（JSON 权威，facts 不覆盖）；
    无事实时留空。
    2026-09-23：机器门回边已停，`facts_feedback` 参数与模板里的「硬性补回」段一并删除
    （判据太糙、罚一切改写，见 graphs/rewrite.py 的 MAX_ITERATIONS 注释）。
    2026-09-07（ADR 0012 合并）：target_role/preferences = 侧重信号，生成改写都喂
    （只指导内容侧重、不写进成品）。
    """
    return _REWRITE_CONTENT_TEMPLATE.format(
        resume_markdown=resume_json,
        user_request=user_request,
        facts=facts or "（暂无已收录事实）",
        target_role=target_role or "（无指定目标岗位，不做侧重）",
        preferences=preferences or "（无持久决策）",
        current_date=_current_date_line(),
        language_instruction=_language_instruction(),
    )


def load_refine_direction_prompt(facts: str, current_direction: str = "") -> str:
    """加载方向提炼 prompt（投递·方向，apply.md §11.2）。

    facts：注入的 active facts 文本（含目标岗位/工作/技能）。
    current_direction：当前方向的人话描述（空 = 首次定方向）。
    """
    return _REFINE_DIRECTION_TEMPLATE.format(
        facts=facts or "（暂无已收录事实）",
        current_direction=current_direction or "（空，首次定方向）",
        language_instruction=_language_instruction(),
    )


def load_opening_prompt(
    *,
    stage_hint: str,
    facts: str,
    resume_document: str,
    info_gaps: str,
    recent_changes: str,
) -> str:
    """加载分阶段开场引导 prompt（§11.8，一次性，非多轮对话）。

    stage_hint：阶段 + 该阶段该说的重点。recent_changes：刚发生动作/这版改动（applied 建议）。
    """
    return _OPENING_TEMPLATE.format(
        stage_hint=stage_hint,
        facts=facts or "（暂无已收录事实）",
        resume_document=resume_document or "（暂无简历）",
        info_gaps=info_gaps or "（关键信息齐全）",
        recent_changes=recent_changes or "（无）",
        current_date=_current_date_line(),
        language_instruction=_language_instruction(),
    )


def preview_pending_hint() -> str:
    """待确认生成预览的 system prompt 提示段（有暂存预览时追加，引导用户去确认/重生成）。"""
    return _PREVIEW_PENDING_HINT


# ---------------------------------------------------------------------------
# 投递页分析（apply.md §11.7，2026-09-14）
# ---------------------------------------------------------------------------


def load_analysis_batch_prompt(*, facts: str, round_summary: str, tone_note: str = "", stats_summary: str = "") -> str:
    """加载「未处理」批次分析 prompt（本轮那批岗位 → 总览报告 + 处方 + 可选配图）。

    round_summary：本批岗位的结构化摘要（job51 有 JD 的走提炼、猎聘无 JD 的走标签聚合）。
    stats_summary：compute_stats 算出的确定性统计文本——analyze Agent 配图只能从这里面取数。
    """
    return _ANALYSIS_BATCH_TEMPLATE.format(
        facts=facts or "（暂无已收录事实）",
        round_summary=round_summary,
        tone_note=tone_note,
        stats_summary=stats_summary or "（暂无统计）",
        current_date=_current_date_line(),
        language_instruction=_language_instruction(),
    )


def load_analysis_distill_jd_prompt(jd: str) -> str:
    """加载逐岗 JD 提炼 prompt（job51 有 JD 的那批逐条过）。"""
    return _ANALYSIS_DISTILL_JD_TEMPLATE.format(
        jd=jd.strip() or "（JD 正文为空）",
        language_instruction=_language_instruction(),
    )


def load_analysis_daily_prompt(summary: str, stats_summary: str = "", tone_note: str = "") -> str:
    """加载「已投递」投递日报 prompt（截至昨天，纯嘱咐 + 可选配图）。"""
    return _ANALYSIS_DAILY_TEMPLATE.format(
        summary=summary,
        stats_summary=stats_summary,
        tone_note=tone_note,
        current_date=_current_date_line(),
        language_instruction=_language_instruction(),
    )


def load_analysis_interview_prompt(*, company: str, title: str, scheduled_at: str, jd: str, facts: str) -> str:
    """加载单场面试准备 prompt（JD + 用户材料 → tips）。"""
    return _ANALYSIS_INTERVIEW_TEMPLATE.format(
        company=company or "（未知）",
        title=title or "（未知）",
        scheduled_at=scheduled_at or "（未定）",
        jd=jd.strip() or "（该岗位无 JD——平台不提供）",
        facts=facts or "（暂无已收录事实）",
        language_instruction=_language_instruction(),
    )
