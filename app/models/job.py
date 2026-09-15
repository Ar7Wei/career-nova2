"""数据表模型：岗位聚合（阶段二 v1）+ 投递跟进状态时间线 + 面试场次。

apply.md §4 / §12 / §12.10：
- `jobs`：一条 = 一个平台的**源岗位**。跨源不去重（同一岗位多平台都留，用户想投
  哪个投哪个，多多益善）。同源幂等 upsert（语义唯一 `source_name + external_id`，
  应用层 select-then-insert 兜底，非数据库 UNIQUE 约束）。
  liveness 是程序爬的**候选信号**（可改），closed 灰度可见。
  **归属字段首触即定**（`crawl_round_id` / `found_by_query`，2026-09-14 补）——upsert
  每轮重抓会全覆盖字段，这几列必须排除在更新之外，否则归属被后一轮改写（§11.7.8）。
- `job_followup_events`（2026-08-30 定稿）：投递跟进**状态时间线**，取代已作废的
  `job_screening`。时间线即真相——追加式事件表，当前态 = 该岗位最新一条事件
  （派生，不建快照列）；未处理 = 无事件。一条状态线 applied → interviewing →
  offered，负向终态 not_pursuing；任意跳、可回退、无状态机。
- `interview`（2026-08-31 定稿，ADR 0006）：**面试场次**——一次具体约面，一个岗位
  0..N 行。与「面试阶段」（状态线管）分两层：阶段是岗位在流水线的位置，场次是那
  一段里的每次约面。日历直接读全表、不过滤状态（编年，历史可追溯）。
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class Job(SQLModel, table=True):
    """一条源岗位（跨平台统一列表的一行）。"""

    __tablename__ = "jobs"

    id: int | None = Field(default=None, primary_key=True)
    source_name: str = Field(index=True)  # 平台：liepin（主）/ job51（boss/zhilian 已砍 2026-08-31）
    external_id: str = Field(default="")  # 平台岗位 ID（Boss 的 encryptJobId 等）
    title: str = Field(default="")
    company: str = Field(default="")
    city: str = Field(default="")  # 原始字符串，不强归一
    salary_text: str = Field(default="")  # 原始字符串，不强归一
    experience: str = Field(default="")
    degree: str = Field(default="")
    skills: str = Field(default="[]")  # JSON 数组文本列
    job_labels: str = Field(default="[]")
    company_scale: str = Field(default="")
    company_stage: str = Field(default="")
    company_industry: str = Field(default="")
    welfare: str = Field(default="[]")
    description: str = Field(default="")  # JD 正文（详情页读 DOM 抓取，可空：列表抓取无）
    # JD 抓取状态（2026-09-14，apply.md §11.7.8）：description=="" 分不清「平台本就没有 JD」
    # 与「抓失败了」——投递页分析靠它区分「深度分析（有 JD）」与「概览（无 JD）」两个面板。
    # none=平台无 JD（猎聘）/ fetched=抓到 / failed=抓失败（job51 读 DOM 超时等）。
    jd_status: str = Field(default="none", index=True)
    source_url: str = Field(default="")  # 岗位详情页 URL
    apply_url: str = Field(default="")  # 投递入口 URL
    liveness: str = Field(default="unknown", index=True)  # active / stale / closed / unknown
    platform_updated_at: str | None = Field(default=None)  # 平台发布时间（可空：Boss 不公开）
    is_new: bool = Field(default=True)  # 本轮刷新新增（增量标记，前端"新"角标；下次刷新再见则 False）
    # —— 归属字段（**首触即定**，upsert 更新分支必须跳过，见 repositories/jobs.upsert_job）——
    # 都是"这岗第一次进库时是谁抓的/哪个词召回的"，属历史事实，被后一轮覆盖即错乱。
    crawl_round_id: int | None = Field(default=None, index=True)  # 首触的抓取轮次 id（轮开始时间戳）
    found_by_query: str = Field(default="")  # 首触召回它的查询组词（"一组词 + 城市"的 join 文本）
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))  # 我们抓取时间（恒有）
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class JobFollowupEvent(SQLModel, table=True):
    """一条投递跟进状态事件（时间线的一行，apply.md §12.5）。

    - 一个岗位可有多条事件（时间线）；当前态 = 最新一条的 status（派生）。
    - status：applied / interviewing / offered / not_pursuing。
    - reason 仅 not_pursuing 有值：withdrawn / failed / job_closed / duplicate。
    - stage 仅 interviewing 有值（自由文本，如"技术二面"）。
    - source：v1 恒 user；L2 观察 / L3 自动投递来了记 observer 兜底。
    - at：变成这个状态的时间（跟进 tab 排序键 = 处理日期）。
    """

    __tablename__ = "job_followup_events"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(index=True, foreign_key="jobs.id")
    status: str = Field(index=True)  # applied / interviewing / offered / not_pursuing
    reason: str | None = Field(default=None)  # withdrawn / failed / job_closed / duplicate（仅 not_pursuing）
    stage: str = Field(default="")  # 面试轮次自由文本（仅 interviewing）
    note: str = Field(default="")  # 这次变化的"为什么"（自由文本）
    source: str = Field(default="user")  # user / observer
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Interview(SQLModel, table=True):
    """一场面试（一次具体约面，apply.md §12.10 / ADR 0006）。

    - 一个岗位 0..N 场（1:N）。日历数据源 = 全表（不过滤状态，编年可追溯）。
    - outcome：scheduled 待面 / awaiting 等结果 / next_round 下一场 / offered 录用 /
      failed 未通过。终局（offered/failed）才条件式驱动岗位状态线。
    - next_round_id：显式链指向下一场（自引用外键），只写一次（删除场景不存在）。
    """

    __tablename__ = "interviews"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(index=True, foreign_key="jobs.id")
    scheduled_at: datetime  # 面试开始时间（必填；上/下午由它派生）
    location: str = Field(default="")  # 自由文本可空（线上填链接、线下填地址，不区分 mode）
    round: str = Field(default="")  # 轮次自由文本（如「技术一面」「HR面」）
    outcome: str = Field(default="scheduled")  # scheduled / awaiting / next_round / offered / failed
    next_round_id: int | None = Field(default=None, foreign_key="interviews.id")  # 指向下一场
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
