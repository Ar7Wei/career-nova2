"""generation 路由：统一「出简历」流水线的 HTTP 收发（红线：Router 不写业务）。

ADR 0012 合并（2026-09-07）：生成与改写收敛为一条人门流水线（services/rewrite.py）——
`POST /resume/generate` 出草稿跑图到人门挂起（不写库）、`GET /resume/generate/preview`
读挂起草稿（checkpointer，重启不丢）、`POST /resume/generate/confirm` 人拍板
（confirm 落库 vN+1 / revise 带 feedback 回图重改）。错误由 service 抛 AppError、
全局 handler 统一收口（Router 不写 try/except）。
"""

from fastapi import APIRouter

from app.schemas.generation import (
    GenerateConfirmRequest,
    GenerateConfirmResponse,
    GeneratePreviewResponse,
    GenerateRequest,
)
from app.schemas.sessions import ChatStopResponse
from app.services.rewrite import (
    generate_confirm,
    generate_preview,
    get_preview,
    get_preview_html,
    stop_generation,
)

router = APIRouter()


@router.post("/resume/generate", response_model=GeneratePreviewResponse)
async def generate(req: GenerateRequest) -> GeneratePreviewResponse:
    """出草稿：跑统一流水线到人门挂起（不写库）。target_role 显式给时先落 facts 槽位。

    apply_confirmed=True（「开始改」）→ 把已确认建议渲染成文本并进图（ADR 0015）。
    """
    json_text, html = await generate_preview(
        user_request="应用已确认的优化建议" if req.apply_confirmed else "生成一版简历",
        target_role=req.target_role,
        apply_confirmed=req.apply_confirmed,
    )
    return GeneratePreviewResponse(markdown=json_text, html=html)


@router.get("/resume/generate/preview", response_model=GeneratePreviewResponse)
async def preview() -> GeneratePreviewResponse:
    """读当前挂起草稿（agent 出稿后 / 重启恢复，前端拉取显示）。空 = 无挂起。"""
    return GeneratePreviewResponse(markdown=await get_preview(), html=await get_preview_html())


@router.post("/resume/generate/confirm", response_model=GenerateConfirmResponse)
async def confirm(req: GenerateConfirmRequest) -> GenerateConfirmResponse:
    """人门拍板：confirm → 落库 vN+1；revise → 带 feedback 回图重改（不写库，返回 version=None）。"""
    result = await generate_confirm(decision=req.decision, feedback=req.feedback)
    return GenerateConfirmResponse(version=result[0] if result is not None else None)


@router.post("/resume/generate/stop", response_model=ChatStopResponse)
async def stop() -> ChatStopResponse:
    """真停止：取消当前在途的「重新生成」（revise 回边重改，重新生成按钮触发）。

    与 POST /chat/stop 同型——revise 跑图在 chat 循环之外，需独立端点单独点它。
    """
    stopped = stop_generation()
    return ChatStopResponse(stopped=stopped)
