"""投递页分析报告传输模型（apply.md §11.7，2026-09-14）。

报告 = 分析产出的**嘱咐**载体（叙事性结论，无状态、无处收录）。

2026-10-11（ADR 0023）：投递分析重新定位为「供给分析」，批次分析**不再产出处方**——
原 `suggestions`（改动记录里 origin=job_analysis 的待收录处方）字段一并删除。改进建议仍由
聊天 agent 的 `suggest_improvements` 出、走优化点面板，与本接口无关。

「投递提案」（ADR 0023）补上：分析在报告之外，另产**两个可执行载体**——① 剔除偏岗
（`drop_jobs`，落到跟进状态线 `not_pursuing`）② 收紧检索词（`refine_hint`，喂 refine 出提案、
预填方向面板、人工拍板）。**分析产提案、人拍板才执行**（与 `dismiss` / `dispose-unprocessed`
同构）；报告本身仍是嘱咐（无状态）。

三 scope（§11.7.2）：batch 未处理批次 / daily 已投递日报 / interview 单场面试。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.report_block import ReportBlock

# 分析 scope（一张表三种报告，§11.7.5）
AnalysisScope = Literal["batch", "daily", "interview"]
# 报告就绪态（§11.7.5「展开不算、显示分析中」）：ready=有缓存 / computing=正在算 /
# missing=没算过且本轮也没东西可算（如本轮无岗位）。
AnalysisStatus = Literal["ready", "computing", "missing"]
# 投递提案的种类与生命周期（2026-10-11）：报告是缓存、内容不变，提案的处置单存一张表
# （`analysis_proposal_state`）。pending=待办 / applied=已执行 / dismissed=已忽略。
ProposalKind = Literal["drop", "refine"]
ProposalStatus = Literal["pending", "applied", "dismissed"]


class DropJob(BaseModel):
    """投递提案的一条「剔除偏岗」（ADR 0023）。

    Agent 出 `drop_job_ids`（从本轮岗位挑偏岗），service 用本轮 jobs 把 id 补成
    `{id, title, company}` 再落报告——**标题/公司以 DB 为准**（不让模型编）。剔除的**理由**在
    报告正文的「提案」段里说（不逐岗存——模型逐岗给理由易与正文脱节）。前端提案区列清单 →
    人确认 → 调既有 `POST /jobs/{id}/followup {status:"not_pursuing", reason:"withdrawn"}`
    落实到跟进状态线（岗位离「未处理」tab）。修由人点，不是自动执行。
    """

    id: int
    title: str = ""
    company: str = ""


class AnalysisReportOut(BaseModel):
    """一份分析报告（读模型）。

    status：ready / computing / missing——前端据此渲染报告 / "分析中…" / 空态。
    headline = 一句话结论（面板顶部）；blocks = 报告正文的**有序块流**（text/chart 穿插，
    Markdown）；meta = 统计口径（总数 / 来源分布 / 深度覆盖 / 日期等，给前端标注
    "深度分析（有 JD）vs 概览（无 JD）"用）。
    投递提案（ADR 0023）：drop_jobs = 建议剔除的偏岗（空 = 没可剔的）；refine_hint =
    「哪组检索词捞歪了 / 该往哪收」的诊断文本（空 = 检索词无需动），喂 `POST /direction/refine`
    出提案。两者**不是**报告正文的一部分，是可执行载体。
    proposal_state（2026-10-11）：各类提案的**处置**（`{drop: applied, refine: dismissed}`），
    来自 `analysis_proposal_state` 表；缺省的 kind = pending。前端据此把「剔除 / 忽略」按钮换成
    「已剔除 / 已忽略」标记。报告是缓存、这份状态不在报告里。
    """

    model_config = {"extra": "ignore"}

    scope: AnalysisScope
    scope_key: str = ""  # batch=轮次 id / daily=本地日 / interview=场次 id（字符串化）
    status: AnalysisStatus = "ready"
    headline: str = ""
    blocks: list[ReportBlock] = Field(default_factory=list)  # 正文块流（文/图穿插，前端校验兜底渲染）
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    drop_jobs: list[DropJob] = Field(default_factory=list)  # 投递提案·剔除偏岗（人点确认才执行）
    refine_hint: str = ""  # 投递提案·检索词诊断（喂 refine 出提案、人拍板）
    proposal_state: dict[str, str] = Field(default_factory=dict)  # 提案处置（kind→status；缺省 pending）


class ProposalApplyRequest(BaseModel):
    """执行/忽略某个投递提案（ADR 0023 生命周期，2026-10-11）：只报轮次（+忽略时的种类）。

    **不递 job id 清单**——服务端从缓存报告现读现校（见 service.apply_drop_proposal），
    免得前端递来一份跟缓存脱节的清单。
    """

    model_config = {"extra": "ignore"}

    round_id: int
    kind: ProposalKind = "drop"  # 忽略时用；执行（drop）恒是 drop


class ProposalActionOut(BaseModel):
    """提案动作的返回：状态 + （剔除时）实际执行条数。"""

    status: ProposalStatus
    count: int = 0
