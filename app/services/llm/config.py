"""LLM 配置辅助：合并的「测试连接 + 拉供应商模型列表」探测。

不碰 DB、不碰 Graph，只做 LLM 连通性探测。默认用已保存设置；请求体可覆盖
（base_url/key）——覆盖时用传入值建临时客户端，不写入设置、不影响 runtime。

流程编排（probe_llm）：先验证连接（发极短消息），连不上直接返回 ok=False 不拉模型；
连上后拉模型列表，分「连接过但拉不到」与「全成功」两态返回。错误不抛给上层，
一律转成带 message 的结果对象（探测本身"看能不能通"，连不上是正常结果）。
"""

from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI
from pydantic import SecretStr

from app.core.config import settings
from app.core.logging import logger
from app.schemas.llm_config import LlmProbeResult

# 测试消息：极短、不消耗多少 token，只为验证 key/endpoint 通不通。
_TEST_PROMPT = "Reply with exactly: OK"


def _resolve(base_url: str | None, api_key: str | None) -> tuple[str | None, str]:
    """解析实际用的 base_url/key：请求体覆盖优先，否则已保存设置。

    已保存设置里，OPENAI_API_KEY 由 _apply_to_runtime 从 SQLite 同步（N1：唯一来源，
    无 .env 兜底），所以这里只要 OR 请求体覆盖值即可。
    """
    return base_url or settings.OPENAI_BASE_URL, api_key or settings.OPENAI_API_KEY


def _test_messages() -> list[dict[str, str]]:
    return [{"role": "user", "content": _TEST_PROMPT}]


async def probe_llm(
    llm_model: str | None,
    llm_base_url: str | None,
    llm_api_key: str | None,
) -> LlmProbeResult:
    """探测：先验证连接，再拉模型列表。返回三态（见 LlmProbeResult docstring）。

    本地不存默认模型名（llm_model 未配置时为空）：
    - 有模型名 → 发极短消息验证 key/端点（能区分"端点通但没 /models"）。
    - 无模型名 → 跳过消息测试，直接拉模型列表——列表本身就需要有效 key/端点，
      成功即证明连接通；失败即连接/key 问题。
    """
    model = llm_model or settings.DEFAULT_LLM_MODEL
    base_url, api_key = _resolve(llm_base_url, llm_api_key)
    if not api_key:
        return LlmProbeResult(ok=False, message="no API key configured (fill it in settings first)")

    # 第 1 步：验证连接。无模型名时跳过（没有可测的模型，由第 2 步拉列表充当连接验证）。
    if model:
        try:
            llm = ChatOpenAI(
                model=model,
                api_key=SecretStr(api_key),
                base_url=base_url,
                temperature=0,
                max_retries=0,
                timeout=settings.LLM_TOTAL_TIMEOUT,
            )
            await llm.ainvoke(_test_messages())
        except Exception as e:  # noqa: BLE001 - 连接失败类型繁多，统一转可读信息
            logger.warning("llm_probe_connection_failed", model=model, error=str(e))
            return LlmProbeResult(ok=False, message=_friendly_error(e))
    logger.info("llm_probe_connection_ok", model=model or "(none)", base_url=base_url)

    # 第 2 步：拉模型列表（连接过，但列表可能拉不到——两种都算"探测完成"）。
    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=settings.LLM_TOTAL_TIMEOUT)
        resp = await client.models.list()
        models = sorted(m.id for m in resp.data)
    except Exception as e:  # noqa: BLE001
        logger.warning("llm_probe_models_failed", error=str(e))
        # 有模型名：消息测试已通过，说明是端点通但没 /models → partial（可手输模型）。
        # 无模型名：没有任何连接证据，就是连接失败。
        if model:
            return LlmProbeResult(ok=True, models_ok=False, message=_friendly_error(e))
        return LlmProbeResult(ok=False, message=_friendly_error(e))
    logger.info("llm_probe_models_ok", count=len(models))
    return LlmProbeResult(ok=True, models_ok=True, models=models)


def _friendly_error(e: Exception) -> str:
    """把异常压成一行可读信息（客户端错误常见，不堆栈）。"""
    msg = str(e).strip() or type(e).__name__
    return msg[:300]
