"""settings service：默认值合并 + 读写 + 把运行时相关项同步进 settings 单例。

纯数据读写（无 LLM），允许 Service 直接走 Repository（不绕 Graph）。
"""

import json

from app.core.config import settings as runtime_settings
from app.core.logging import logger
from app.repositories.settings import get_settings_json, save_settings_json
from app.schemas.settings import AppSettings, AppSettingsPatch
from app.services.llm import llm_service

# LLM 相关字段：改了这些才需要重置 registry 缓存 + 清降级记忆（provider 能力未知，重新探测）。
# 其它偏好（language/apply_mode/crawl_*）不影响 LLM 连接与能力，
# 不该清降级记忆（2026-08-29 修复：切 apply_mode 曾把降级记忆清掉 → 重启后从头撞 json_schema）。
_LLM_FIELDS = {"llm_model", "llm_base_url", "llm_api_key"}


def _apply_to_runtime(s: AppSettings) -> None:
    """把运行时相关项同步进全局 settings 单例。

    - llm_model / llm_base_url / llm_api_key → registry 建模型时读（懒加载，下次真调生效）。
      API key 唯一来源 = SQLite 设置表（N1，2026-08-13 删 .env 兜底）——空就是未配置。
    - language → APP_LANGUAGE，Agent 组 prompt 注入回答语言时读。

    只同步值、不重置 LLM 缓存——重置是"真改了设置"才需要（update_settings 里做），
    纯读（get_settings/启动加载）不应清掉已实例化的模型。
    """
    runtime_settings.DEFAULT_LLM_MODEL = s.llm_model
    runtime_settings.OPENAI_BASE_URL = s.llm_base_url or None
    runtime_settings.OPENAI_API_KEY = s.llm_api_key
    runtime_settings.APP_LANGUAGE = s.language


async def get_settings() -> AppSettings:
    """读设置；无记录或部分字段缺失时用默认值补全，并同步 runtime。

    启动加载路径（lifespan 调用）：_apply_to_runtime 把 SQLite 持久化的模型名同步进
    settings 单例，registry 下次 get() 即按它建模型实例（registry 直接读
    settings.DEFAULT_LLM_MODEL，无 import 时固化）。无需在此重置缓存——模型名变了
    缓存键就变，旧实例自然不被命中。
    """
    raw = await get_settings_json()
    if raw is None:
        s = AppSettings()
    else:
        try:
            s = AppSettings.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            logger.exception("settings_parse_failed_using_defaults")
            s = AppSettings()
    _apply_to_runtime(s)
    # 单模型塌缩（2026-08-26）：registry 直接读 settings.DEFAULT_LLM_MODEL，且按模型名键控
    # 缓存——_apply_to_runtime 已把持久化模型名同步进 settings，get_settings 无需再管 registry
    # （模型名变了缓存键就变，旧实例自然不被命中）。重置只在"真改了设置"的 update_settings 里做。
    return s


async def update_settings(patch: AppSettingsPatch) -> AppSettings:
    """部分更新设置：合并到现有值、持久化、同步 runtime，并按需重置 LLM。

    只有改了 LLM 相关字段（model/base_url/key）才重置 registry 缓存 + 清降级记忆——
    其余偏好变更（language/apply_mode/crawl_*）不该动 provider 能力记忆。
    """
    current = await get_settings()
    # 嵌套模型（crawl_quota）：model_copy(update=dict) 会把 crawl_quota 塞成 dict 而非
    # CrawlQuota（update 值不 re-validate）。直接合并两份 dump 后 model_validate 一步成型，
    # 让 patch 里的 crawl_quota dict 经 pydantic 收口成 CrawlQuota，避免脏中间态。
    merged = AppSettings.model_validate({**current.model_dump(), **patch.model_dump(exclude_none=True)})
    await save_settings_json(merged.model_dump_json())
    _apply_to_runtime(merged)
    changed = set(patch.model_dump(exclude_none=True).keys())
    if changed & _LLM_FIELDS:
        llm_service.reset_llm()
        await llm_service.clear_degraded_providers()
    logger.info("settings_updated", changed=sorted(changed))
    return merged
