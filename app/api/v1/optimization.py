"""optimization 路由：1.2 优化点的 HTTP 收发（红线：Router 不写业务）。

**2026-09-23 一张表**：优化点 = 改动记录（change_records）。面板读 `/optimization/records`，
逐条子项改状态走 `/optimization/record/status`；投递页处方「改进」走 `/optimization/promote`
（落一条新记录 + 结清原处方行）。旧的建议流端点（/pending、/accept、/reject、/discuss、
/retract、/update）随旧表停用一并删除。

错误由 service 层抛 AppError、全局 handler 统一收口。
"""

from fastapi import APIRouter

from app.core.errors import NotFoundError
from app.schemas.optimization import (
    ChangeRecordListResponse,
    ChangeStatusRequest,
    OptimizationDecideResponse,
    PromotionRequest,
)
from app.services.optimization import (
    _ensure_not_applying,
    pending_records,
    promote_suggestion,
    set_change_item_status,
    set_record_status,
)

router = APIRouter()


@router.get("/optimization/records", response_model=ChangeRecordListResponse)
async def records(kind: str | None = None) -> ChangeRecordListResponse:
    """读活跃改动记录（原因 + 改动点）。kind 可选过滤（change/decision）。"""
    all_records = await pending_records()
    if kind is not None:
        all_records = [r for r in all_records if r.kind == kind]
    return ChangeRecordListResponse(records=all_records)


@router.post("/optimization/record/status", response_model=OptimizationDecideResponse)
async def record_status(req: ChangeStatusRequest) -> OptimizationDecideResponse:
    """改一条改动记录/子项的状态。给了 change_id 改子项，否则改整条记录。

    这是**用户入口**（面板）：文档任务执行中撞锁 → 抛 409（`_ensure_not_applying`）。
    agent 工具走 service 直调，不抛、返回可读反馈（见 `set_change_item_status` 注）。
    """
    _ensure_not_applying()
    if req.change_id is not None:
        result = await set_change_item_status(req.record_id, req.change_id, req.status)
    else:
        result = await set_record_status(req.record_id, req.status)
    return OptimizationDecideResponse(result=result)


@router.post("/optimization/promote", response_model=OptimizationDecideResponse)
async def promote(req: PromotionRequest) -> OptimizationDecideResponse:
    """收录一条处方进优化点（落一条改动记录，原处方行结清）——投递页分析面板的「改进」按钮。

    到这一步面板就多了一行，而面板本就每轮注入聊天 agent——这既是"收录"也是"交给 agent"
    （apply.md §11.7.7）。仅 proposed 可收录。
    """
    rec = await promote_suggestion(req.suggestion_id)
    if rec is None:
        raise NotFoundError("处方不存在或不是待收录状态")
    return OptimizationDecideResponse(result=f"处方已收录为改动记录 #{rec.id}。")
