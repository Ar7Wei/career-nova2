"""岗位聚合路由（阶段二 v1）：手动刷新、列表、详情、投递跟进状态线。

仅做 HTTP 收发，业务交给 jobs service（红线：Router 不写业务）。
错误由 service 层抛 AppError、全局 handler 统一收口。
"""

from typing import Literal

from fastapi import APIRouter, Query

from app.schemas.crawl_state import CrawlStateRead, CrawlStateReport, CrawlStateResult, KickResponse
from app.schemas.jobs import (
    ExternalIdsResponse,
    FollowupEventCreate,
    FollowupListResponse,
    InterviewCreate,
    InterviewListResponse,
    InterviewUpdate,
    Interview,
    Job,
    JobFollowupEvent,
    JobIngestRequest,
    JobIngestResponse,
    JobsListResponse,
)
from app.services import crawl as crawl_service
from app.services import jobs as service

router = APIRouter()

# 岗位列表筛选参数闭集（ADR 0013：排序只剩单一「时间」维度——time/followup）。
JobSortParam = Literal["time", "followup"]
JobOrderParam = Literal["asc", "desc"]
JobStatusParam = Literal["unprocessed", "applied", "interviewing", "offered", "not_pursuing"]
FollowupReasonParam = Literal["withdrawn", "failed", "job_closed", "duplicate"]


@router.post("/jobs/ingest", response_model=JobIngestResponse)
async def ingest_jobs(req: JobIngestRequest) -> JobIngestResponse:
    """批量入库（Electron 读 DOM 抓到的归一化岗位）。

    data_epoch 传入（前程无忧一轮）时若代次已变（核爆）→ 返回 aborted=True 丢弃（200，
    非错误——协作式取消的正常收尾）；该响应策略在 service 层。
    """
    return await service.ingest_requested(req)


@router.get("/jobs", response_model=JobsListResponse)
async def list_jobs(
    sort: JobSortParam = Query(default="time", description="排序维度：time / followup"),
    order: JobOrderParam = Query(default="desc", description="asc / desc"),
    status: JobStatusParam | None = Query(default=None, description="状态 tab"),
    reason: FollowupReasonParam | None = Query(default=None, description="不再追踪原因（仅 status=not_pursuing 叠加）"),
    start: str | None = Query(default=None, description="日期范围起点（闭区间，ISO 字符串）"),
    end: str | None = Query(default=None, description="日期范围终点（闭区间，ISO 字符串）"),
) -> JobsListResponse:
    """列岗位列表（跨平台统一列表，含排序/筛选/状态 tab + counts 徽标）。"""
    return await service.list_jobs(sort=sort, order=order, status=status, reason=reason, start=start, end=end)


@router.get("/jobs/external-ids", response_model=ExternalIdsResponse)
async def list_external_ids(source: str = Query(..., description="平台：liepin/job51")) -> ExternalIdsResponse:
    """读某平台已入库的 external_id 集合（Electron 点击遍历去重用：只点没抓过的）。"""
    return ExternalIdsResponse(ids=await service.list_external_ids(source))


@router.post("/jobs/crawl/kick", response_model=KickResponse)
async def kick_crawl() -> KickResponse:
    """事件驱动的一脚（前端 kickCrawl → Electron → 后端）。

    门控 + 持锁跑猎聘一轮（自循环 + 自记账）→ 返回猎聘结果 + 前程无忧只读建议。
    前程无忧的实际爬取由 Electron 在本响应之后驱动、POST /jobs/crawl-state 记账。
    """
    return await crawl_service.kick()


@router.get("/jobs/crawl-state", response_model=CrawlStateRead)
async def read_crawl_state(source: str = Query(..., description="平台：liepin / job51")) -> CrawlStateRead:
    """读某平台抓取游标（Electron 前程无忧一轮的起点）。"""
    return await crawl_service.read_crawl_state(source)


@router.post("/jobs/crawl-state", response_model=CrawlStateResult)
async def report_crawl_state(report: CrawlStateReport) -> CrawlStateResult:
    """一轮抓取结果上报（Electron 前程无忧一轮 → 后端记账推进游标 + 标三态）。"""
    return await crawl_service.report_crawl_state(report)


@router.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: int) -> Job:
    """读单条岗位（详情弹窗岗位概览）。"""
    return await service.get_job(job_id)


@router.post("/jobs/{job_id}/followup", response_model=JobFollowupEvent)
async def add_followup(job_id: int, req: FollowupEventCreate) -> JobFollowupEvent:
    """追加一条投递跟进状态事件（单一状态线，任意跳、可回退）。"""
    return await service.add_followup(job_id, req)


@router.get("/jobs/{job_id}/followup", response_model=FollowupListResponse)
async def list_followup(job_id: int) -> FollowupListResponse:
    """读某岗位的完整状态时间线（按 at 升序）。"""
    return await service.list_followup(job_id)


@router.get("/interviews", response_model=InterviewListResponse)
async def list_interviews(job_id: int | None = Query(default=None, description="只看某岗位的面试场次；省略 = 全量（日历数据源）")) -> InterviewListResponse:
    """列面试场次（日历数据源 = 全表，不过滤状态）。"""
    return await service.list_interviews(job_id=job_id)


@router.post("/jobs/{job_id}/interviews", response_model=Interview, status_code=201)
async def create_interview(job_id: int, req: InterviewCreate) -> Interview:
    """排一场面试（唯一进入「待面试」的入口）：建场次 + 联动 push interviewing。"""
    return await service.create_interview(job_id, req)


@router.patch("/interviews/{interview_id}", response_model=Interview)
async def update_interview(interview_id: int, req: InterviewUpdate) -> Interview:
    """更新一场面试：改期 / 改结果 / 安排下一场。终局（offered/failed）联动状态线。"""
    return await service.update_interview(interview_id, req)
