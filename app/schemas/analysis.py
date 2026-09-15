"""投递页分析报告传输模型（apply.md §11.7，2026-09-14）。

报告 = 分析产出的**嘱咐**载体（叙事性结论，无状态、无处收录）；**处方**不在这里——
它是 `optimization_pending` 的 proposed 行（随优化点状态机走）。未处理 tab 的接口把
两者一起返回（一份报告 + 一列待收录的处方），是"一屏两物"而不是"一物两存"。

三 scope（§11.7.2）：batch 未处理批次 / daily 已投递日报 / interview 单场面试。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.optimization import PendingSuggestion
from app.schemas.report_block import ReportBlock

# 分析 scope（一张表三种报告，§11.7.5）
AnalysisScope = Literal["batch", "daily", "interview"]
# 报告就绪态（§11.7.5「展开不算、显示分析中」）：ready=有缓存 / computing=正在算 /
# missing=没算过且本轮也没东西可算（如本轮无岗位）。
AnalysisStatus = Literal["ready", "computing", "missing"]


class AnalysisReportOut(BaseModel):
    """一份分析报告（读模型）。

    status：ready / computing / missing——前端据此渲染报告 / "分析中…" / 空态。
    headline = 一句话结论（面板顶部）；blocks = 报告正文的**有序块流**（text/chart 穿插，
    Markdown）；meta = 统计口径（总数 / 来源分布 / 深度覆盖 / 日期等，给前端标注
    "深度分析（有 JD）vs 概览（无 JD）"用）。suggestions 仅 batch 有值——该批分析产出的
    待收录处方（点「改进」→ proposed→pending）。
    """

    model_config = {"extra": "ignore"}

    scope: AnalysisScope
    scope_key: str = ""  # batch=轮次 id / daily=本地日 / interview=场次 id（字符串化）
    status: AnalysisStatus = "ready"
    headline: str = ""
    blocks: list[ReportBlock] = Field(default_factory=list)  # 正文块流（文/图穿插，前端校验兜底渲染）
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    suggestions: list[PendingSuggestion] = Field(default_factory=list)
