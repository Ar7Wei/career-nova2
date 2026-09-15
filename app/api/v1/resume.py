"""简历路由：上传转文档 v1（快）+ 后台抽取事实（待确认）。

重构后（文档为成品，事实为唯一真相源）：
- ``POST /resume/parse`` 同步只做 MarkItDown 转文档 + 存 v1，**不阻塞等抽取**——
  返回文档即完成，后台任务异步抽事实进抽取状态。LLM 从上传关键路径上消失。
- 抽取结果**不直接入库**：进内存态（`extract_state`），前端轮询
  ``GET /resume/extract-status`` 弹「抽取信息卡片」，确认才入库（confirm 端点）。
- 格式拦截（415）/ 扫描件（422）保持。
- 取消链：上传已不再等 LLM，后台抽取走调度器 fire-and-forget，LLM 内部取消事件保留。
- Router 只做 HTTP 收发：封闭通道守卫、代次校验、后台调度都收在 services/resume.py
  （本层只注入调度机制 BackgroundTasks.add_task，不 import extract_state / Repository）。
- 错误由 service/工具层抛 AppError、全局 handler 统一收口（Router 不写 try/except）。
"""

from fastapi import APIRouter, BackgroundTasks, UploadFile

from app.core.errors import PayloadTooLargeError
from app.schemas.documents import ResumeUploadResponse
from app.schemas.facts import (
    ExtractConfirmRequest,
    ExtractConfirmResponse,
    ExtractRejectRequest,
    ExtractRetryResponse,
    ExtractState,
)
from app.services import resume as resume_service

router = APIRouter()

# 单文件大小上限（本地单用户，给个防呆值）。
_MAX_FILE_BYTES = 20 * 1024 * 1024


@router.post("/resume/parse", response_model=ResumeUploadResponse)
async def parse(file: UploadFile, background_tasks: BackgroundTasks) -> ResumeUploadResponse:
    """上传简历文件：转文档 v1 立即返回；后台异步抽事实进抽取状态（待确认）。

    确认前通道开启（可重传）；确认后通道关闭（ready）。
    """
    data = await file.read()
    if len(data) > _MAX_FILE_BYTES:
        raise PayloadTooLargeError()
    return await resume_service.parse_resume(file.filename or "upload", data, background_tasks.add_task)


@router.get("/resume/extract-status", response_model=ExtractState)
async def extract_status() -> ExtractState:
    """读后台抽取状态（前端轮询：蒙版/进度条/卡片/通道开关）。"""
    return await resume_service.extract_status()


@router.post("/resume/extract/confirm", response_model=ExtractConfirmResponse)
async def confirm(req: ExtractConfirmRequest) -> ExtractConfirmResponse:
    """确认抽取卡片：统一冲突检测后入库（source=resume_upload），状态 → confirmed。

    generation 不匹配（旧卡片/代次已过）→ 409，前端重新拉状态。
    """
    return await resume_service.confirm_extract_requested(req)


@router.post("/resume/extract/reject", status_code=204)
async def reject(req: ExtractRejectRequest) -> None:
    """拒绝抽取卡片：该批不入库，状态回 idle、通道重开（可重传）。"""
    await resume_service.reject_extract_requested(req)


@router.post("/resume/extract/retry", response_model=ExtractRetryResponse)
async def retry(background_tasks: BackgroundTasks) -> ExtractRetryResponse:
    """重新抽取：对当前文档重新抽一遍（不重传、不清文档流）。

    用户对当前结果不满意（空卡片/想再试）时点「重新抽取」。代次 +1 作废旧抽取。
    """
    return await resume_service.retry_resume(background_tasks.add_task)
