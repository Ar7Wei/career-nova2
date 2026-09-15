"""投递页分析 graph 的状态与前置统计包（2026-09-15 方向定稿：批次分析 graph 化）。

- `StatsPack`：`compute_stats` 用代码算出的**确定性统计**（技能词频 / 城市 / 薪资 / 学历 /
  年限分布）——既是 analyze Agent 的 prompt 素材，又是 charts 的数据源。分布切片与
  `MarketSnapshot` 同构（`DistributionSlice`），前端渲染器好复用。
- `AnalysisState`：分析 graph 的流转状态。一个 graph 按 scope 分流（batch/daily），
  节点共用 load → compute_stats → analyze → persist。

分层红线：本层只定义数据形状；取数/落库在 service（注入节点），graph/node 不碰 DB。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.jobs import Job, JobFollowupEvent
from app.schemas.market import DistributionSlice
from app.schemas.report_block import ReportBlock

# 分析 scope（graph 入口分流键）。interview 暂不并入本 graph（单场数据薄，维持线性）。
AnalysisGraphScope = Literal["batch", "daily"]


class StatsPack(BaseModel):
    """一批岗位的确定性前置统计（compute_stats 产出，喂 analyze Agent）。

    分布切片与 MarketSnapshot 同构（label+count 降序），前端图表渲染直接吃这个形状。
    """

    total: int = 0
    liepin: int = 0  # 猎聘（结构化标签、无 JD）条数
    job51: int = 0  # 前程无忧（JD 全文）条数
    with_jd: int = 0  # job51 中 jd_status=fetched 的条数（深度分析覆盖）
    skill_freq: list[DistributionSlice] = Field(default_factory=list)  # 技能词频 top15
    by_city: list[DistributionSlice] = Field(default_factory=list)
    by_degree: list[DistributionSlice] = Field(default_factory=list)
    by_experience: list[DistributionSlice] = Field(default_factory=list)
    salary_buckets: list[DistributionSlice] = Field(default_factory=list)


class DailyStatsPack(BaseModel):
    """已投递**日报**的确定性前置统计（投递**活动**视角，`compute_daily_stats` 产出）。

    与 batch 的 `StatsPack`（岗位画像：技能/城市/薪资）不同——日报讲「投递这件事本身」的
    起伏（投了多少、谁回了、趋势如何），故统计的是**跟进事件**：状态分布 / 来源分布 /
    近 N 天逐日投递趋势 / 响应率。既是 prompt 素材，又是日报配图的数据源。
    """

    window_days: int = 14  # 观察窗（往回看 N 天，与 service 的 DAILY_LOOKBACK_DAYS 同源）
    as_of: str = ""  # 截至哪一天（本地日 YYYY-MM-DD）
    total_events: int = 0  # 窗口内事件总数
    by_status: list[DistributionSlice] = Field(default_factory=list)  # 当前态分布（含 interviewed 派生）
    by_source: list[DistributionSlice] = Field(default_factory=list)  # 来源分布（猎聘 / 前程无忧）
    daily_trend: list[DistributionSlice] = Field(default_factory=list)  # 逐日投递量（label=YYYY-MM-DD）
    applied_count: int = 0  # 窗口内投递（applied 事件）数
    responded_count: int = 0  # 有回音（interviewing/offered；not_pursuing 视作无回音）数
    response_rate: float = 0.0  # 响应率 = responded / applied（0~1；applied=0 时为 0）


class AnalysisState(BaseModel):
    """分析 graph 的流转状态（节点间单向传递，persist 节点读终态落库）。

    scope 分流后：load 填 jobs → compute_stats 填 stats → analyze 填
    headline/blocks/prescriptions → persist 落库。meta 由 load/compute_stats 累积口径。
    """

    scope: AnalysisGraphScope = "batch"
    scope_key: str = ""  # batch=轮次 id / daily=本地日
    round_id: int | None = None  # batch 取数键
    jobs: list[Job] = Field(default_factory=list)
    # 喂 analyze 的素材（service 备好注入；round_summary 含 job51 JD 提炼，属重活不进 graph）。
    facts: str = ""
    round_summary: str = ""
    tone_note: str = ""
    stats: StatsPack | None = None  # batch：岗位画像统计
    daily_stats: DailyStatsPack | None = None  # daily：投递活动统计（与 stats 二者其一）
    # daily 取数素材（service 从 repo 取好喂进 state）：窗口内的跟进事件 + 岗位对。
    daily_events: list[tuple[JobFollowupEvent, Job]] | None = None
    daily_as_of: str = ""  # daily 观察窗截至日（本地日）
    daily_window_days: int = 0  # daily 观察窗天数
    headline: str = ""
    blocks: list[ReportBlock] = Field(default_factory=list)  # 报告正文块流（文/图穿插，analyze 自由配）
    prescriptions: list[dict[str, Any]] = Field(default_factory=list)  # 待落 optimization_pending
    meta: dict[str, Any] = Field(default_factory=dict)  # 统计口径（前端标注深度/概览）
