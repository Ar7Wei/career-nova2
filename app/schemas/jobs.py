"""岗位聚合（阶段二 v1）+ 投递跟进状态时间线 传输模型。

API 契约（docs/API.md「投递·岗位聚合」/「投递·跟进」，2026-08-30 定稿），从界面字段倒推：
- Job：源岗位（列表/详情）。is_new = 本轮刷新新增（增量标记）。
- JobFollowupEvent / FollowupEventCreate / FollowupListResponse：投递跟进状态时间线，
  取代已作废的 JobScreening / ScreeningUpdate（「投了/没投布尔 + 6 值原因」）。
- JobsRefreshResponse：刷新结果（新增/更新条数）。
字段与前端 src/mocks/apply.ts 的 Job / JobScreening 类型对齐。
"""
from typing import Literal

from pydantic import BaseModel, Field

# 岗位来源平台（v1：猎聘主 connector；前程无忧走 Electron 读 DOM；BOSS/智联已砍 2026-08-31）
JobSource = Literal["liepin", "job51"]
# 岗位生死（程序爬的候选信号，可改；closed 灰度可见）
JobLiveness = Literal["active", "stale", "closed", "unknown"]
# JD 抓取状态（2026-09-14，apply.md §11.7.8）——none=平台无 JD（猎聘）/ fetched=抓到 /
# failed=抓失败（job51 读 DOM 超时等）。分不清"真空"与"抓失败"，投递页分析就写不了"深度/概览"。
JdStatus = Literal["none", "fetched", "failed"]
# 投递跟进状态（单一状态线，apply.md §12 存储态——事件表只存这 4 值）
FollowupStatus = Literal["applied", "interviewing", "offered", "not_pursuing"]
# 投递跟进状态（展示态，含派生的「已面试」——读时按 now ≥ 场次 scheduled_at 现算，不写事件）
FollowupStatusDisplay = Literal["applied", "interviewing", "interviewed", "offered", "not_pursuing"]
# 不再追踪的原因（仅 status=not_pursuing 有值）
FollowupReason = Literal["withdrawn", "failed", "job_closed", "duplicate"]
# 面试场次结果（apply.md §12.10 / ADR 0006）
InterviewOutcome = Literal["scheduled", "awaiting", "next_round", "offered", "failed"]


class Job(BaseModel):
    """一条源岗位（读模型，列表/详情）。"""

    model_config = {"extra": "ignore"}

    id: int
    source: JobSource
    external_id: str = ""
    title: str = ""
    company: str = ""
    city: str = ""
    salary_text: str = ""
    experience: str = ""
    degree: str = ""
    skills: list[str] = Field(default_factory=list)
    job_labels: list[str] = Field(default_factory=list)
    welfare: list[str] = Field(default_factory=list)
    description: str = ""
    jd_status: JdStatus = "none"  # JD 抓取状态（none/fetched/failed）——投递页分析分「深度/概览」用
    source_url: str = ""
    apply_url: str = ""
    liveness: JobLiveness = "unknown"
    last_seen_at: str = ""
    is_new: bool = False  # 本轮刷新新增（增量标记）
    crawl_round_id: int | None = None  # 首触的抓取轮次 id（首触即定，后轮不改）
    # 投递跟进当前态（2026-08-30 定稿 + 2026-08-31 派生态）：派生自 job_followup_events
    # 最新一条；无事件 = None = 未处理。interviewing 可能被惰性派生为 interviewed（已面试）。
    followup_status: FollowupStatusDisplay | None = None
    followup_reason: FollowupReason | None = None  # 仅 not_pursuing 有值（不再追踪 tab 原因 chip）
    followup_at: str = ""  # 当前态变更时间（跟进 tab 排序键；未处理时按 last_seen_at 排）


class JobFollowupEvent(BaseModel):
    """一条投递跟进状态事件（读模型，时间线的一行）。"""

    model_config = {"extra": "ignore"}

    id: int
    job_id: int
    status: FollowupStatus
    reason: FollowupReason | None = None
    stage: str = ""
    note: str = ""
    source: str = "user"
    at: str = ""


class FollowupEventCreate(BaseModel):
    """追加一条投递跟进状态事件（写模型）。任意跳、可回退、无状态机校验。"""

    model_config = {"extra": "ignore"}

    status: FollowupStatus
    reason: FollowupReason | None = None  # 仅 not_pursuing 有值
    stage: str = ""  # 仅 interviewing 有值（自由文本）
    note: str = ""  # 这次变化的"为什么"


class FollowupListResponse(BaseModel):
    """某岗位的完整状态时间线（按 at 升序）。"""

    events: list[JobFollowupEvent] = Field(default_factory=list)


class JobsRefreshResponse(BaseModel):
    """刷新结果：新增 N 条 / 更新 M 条。aborted = 代次已变（核爆），丢弃未回写。"""

    added: int = 0
    updated: int = 0
    aborted: bool = False


class JobIngestItem(BaseModel):
    """一条待入库的归一化岗位（connector / Electron 抓取层产出，ingest 入参）。

    skills / job_labels / welfare 传 list（repo 落库时序列化为 JSON 文本列）；
    connector 若已给 JSON 字符串也兼容（repo 只在是 list 时才序列化）。
    字段缺省即 upsert 落默认值。
    """

    model_config = {"extra": "ignore"}

    source_name: JobSource
    external_id: str = ""
    title: str = ""
    company: str = ""
    city: str = ""
    salary_text: str = ""
    experience: str = ""
    degree: str = ""
    skills: list[str] | str = Field(default_factory=list)
    job_labels: list[str] | str = Field(default_factory=list)
    company_scale: str = ""
    company_stage: str = ""
    company_industry: str = ""
    welfare: list[str] | str = Field(default_factory=list)
    description: str = ""
    jd_status: JdStatus = "none"  # job51 抓取层填 fetched/failed；猎聘无 JD 恒 none
    source_url: str = ""
    apply_url: str = ""
    liveness: JobLiveness = "active"
    platform_updated_at: str | None = None
    # 归属字段（**首触即定**，§11.7.8）：轮次 id（轮开始时间戳）+ 首触召回它的查询组词。
    # repo.upsert_job 只在插入时落这两个，更新分支跳过——否则被后一轮覆盖 → 归属错乱。
    crawl_round_id: int | None = None
    found_by_query: str = ""


class JobIngestRequest(BaseModel):
    """Electron 读 DOM 抓到的归一化岗位批量入库请求。

    jobs：connector/抓取层归一化后的岗位条目（JobIngestItem），至少含
    source_name/external_id/title；其余字段缺省由 repo.upsert_job 落默认值。
    data_epoch（2026-08-21 方案②）：后台批爬开抓时的数据代次——后端在同一事务里
    比对，核爆后丢弃在途、不回写。手动刷新不传（None=不守卫）。
    """

    model_config = {"extra": "ignore"}

    jobs: list[JobIngestItem] = Field(default_factory=list)
    data_epoch: int | None = None


class JobIngestResponse(BaseModel):
    """批量入库结果：新增 N 条 / 更新 M 条。aborted = 代次已变（核爆），丢弃未回写。"""

    added: int = 0
    updated: int = 0
    aborted: bool = False


class JobCounts(BaseModel):
    """各状态徽标数（GET /jobs 顶层 counts，apply.md §12.11⑤）。

    interviewing = 待面试场次数（非岗位数，与「面试」tab 徽标一致）。其余 = 岗位数。
    全局计数（不受 source/status/date 筛选影响——漏斗总览，非当前视图钻取）。
    """

    unprocessed: int = 0
    applied: int = 0
    interviewing: int = 0
    offered: int = 0
    not_pursuing: int = 0


class JobBrief(BaseModel):
    """面试场次内嵌的岗位摘要（日历详情弹窗用：company/title/description）。"""

    model_config = {"extra": "ignore"}

    company: str = ""
    title: str = ""
    description: str = ""


class JobsListResponse(BaseModel):
    """岗位列表（跨平台统一列表）。counts = 各状态徽标数（一次请求列表 + 全徽标）。"""

    jobs: list[Job] = Field(default_factory=list)
    counts: JobCounts = Field(default_factory=JobCounts)


class Interview(BaseModel):
    """一场面试（读模型，日历的一个标注）。"""

    model_config = {"extra": "ignore"}

    id: int
    job_id: int
    scheduled_at: str = ""  # 面试开始时间（ISO 字符串）
    location: str = ""
    round: str = ""  # 轮次自由文本（「技术一面」「HR面」）
    outcome: InterviewOutcome = "scheduled"
    next_round_id: int | None = None
    job: JobBrief = Field(default_factory=JobBrief)  # 内嵌岗位摘要（日历详情弹窗联查用）


class InterviewCreate(BaseModel):
    """新建一场面试（写模型）。排面试 = 唯一进入「待面试」的入口。"""

    model_config = {"extra": "ignore"}

    scheduled_at: str  # 面试开始时间（ISO 字符串，必填）
    location: str = ""
    round: str = ""


class InterviewUpdate(BaseModel):
    """更新一场面试（写模型）：改期 / 改结果 / 改地点轮次。

    outcome 终局（offered/failed）时由 service 联动岗位状态线；reason 仅在 outcome=failed
    时用于选择岗位 not_pursuing 的原因（未通过/岗位关闭/主动放弃）。
    """

    model_config = {"extra": "ignore"}

    scheduled_at: str | None = None
    location: str | None = None
    round: str | None = None
    outcome: InterviewOutcome | None = None
    # failed 时联动状态线用的 reason（默认 failed = 未通过）
    reason: FollowupReason | None = None
    # next_round 时联动：本次填的下一场时间/地点/轮次
    next_scheduled_at: str | None = None
    next_location: str = ""
    next_round: str = ""


class InterviewListResponse(BaseModel):
    """某岗位 / 全量面试场次列表（日历数据源）。"""

    interviews: list[Interview] = Field(default_factory=list)


class ExternalIdsResponse(BaseModel):
    """某平台已入库的 external_id 集合（Electron 点击遍历去重用：只点没抓过的）。"""

    ids: list[str] = Field(default_factory=list)
