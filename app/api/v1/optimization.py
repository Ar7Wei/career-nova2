"""optimization 路由：1.2 优化点的 HTTP 收发（红线：Router 不写业务）。

**2026-09-23 一张表**：优化点 = 改动记录（change_records）。面板读 `/optimization/records`，
逐条子项改状态走 `/optimization/record/status`。投递页处方的「改进」也走 `/optimization/record/status`
（子项 pending→confirmed，与面板定论同一端点——分析面板与优化点是同一 change_records 的两个视图）。
旧的建议流端点（/pending、/accept、/reject、/discuss、/retract、/update、/promote）随旧表停用一并删除。

错误由 service 层抛 AppError、全局 handler 统一收口。
"""

from fastapi import APIRouter

from app.schemas.optimization import (
    ChangeRecordListResponse,
    ChangeStatusRequest,
    OptimizationDecideResponse,
)
from app.services.optimization import (
    _ensure_not_applying,
    all_change_records,
    pending_records,
    set_change_item_status,
    set_record_status,
)

router = APIRouter()


@router.get("/optimization/records", response_model=ChangeRecordListResponse)
async def records(kind: str | None = None, scope: str = "active") -> ChangeRecordListResponse:
    """读改动记录（原因 + 改动点）。kind 可选过滤（change/decision）。

    - `scope=active`（默认）：只取活跃的——优化点四栏面板（可操作）。
    - `scope=all`：取**全部**（含已结清）——记录流面板（只读，看历史与跨版本决策）。
    """
    if scope == "all":
        all_records = await all_change_records(kind=kind)
    else:
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
