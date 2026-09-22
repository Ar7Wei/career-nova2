"""投递页分析路由（apply.md §11.7，2026-09-14）。

三个入口对应三个 tab 的触发（§11.7.5）：
- `GET /analysis/unprocessed?round_id=`：未处理批次报告 + 处方列表。**一轮爬完**触发——
  前端在 kickCrawl 尾部调一次；读不到就在**后台补算**并返回 computing（前端轮询）。
- `GET /analysis/applied`：已投递日报（截至昨天，本地日）。**进页面**触发——有则读、
  没有则后台补算。
- `GET /analysis/interview/{id}`：单场面试准备。**展开该场次**触发。

「算一次存一次」：有缓存直接返回 ready；没有则起后台任务（同一 scope+key 防重入）返回
computing，前端轮询到 ready。**不阻塞请求**——LLM 慢，展开面板不该干等。

「改进」按钮的收录走 `/optimization/promote`（proposed → pending），不在这儿。
"""

import asyncio

from fastapi import APIRouter, Query

from app.core.logging import logger
from app.schemas.analysis import AnalysisReportOut
from app.services import analysis as analysis_service

router = APIRouter()

# 后台分析任务的强引用集（防 GC 回收未完成的 task——asyncio 只持弱引用）。
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """起一个后台分析任务并持强引用（跑完自动移除）。异常出声、不吞。"""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            # 分析失败不该炸请求；日志出声（后端 task 自身已在 service 里记关键节点）。
            logger.exception("analysis_background_failed", error=str(exc))

    task.add_done_callback(_done)


def _placeholder(scope: str, key: str, status: str) -> AnalysisReportOut:
    """占位响应：computing（正在算，前端轮询）/ missing（没东西可算，前端显示空态）。"""
    return AnalysisReportOut(scope=scope, scope_key=key, status=status)  # type: ignore[arg-type]


@router.get("/analysis/unprocessed", response_model=AnalysisReportOut)
async def unprocessed_analysis(
    round_id: int = Query(..., description="抓取轮次 id——分析输入 = 本轮那批岗位"),
) -> AnalysisReportOut:
    """未处理批次分析（报告 + 待收录处方）。有则读、无则后台补算返回 computing。"""
    key = str(round_id)
    cached = await analysis_service.read_batch(round_id)
    if cached is not None:
        return cached
    # 本轮无岗位 → 没东西可算，别起后台任务（否则前端永远轮询 computing）。
    if not await analysis_service.batch_has_input(round_id):
        return _placeholder("batch", key, "missing")
    if not analysis_service.is_computing("batch", key):
        _spawn(analysis_service.summarize_and_analyze_batch(round_id))
    return _placeholder("batch", key, "computing")


@router.get("/analysis/applied", response_model=AnalysisReportOut)
async def applied_analysis() -> AnalysisReportOut:
    """已投递日报（截至昨天，本地日界）。有则读、无则后台补算返回 computing。"""
    key = analysis_service.daily_scope_key()
    cached = await analysis_service.read_daily()
    if cached is not None:
        return cached
    # 窗口内无投递活动 → 没东西可算，别起后台任务（否则前端永远轮询 computing）。
    if not await analysis_service.daily_has_input():
        return _placeholder("daily", key, "missing")
    if not analysis_service.is_computing("daily", key):
        _spawn(analysis_service.refresh_daily())
    return _placeholder("daily", key, "computing")


@router.get("/analysis/interview/{interview_id}", response_model=AnalysisReportOut)
async def interview_analysis(interview_id: int) -> AnalysisReportOut:
    """单场面试准备（展开该场次触发）。有则读、无则后台补算返回 computing。"""
    key = str(interview_id)
    cached = await analysis_service.read_interview(interview_id)
    if cached is not None:
        return cached
    if not await analysis_service.interview_has_input(interview_id):
        return _placeholder("interview", key, "missing")
    if not analysis_service.is_computing("interview", key):
        _spawn(analysis_service.refresh_interview(interview_id))
    return _placeholder("interview", key, "computing")
