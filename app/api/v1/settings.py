"""settings 路由：仅做 HTTP 收发，业务交给 settings service（红线：Router 不写业务）。

API key 只写不回读：GET/PATCH 响应经 mask_settings 掩码，前端拿不到明文。
LLM 探测（测试连接 + 拉模型列表）：走 app.services.llm.config，默认用已保存设置。
"""

from fastapi import APIRouter

from app.schemas.llm_config import LlmProbeRequest, LlmProbeResult
from app.schemas.settings import AppSettingsPatch, AppSettingsPublic, mask_settings
from app.services.llm.config import probe_llm
from app.services.settings import get_settings, update_settings

router = APIRouter()


@router.get("/settings", response_model=AppSettingsPublic)
async def read_settings() -> AppSettingsPublic:
    """读全局设置（无记录时返回默认值）。llm_api_key 一律掩码。"""
    return mask_settings(await get_settings())


@router.patch("/settings", response_model=AppSettingsPublic)
async def patch_settings(patch: AppSettingsPatch) -> AppSettingsPublic:
    """部分更新全局设置：只更新传入的字段。llm_api_key 掩码返回。"""
    return mask_settings(await update_settings(patch))


@router.post("/settings/llm/probe", response_model=LlmProbeResult)
async def llm_probe(payload: LlmProbeRequest) -> LlmProbeResult:
    """探测 LLM：先发一条极短消息验证连接，再拉供应商模型列表。

    请求体可选覆盖 model/base_url/key（默认用已保存设置）；覆盖不写入设置。
    返回三态：ok=False（连接失败）；ok=True+models_ok=False（连接过但拉不到列表）；
    ok=True+models_ok=True（全成功，models 为真实列表）。
    """
    return await probe_llm(payload.llm_model, payload.llm_base_url, payload.llm_api_key)
