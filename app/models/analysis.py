"""数据表模型：投递页分析报告（apply.md §11.7，2026-09-14 定稿）。

投递页分析的产物缓存——**算一次存一次**（§11.7.5）。三种分析共用一张表，靠 `scope` 分：
- `batch`：未处理 tab 的批次分析。scope_key = 抓取轮次 id（`job.crawl_round_id`）——
  分析输入 = **本轮抓到的那批**（检索条件会改、轮次间不可比，§11.7 grill 定）。
  产出 = 总览报告（嘱咐性叙述）+ 若干「处方」（另落 `optimization_pending`，status=proposed，
  本表只存报告叙事）。
- `daily`：已投递 tab 的投递日报。scope_key = **本地日**（`YYYY-MM-DD`，UTC+8）——
  报告是"截至昨天"的纯函数（§11.7.5），同一日期永只算一份、永不需复算。
- `interview`：面试 tab 的单场准备。scope_key = 面试场次 id——展开某场时算一份。

`content` 是 JSON 文本列（报告结构化产物，读写都过 pydantic，不裸用）。**不存"未就绪"行**：
算完才写；读不到 = 该 scope 还没算过，由触发方补算（§11.7.5「展开不算、显示分析中」）。
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class AnalysisReport(SQLModel, table=True):
    """一份分析报告（按 scope + scope_key 唯一，算一次存一次）。"""

    __tablename__ = "analysis_reports"

    id: int | None = Field(default=None, primary_key=True)
    scope: str = Field(index=True)  # batch / daily / interview
    scope_key: str = Field(index=True)  # batch=轮次 id / daily=本地日 YYYY-MM-DD / interview=场次 id
    # 报告结构化内容（JSON 文本列）：总览叙事 + 统计口径 + （面试）JD/tips。
    # 处方**不在这里**——它是 `optimization_pending` 的行（status=proposed），随优化点状态机走。
    content: str = Field(default="{}")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
