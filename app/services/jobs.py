"""岗位聚合 service：批量入库 + 列表/详情 + 投递跟进。

分层铁律：Router → Service → Repository。本层编排 repository，抛 AppError 子类
（错误出声），不碰 HTTP。抓取编排（kick/游标/三态）已迁到 app/services/crawl.py。
"""

from app.core.errors import EpochChangedError, NotFoundError
from app.repositories import jobs as repo
from app.schemas.jobs import (
    FollowupEventCreate,
    FollowupListResponse,
    InterviewCreate,
    InterviewListResponse,
    InterviewUpdate,
    Interview,
    Job,
    JobFollowupEvent,
    JobIngestItem,
    JobIngestRequest,
    JobIngestResponse,
    JobsListResponse,
)


async def ingest_jobs(raw_jobs: list[JobIngestItem], data_epoch: int | None = None) -> JobIngestResponse:
    """批量入库 Electron 读 DOM 抓到的岗位（同源幂等 upsert，与猎聘同一套入库逻辑）。

    raw_jobs：connector/抓取层归一化后的岗位条目（JobIngestItem），至少含
    source_name/external_id/title。逐条 upsert（同源去重），返回新增/更新数。
    data_epoch（2026-08-21 方案②）：后台批爬开抓代次，写前在同一守卫里比对——
    核爆后丢弃不回写（抛 EpochChangedError，调用方安静收尾）。
    """
    added = 0
    updated = 0
    async with repo.epoch_guard(data_epoch):
        for raw in raw_jobs:
            _, is_new = await repo.upsert_job(raw.model_dump())
            if is_new:
                added += 1
            else:
                updated += 1
    return JobIngestResponse(added=added, updated=updated)


async def ingest_requested(req: JobIngestRequest) -> JobIngestResponse:
    """入库端点编排：核爆（EpochChangedError）是协作式取消的正常收尾——吞掉、标 aborted。

    响应策略（200 + aborted=True，非 409 错误）归 service 层，Router 不再 try/except。
    """
    try:
        return await ingest_jobs(req.jobs, data_epoch=req.data_epoch)
    except EpochChangedError:
        return JobIngestResponse(added=0, updated=0, aborted=True)


async def list_jobs(
    sort: str = "time",
    order: str = "desc",
    status: str | None = None,
    reason: str | None = None,
    start: str | None = None,
    end: str | None = None,
    crawl_round_id: int | None = None,
) -> JobsListResponse:
    """列岗位列表（跨平台统一列表，含排序/筛选/状态 tab + counts 徽标）。"""
    jobs = await repo.list_jobs(
        sort=sort, order=order, status=status, reason=reason, start=start, end=end, crawl_round_id=crawl_round_id
    )
    counts = await repo.count_jobs()
    return JobsListResponse(jobs=jobs, counts=counts)


async def get_job(job_id: int) -> Job:
    """读一条岗位；不存在抛 NotFoundError。"""
    job = await repo.get_job(job_id)
    if job is None:
        raise NotFoundError("岗位不存在")
    return job


async def add_followup(job_id: int, event: FollowupEventCreate) -> JobFollowupEvent:
    """追加一条投递跟进状态事件；岗位必须存在（否则 404）。"""
    await get_job(job_id)  # 岗位不存在 → NotFoundError
    created = await repo.add_followup_event(job_id, event)
    assert created is not None  # 上面已确认岗位存在
    return created


async def list_followup(job_id: int) -> FollowupListResponse:
    """读某岗位的完整状态时间线；岗位必须存在（否则 404）。"""
    await get_job(job_id)  # 岗位不存在 → NotFoundError
    return FollowupListResponse(events=await repo.list_followup_events(job_id))


async def list_external_ids(source: str) -> list[str]:
    """读某平台已入库的全部 external_id（Electron 点击遍历去重用）。"""
    return await repo.list_external_ids(source)


# ---------------------------------------------------------------------------
# 面试场次（interviews，apply.md §12.10 / ADR 0006）
# ---------------------------------------------------------------------------


async def create_interview(job_id: int, data: InterviewCreate) -> Interview:
    """排一场面试 = 唯一进入「待面试」的入口（ADR 0006）。

    建面试场次（outcome=scheduled）+ 联动追加一条 interviewing 事件。
    岗位不存在抛 NotFoundError。
    """
    await get_job(job_id)  # 岗位不存在 → NotFoundError
    iv = await repo.create_interview(job_id, data)
    assert iv is not None
    await repo.add_followup_event(job_id, FollowupEventCreate(status="interviewing", stage=data.round or ""))
    return iv


async def list_interviews(job_id: int | None = None) -> InterviewListResponse:
    """列面试场次（日历数据源 = 全表；job_id 可选只看某岗位）。"""
    return InterviewListResponse(interviews=await repo.list_interviews(job_id=job_id))


async def update_interview(interview_id: int, data: InterviewUpdate) -> Interview:
    """更新一场面试：改期 / 改 outcome / 改地点轮次。返回更新后的本场。

    - outcome=offered → 联动 push offered。
    - outcome=failed → 联动 push not_pursuing，reason 默认 failed（可当场选 job_closed/withdrawn）。
    - outcome=next_round → 建下一场（新行 scheduled）+ 本场标 next_round + next_round_id 指向新行，
      状态线不新增（仍在面试阶段）。返回本场（含 next_round_id）。
    - 其余（scheduled/awaiting）与改期/改地点：不联动状态线。
    """
    cur = await repo.get_interview(interview_id)
    if cur is None:
        raise NotFoundError("面试不存在")

    if data.outcome == "next_round":
        # 安排下一场：需要下一场的时间（schema 不强制，service 语义要求）
        assert data.next_scheduled_at is not None, "next_round 需提供 next_scheduled_at"
        _, nxt = await repo.add_next_round(
            interview_id,
            InterviewCreate(scheduled_at=data.next_scheduled_at, location=data.next_location, round=data.next_round),
        )
        assert nxt is not None
        updated_cur = await repo.get_interview(interview_id)
        assert updated_cur is not None
        return updated_cur

    updated = await repo.update_interview(interview_id, data)
    assert updated is not None

    # 终局联动
    if data.outcome == "offered":
        await repo.add_followup_event(cur.job_id, FollowupEventCreate(status="offered"))
    elif data.outcome == "failed":
        reason = data.reason or "failed"
        await repo.add_followup_event(cur.job_id, FollowupEventCreate(status="not_pursuing", reason=reason))
    return updated
