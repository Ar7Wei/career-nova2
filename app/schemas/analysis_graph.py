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

from app.schemas.analysis import DropJob
from app.schemas.jobs import Job, JobFollowupEvent
from app.schemas.market import DistributionSlice
from app.schemas.report_block import ReportBlock

# 分析 scope（graph 入口分流键）。interview 暂不并入本 graph（单场数据薄，维持线性）。
AnalysisGraphScope = Literal["batch", "daily"]


class UserProfile(BaseModel):
    """从简历 JSON **现算**出的用户门槛画像（ADR 0023，2026-10-11）。

    门槛对表要比大小，就得先有「用户这一侧的确定值」：总年限（最早工作起始 → 今天）+
    最高学历。取简历成品（`resume_json`）而非 facts 自由文本——日期/学历是结构化的，
    能确定性算；facts 是自然语言，算不准。算不出（无简历/坏 JSON）→ `None`，门槛整体不做。
    """

    years: float | None = None  # 总工作年限（最早一段起始 → 今天，年）
    years_basis: str = ""  # 年限依据（最早那段：公司/起始年月），可溯源
    degree: str = ""  # 最高学历（归一后的词，如 硕士/本科/大专）
    degree_rank: int = 0  # 学历档位（博士4/硕士3/本科2/大专1/其它0）
    target: str = ""  # 目标岗位（basics.label，作「检索词调整」参照）
    city: str = ""  # 目标城市（basics.location.city）


class GatePack(BaseModel):
    """用户 × 岗位门槛的确定性对表（ADR 0023，2026-10-11；compute_gate 产出）。

    「你的门槛过几成」——把用户年限/学历与岗位 experience/degree 做**三档对表**：
    达标（clear）/ 差一点（risky，差 1 年内）/ 明显不够（blocked）。门槛未知（经验不限、
    学历不限、空、不可解析）**不计入分母**——只对**有明确门槛**的岗说实话，别拿「不限」充数。
    这一层直接支撑「投递分析」报告的第一段「判词：这批岗你要不要得起」。
    """

    my_years: float | None = None  # 用户总年限（回带，供 prompt 讲「你几年」）
    my_degree: str = ""  # 用户最高学历（回带）
    my_target: str = ""  # 用户目标岗位（basics.label，回带——「这批岗是不是你要的」参照）
    my_city: str = ""  # 用户期望城市（basics.location.city，回带）
    # 经验门槛三档（只统计 exp 明确的岗）
    exp_known: int = 0
    exp_clear: int = 0
    exp_risky: int = 0  # 差 1 年内
    exp_blocked: int = 0
    # 学历门槛三档（只统计 degree 明确的岗）
    deg_known: int = 0
    deg_clear: int = 0
    deg_risky: int = 0  # 学历门槛若可量化（一般不可）——留位，通常 0
    deg_blocked: int = 0
    # 双门槛同时明确、且同时达标的岗数（「直接能投」的核心量）
    both_known: int = 0
    both_clear: int = 0


class StatsPack(BaseModel):
    """一批岗位的确定性前置统计（compute_stats 产出，喂 analyze Agent）。

    分布切片与 MarketSnapshot 同构（label+count 降序），前端图表渲染直接吃这个形状。
    """

    total: int = 0
    liepin: int = 0  # 猎聘（搜索列表字段、无 JD）条数
    job51: int = 0  # 前程无忧（JD 全文）条数
    with_jd: int = 0  # job51 中 jd_status=fetched 的条数（深度分析覆盖）
    skill_freq: list[DistributionSlice] = Field(default_factory=list)  # 技能词频 top15
    by_city: list[DistributionSlice] = Field(default_factory=list)
    by_degree: list[DistributionSlice] = Field(default_factory=list)
    by_experience: list[DistributionSlice] = Field(default_factory=list)
    salary_buckets: list[DistributionSlice] = Field(default_factory=list)
    gate: "GatePack | None" = None  # 门槛对表（有用户画像时才算；ADR 0023）


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

    scope 分流后：load 填 jobs → compute_stats 填 stats → analyze 填 headline/blocks →
    persist 落库。meta 由 load/compute_stats 累积口径。
    """

    scope: AnalysisGraphScope = "batch"
    scope_key: str = ""  # batch=轮次 id / daily=本地日
    round_id: int | None = None  # batch 取数键
    jobs: list[Job] = Field(default_factory=list)
    # 喂 analyze 的素材（service 备好注入；round_summary 含 job51 JD 提炼，属重活不进 graph）。
    facts: str = ""
    round_summary: str = ""
    tone_note: str = ""
    user_profile: UserProfile | None = None  # 用户门槛画像（prepare 注入；供 compute_stats 算 gate）
    stats: StatsPack | None = None  # batch：岗位画像统计
    daily_stats: DailyStatsPack | None = None  # daily：投递活动统计（与 stats 二者其一）
    # daily 取数素材（service 从 repo 取好喂进 state）：窗口内的跟进事件 + 岗位对。
    daily_events: list[tuple[JobFollowupEvent, Job]] | None = None
    daily_as_of: str = ""  # daily 观察窗截至日（本地日）
    daily_window_days: int = 0  # daily 观察窗天数
    headline: str = ""
    blocks: list[ReportBlock] = Field(default_factory=list)  # 报告正文块流（文/图穿插，analyze 自由配）
    meta: dict[str, Any] = Field(default_factory=dict)  # 统计口径（前端标注深度/概览）
    # 投递提案（ADR 0023）：analyze 产出后由 service 补全/校验，persist 落进报告。
    drop_jobs: list[DropJob] = Field(default_factory=list)  # 建议剔除的偏岗（id 校验 ⊆ 本轮）
    refine_hint: str = ""  # 检索词诊断（「哪组词捞歪了」），喂 refine 出提案、人拍板
