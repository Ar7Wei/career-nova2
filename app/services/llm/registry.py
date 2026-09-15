"""LLM 模型注册表：惰性实例化的 OpenAI 兼容模型。

本地单用户：单模型——唯一模型名来自 settings.DEFAULT_LLM_MODEL（可指 deepseek 等
OpenAI 兼容端点）。实例化是惰性的——首次 get() 才创建，避免 import 时因缺 API key 崩溃。

2026-08-26 砍多模型 fallback：本地单用户只配一个模型，原 _MODEL_NAMES 列表/下标/环形切换
是为多模型容灾留的死代码（count()=1 时循环只跑一格）。塌缩成单模型语义：get() 取默认模型、
reset() 清缓存。真要多模型时再按那时的需求加回，不预留不合用的钩子。
"""

from typing import Any, Dict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import settings
from app.core.logging import logger


def _build_llm(model_name: str, **kwargs: Any) -> ChatOpenAI:
    """构造一个 OpenAI 兼容 ChatOpenAI 实例。"""
    temperature = kwargs.pop("temperature", settings.DEFAULT_LLM_TEMPERATURE)
    max_tokens = kwargs.pop("max_tokens", settings.MAX_TOKENS)
    # max_tokens 是 ChatOpenAI 的显式字段，其 alias 就是 max_completion_tokens——线上报文
    # 仍是 {"max_completion_tokens": N}。原走 model_kwargs 是 langchain-core 1.6 前的旧写法，
    # 升级后会冒 UserWarning（「should be specified explicitly」），2026-09-09 改显式传参。
    return ChatOpenAI(
        model=model_name,
        api_key=SecretStr(settings.OPENAI_API_KEY),
        base_url=settings.OPENAI_BASE_URL,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )


class LLMRegistry:
    """单模型注册表（惰性实例化 + 缓存）。

    get()：带 kwargs 返回一次性新实例（结构化输出等需要定制链时用，不动共享缓存）；
          不带 kwargs 返回缓存的默认模型实例（聊天 agent 拿去 bind_tools 的那把）。
    reset()：清缓存——设置面板改模型名/base_url/API key 后调用，下次 get() 按新配置重建。
    """

    _instances: Dict[str, BaseChatModel] = {}

    @classmethod
    def get(cls, model_name: str | None = None, **kwargs: Any) -> BaseChatModel:
        """取模型实例。model_name 缺省 = 当前默认模型（settings.DEFAULT_LLM_MODEL）。

        带 kwargs → 返回新实例（不动共享缓存）；不带 → 返回缓存实例（惰性建）。
        """
        name = model_name or settings.DEFAULT_LLM_MODEL
        if kwargs:
            logger.debug("creating_llm_with_custom_args", model_name=name, custom_args=list(kwargs.keys()))
            return _build_llm(name, **kwargs)
        if name not in cls._instances:
            logger.debug("instantiating_llm", model_name=name)
            cls._instances[name] = _build_llm(name)
        return cls._instances[name]

    @classmethod
    def reset(cls) -> None:
        """清空缓存实例（改模型名/base_url/API key 后调用，下次 get() 按新配置重建）。"""
        cls._instances.clear()
        logger.info("llm_registry_cache_reset", default_model=settings.DEFAULT_LLM_MODEL)
