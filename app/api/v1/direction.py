"""投递方向路由（阶段二 v2）：读/改方向 + 岗位处置。

仅做 HTTP 收发，业务交给 direction service（红线：Router 不写业务）。
错误由 service 层抛 AppError、全局 handler 统一收口。

2026-08-20 定稿：方向由**聊天 agent 自动生成**（refine_direction/commit_direction 工具，
走 service 真源不经 HTTP）；投递页不再提供「从我的信息生成」按钮（面板只读当前方向 +
手填编辑）。故删去 POST /direction/refine 端点，保留 GET + commit（聊天内「接受方向」
卡片走 commit）。
2026-08-25 方向口头化：删 POST /direction/proposal/dismiss（方向卡片废除）；
commit 只写方向（返回 pending_disposal_count），岗位处置拆到
POST /direction/dispose-unprocessed（前端就地弹「留/删」后决定调不调）。
"""

from fastapi import APIRouter

from app.schemas.direction import (
    CitiesResponse,
    Direction,
    DirectionCommitRequest,
    DirectionCommitResponse,
    DirectionDisposeResponse,
)
from app.services import direction as service

router = APIRouter()


@router.get("/direction", response_model=Direction)
async def get_direction() -> Direction:
    """读当前方向（抓取器替换硬编码 query/城市用）。"""
    return await service.get_direction()


@router.get("/direction/cities", response_model=CitiesResponse)
async def list_cities() -> CitiesResponse:
    """可选城市库（有序 25 城，前端城市下拉数据源）。"""
    return CitiesResponse(cities=service.get_supported_cities())


@router.post("/direction/commit", response_model=DirectionCommitResponse)
async def commit_direction(req: DirectionCommitRequest) -> DirectionCommitResponse:
    """改方向定稿（人拍板）：写回 facts，返回待处置的未处理岗位数。role 与 keywords/cities 独立入参。"""
    return await service.commit_direction(
        role=req.role,
        keywords=req.keywords,
        cities=req.cities,
    )


@router.post("/direction/dispose-unprocessed", response_model=DirectionDisposeResponse)
async def dispose_unprocessed() -> DirectionDisposeResponse:
    """删除未处理岗位（前端岗位处置弹窗选了「清」）。keep 无操作，前端不调本端点。"""
    return await service.dispose_unprocessed_jobs()
