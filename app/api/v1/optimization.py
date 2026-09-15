"""optimization 路由：1.2 优化建议流的 HTTP 收发（红线：Router 不写业务）。

2026-08-10 落库升级：建议组落库 + 三栏面板（待定/已确认/正在聊）+ 聊一聊/裁决。
错误由 service 层抛 AppError、全局 handler 统一收口。
"""

from fastapi import APIRouter

from app.core.errors import NotFoundError
from app.schemas.optimization import (
    OptimizationDecideRequest,
    OptimizationDecideResponse,
    PendingSuggestionsResponse,
    SuggestionAcceptRequest,
    SuggestionAcceptResponse,
    SuggestionDiscussRequest,
    SuggestionRetractRequest,
    SuggestionRejectRequest,
)
from app.services.optimization import (
    accept_suggestion,
    pending_suggestions,
    promote_suggestion,
    reject_suggestion,
    retract_suggestion,
    start_discuss,
    update_suggestion,
)

router = APIRouter()


@router.get("/optimization/pending", response_model=PendingSuggestionsResponse)
async def pending() -> PendingSuggestionsResponse:
    """读建议面板三栏（待定 / 已确认 / 正在聊）。"""
    return await pending_suggestions()


@router.post("/optimization/accept", response_model=SuggestionAcceptResponse)
async def accept(req: SuggestionAcceptRequest) -> SuggestionAcceptResponse:
    """确认一条已落库建议（pending/discussing → confirmed）。去重兜底。"""
    s = await accept_suggestion(req.suggestion_id)
    if s is None:
        raise NotFoundError("建议不存在或已定论")
    return SuggestionAcceptResponse(pending_id=s.id)


@router.post("/optimization/reject", status_code=204)
async def reject(req: SuggestionRejectRequest) -> None:
    """拒绝一条已落库建议（→ rejected + 记偏好"拒掉这一类"）。"""
    await reject_suggestion(req.suggestion_id, req.reason)


@router.post("/optimization/discuss", status_code=204)
async def discuss(req: SuggestionDiscussRequest) -> None:
    """聊一聊：pending → discussing（面板右栏「正在聊」区）。"""
    await start_discuss(req.suggestion_id)


@router.post("/optimization/promote", response_model=SuggestionAcceptResponse)
async def promote(req: SuggestionAcceptRequest) -> SuggestionAcceptResponse:
    """收录一条处方进优化点面板（proposed → pending）——投递页分析面板的「改进」按钮。

    到这一步面板就多了一行，而面板本就每轮注入聊天 agent——这既是"收录"也是"交给 agent"
    （apply.md §11.7.7）。仅 proposed 可收录。
    """
    s = await promote_suggestion(req.suggestion_id)
    if s is None:
        raise NotFoundError("建议不存在或不是待收录的处方")
    return SuggestionAcceptResponse(pending_id=s.id)


@router.post("/optimization/retract", status_code=204)
async def retract(req: SuggestionRetractRequest) -> None:
    """撤回一条已定论建议（confirmed/discussing/rejected → pending，2026-08-12）。

    右三栏每条「撤回」：已确认（改前可撤回）、正在聊、已拒绝（版本内拉回）都能回到待定。
    """
    await retract_suggestion(req.suggestion_id)


@router.post("/optimization/update", response_model=OptimizationDecideResponse)
async def update(req: OptimizationDecideRequest) -> OptimizationDecideResponse:
    """更新一条建议（agent update_suggestion 工具执行，2026-08-25 起开放全状态操作）。

    decision：accept / reject / refine / split / discuss / retract（§12.6 优化点操作权）。
    """
    result = await update_suggestion(req.suggestion_id, req.decision, refined=req.refined, split_to=req.split_to)
    return OptimizationDecideResponse(result=result)
