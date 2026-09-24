"""简历文档路由：版本列表、当前文档、原件、存生成版、回滚（整份恢复工作台快照）。

仅做 HTTP 收发，业务交给 documents service（红线：Router 不写业务）。
回滚语义：软作废（目标稿之后标 superseded、目标稿恢复当前，不删历史）+
**整份恢复目标版开始时的工作台**（资料集 + 改动记录，直回不叠加；2026-09-24）。
错误由 service 层抛 AppError、全局 handler 统一收口。
"""

from fastapi import APIRouter, Query
from fastapi.responses import Response
from urllib.parse import quote

from app.core.errors import NotFoundError
from app.schemas.documents import (
    ResetAllResponse,
    ResumeCurrentResponse,
    ResumeDocument,
    ResumeDocumentCreate,
    ResumeResetRequest,
    ResumeResetResponse,
    ResumeTypographyUpdate,
    ResumeVersionsResponse,
    RollbackRequest,
    RollbackResponse,
)
from app.services.documents import (
    get_all_versions,
    get_current,
    get_document_original,
    get_versions,
    reset_all,
    reset_resume,
    rollback,
    save_document,
    update_current_typography,
)

router = APIRouter()


@router.get("/documents/current", response_model=ResumeCurrentResponse)
async def current_document() -> ResumeCurrentResponse:
    """取当前（最新）简历文档。无文档是正常空态 → document 为 null（2026-09-09）。"""
    doc = await get_current()
    return ResumeCurrentResponse(document=doc)


@router.post("/documents/current/typography", response_model=ResumeDocument)
async def update_typography(req: ResumeTypographyUpdate) -> ResumeDocument:
    """排版自由度防抖回后端（2026-09-02）：改当前版排版四参数 → 后端用该版 resume_json 重渲染 html + 配置落库。

    前端调排版先本地注入预览（即时），防抖后打这里落库——导出/重开吃这份带正确排版变量的 html。
    上传原件（无 resume_json）→ 409（前端已置灰控件，这里双保险）。
    """
    return await update_current_typography(req.typography)


@router.get("/documents/original")
async def current_original(document_id: int | None = Query(default=None, description="指定文档 id（唯一身份，2026-08-12）；省略 = 当前版本")) -> Response:
    """取某文档的上传原件文件（PDF/HTML 等，原生预览用）。

    省略 document_id = 当前版本（默认路径，重启后重建当前预览）；
    传 document_id = 回看模式，按 **id** 精确取该稿原件（不撞同号作废稿——version
    是显示标签可复用，id 才是唯一身份）。
    无原件（生成/回滚到生成版）→ 404；文件缺失 → 404（前端落 Markdown 预览）。
    返回原始字节流 + 原文件名（Content-Disposition），供前端 blob 重建。
    """
    original = await get_document_original(document_id)
    if original is None:
        raise NotFoundError("当前文档没有上传原件")
    data, name, ext = original
    # Content-Disposition 里的文件名必须是 ASCII——中文文件名（如「张三_简历.html」）
    # 直接内联会让 Starlette 序列化 header 时抛 UnicodeEncodeError → 500。
    # 用 RFC 5987（filename*=UTF-8''..）百分号编码，中文文件名在浏览器/axios 里正确还原。
    ascii_name = quote(name, safe="")
    # Content-Type 按扩展名：原生预览的 blob 需要正确 MIME 才能渲染——
    # 重启后前端拉原件建 blob，html 若是 octet-stream，iframe 无法解析（卡"正在渲染 HTML"）。
    media_type = {
        "html": "text/html; charset=utf-8",
        "pdf": "application/pdf",
        "txt": "text/plain; charset=utf-8",
        "md": "text/markdown; charset=utf-8",
    }.get(ext, "application/octet-stream")
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{ascii_name}"},
    )


@router.get("/documents/versions", response_model=ResumeVersionsResponse)
async def versions(all: bool = Query(default=False, description="True = 含软作废稿（回看时间线聚合用）")) -> ResumeVersionsResponse:
    """列当前简历的全部稿（v1→vN 升序）。默认不含软作废稿（版本面板显示）。"""
    if all:
        return ResumeVersionsResponse(versions=await get_all_versions())
    return ResumeVersionsResponse(versions=await get_versions())


@router.post("/documents/save", response_model=ResumeDocument)
async def save_generated(req: ResumeDocumentCreate) -> ResumeDocument:
    """存一版生成/修改的简历文档（LLM 组合产出），顺带打事实快照。"""
    return await save_document(markdown=req.markdown, source=req.source, html=req.html)


@router.post("/documents/rollback", response_model=RollbackResponse)
async def rollback_document(req: RollbackRequest) -> RollbackResponse:
    """回滚到目标稿（软作废：目标之后全标 superseded，目标变当前）。按 document_id 身份锚定。

    2026-09-24：回滚 = 整份恢复该版**开始时**的工作台（资料集 + 改动记录），无开关；
    回滚到最早一版被拒（那是「重置」，见 service.rollback）。
    """
    doc, workspace_restored = await rollback(req.document_id)
    return RollbackResponse(version=doc.version, workspace_restored=workspace_restored)


@router.post("/documents/reset", response_model=ResumeResetResponse)
async def reset_document(req: ResumeResetRequest) -> ResumeResetResponse:
    """重置简历：清空文档流 + 快照（回到空态，可重传新 v1）；clear_facts 连事实清空。"""
    counts = await reset_resume(clear_facts=req.clear_facts)
    return ResumeResetResponse(**counts)


@router.post("/reset/all", response_model=ResetAllResponse)
async def reset_everything() -> ResetAllResponse:
    """核爆（「重新开始」）：清空一切求职数据回到出厂态。

    删文档流 + 事实 + 会话 + 建议 + 偏好 + 投递侧（jobs + job_followup_events），
    apply_mode 强制置 off；用户配置（LLM key/模型/语言）保留。不推荐的后悔药。
    """
    counts = await reset_all()
    return ResetAllResponse(**counts)
