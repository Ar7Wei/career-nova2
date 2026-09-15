"""LLM service：单模型调用，带重试与结构化输出降级。

本地单用户单模型（2026-08-26 砍多模型 fallback）：唯一模型来自 settings.DEFAULT_LLM_MODEL。
call() 每次新建独立模型实例（one-off），不共享状态——结构化输出/定制 kwargs 天然隔离，
聊天 agent 的工具绑定在 registry 的缓存实例上做，两者互不干扰。

每条调用链：建链（含结构化输出，json_schema 不支持时降级 json_mode）→ tenacity 重试 →
失败抛 LLMUnavailableError。无模型索引、无切换、无 fallback 循环。
"""

import asyncio
import logging
from typing import (
    Any,
    Optional,
    Type,
    TypeVar,
    Union,
    overload,
)

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    OpenAIError,
    RateLimitError,
)
from pydantic import BaseModel
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.errors import LLMUnavailableError
from app.core.logging import logger
from app.repositories.settings import get_degraded_providers, save_degraded_providers
from app.schemas import Message
from app.services.llm.lenient_parser import lenient_structured_parser
from app.services.llm.registry import LLMRegistry
from app.utils.graph import messages_to_lc

T = TypeVar("T", bound=BaseModel)

# 已确认不支持 json_schema 的 provider（按 base_url 记）。首次结构化输出降级后记录，
# 后续同 base_url 调用直接走 json_mode，跳过 json_schema 无谓的 3 次重试（省 ~10s/次）。
# 内存缓存；持久化在 SQLite settings 表（key=llm_degraded，见 load/clear_degraded_providers）。
_DEGRADED_PROVIDERS: set[str] = set()

# json_mode 注入的 JSON 输出指令（DeepSeek json_object 硬校验：prompt 须含 "json"）。
_JSON_MODE_DIRECTIVE = (
    "You must respond with a single valid JSON object only. "
    "Output nothing except the JSON — no prose, no markdown fences."
)


def _normalize_messages(messages: list) -> list[BaseMessage]:
    """把 call() 入参归一成 BaseMessage 列表。

    收窄契约（2026-08-26）：只接两种合法形态——
    - BaseMessage（LangChain 原生）：内部一次性 prompt（HumanMessage）直接用。
    - pydantic Message（对话消息）：经 messages_to_lc 转换（user/assistant）。
    dict / 其他类型一律抛 ValueError——消息在进 call() 前就该已是这两种形态之一。
    """
    if all(isinstance(m, BaseMessage) for m in messages):
        return list(messages)
    if all(isinstance(m, Message) for m in messages):
        return messages_to_lc(messages)
    kinds = {type(m).__name__ for m in messages}
    raise ValueError(f"llm_service.call 只接受 BaseMessage 或 pydantic Message 列表，got {kinds}")


def _prepend_json_directive_fn(messages: list[BaseMessage]) -> list[BaseMessage]:
    """json_mode 链前置：在 messages 最前注入一条 JSON 输出 system 指令。

    唯一触发条件是「本次调用要结构化输出」——本 chain 只由 _build_chain 的 json_mode
    分支拼出来，能到这儿的调用必然要 JSON，所以无条件注入。

    2026-09-11：旧实现靠「messages 文本里有没有 "json" 字样」来决定注不注入，是个
    关键词嗅探——它在两种情况下出错：
      ① 误伤（危险）：prompt 恰好不含 "json" 字样时（如聊天 system prompt、开场白），
         被前置「You must respond with a single valid JSON object only」，而调用方
         根本不要 JSON。聊天路径的 system prompt 每轮重建，这条错指令会常驻上下文，
         与「自然对话」直接冲突。
      ② 漏注入：prompt 里已经出现 "json"（如「不要输出 JSON 之外的任何内容」）时
         整条指令被跳过，而模型未必真把「只出 JSON」当硬约束。
    """
    return [SystemMessage(content=_JSON_MODE_DIRECTIVE), *messages]


_prepend_json_directive = RunnableLambda(_prepend_json_directive_fn)


async def run_with_cancel(coro: Any, cancel_event: asyncio.Event, timeout: Optional[float] = None) -> Any:
    """等待 coro，但若 cancel_event 先触发则取消整个调用链并传播取消。

    公开原语（2026-08-14 从 LLMService._await_with_cancel 提为模块级）——聊天「真停止」
    的地基。复用原取消竞争逻辑，唯一改动是 timeout 可选（None = 不设总超时，聊天工具
    循环最多 40 轮、不能套 LLM_TOTAL_TIMEOUT=300s 否则长对话被误杀）。

    外层竞争：把 coro 包成一个 task，与 cancel_event.wait() 竞争。cancel_event 先到 →
    task.cancel() → in-flight 调用连重试一起被取消，抛 CancelledError 向上传播。
    """
    if cancel_event.is_set():
        coro.close()  # 未开始即取消：关掉未 await 的 coroutine，避免 ResourceWarning
        raise asyncio.CancelledError("cancelled before start")
    invoke_task = asyncio.create_task(coro if timeout is None else asyncio.wait_for(coro, timeout))
    cancel_wait_task = asyncio.create_task(cancel_event.wait())
    try:
        await asyncio.wait(
            {invoke_task, cancel_wait_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except BaseException:
        invoke_task.cancel()
        cancel_wait_task.cancel()
        raise
    finally:
        if not cancel_wait_task.done():
            cancel_wait_task.cancel()
    if cancel_event.is_set():
        # 取消整个调用链（含 in-flight）；吞掉内部取消/错误，向外抛统一 CancelledError。
        invoke_task.cancel()
        try:
            await invoke_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - 取消路径，不向上抛内部细节
            pass
        raise asyncio.CancelledError("cancelled by cancel_event")
    return await invoke_task  # invoke 先完成：成功或抛其自身异常


class LLMService:
    """单模型 LLM 调用：重试 + 结构化输出降级 + 取消。

    call() 每次经 LLMRegistry.get(**model_kwargs) 新建独立实例（带 kwargs 时不动共享缓存），
    因此结构化输出 / 定制参数彼此隔离，也不碰聊天 agent 绑定工具的缓存实例。
    """

    def __init__(self) -> None:
        """初始化（惰性——首次调用才建模型实例，本地无 API key 也能启动服务）。"""
        logger.info("llm_service_initialized_lazy")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @overload
    async def call(
        self,
        messages: list,
        model_name: Optional[str] = ...,
        response_format: None = ...,
        **model_kwargs: Any,
    ) -> BaseMessage: ...

    @overload
    async def call(
        self,
        messages: list,
        model_name: Optional[str] = ...,
        *,
        response_format: Type[T],
        **model_kwargs: Any,
    ) -> T: ...

    async def call(
        self,
        messages: list,
        model_name: Optional[str] = None,
        response_format: Optional[Type[BaseModel]] = None,
        cancel_event: Optional[asyncio.Event] = None,
        **model_kwargs: Any,
    ) -> Union[BaseMessage, BaseModel]:
        """调 LLM：建链（含结构化降级）→ 重试 → 失败抛 LLMUnavailableError。

        Args:
            messages: 对话/提示消息——BaseMessage 列表（内部 prompt）或 pydantic
                Message 列表（对话历史，内部转 LangChain）。
            model_name: 覆盖默认模型名；None 用 settings.DEFAULT_LLM_MODEL。
            response_format: Pydantic schema 结构化输出。给了就链 with_structured_output
                （json_schema 严格模式），返回校验后的 schema 实例而非裸 BaseMessage。
            cancel_event: 可选取消信号（如客户端断开），set 后提前取消 in-flight 调用。
            **model_kwargs: 传给 LLMRegistry.get 造实例（temperature/max_tokens/reasoning 等）。

        Raises:
            LLMUnavailableError: 重试耗尽仍失败或超总预算（503, retryable, action=open_settings）。
        """
        try:
            coro = self._call_once(messages, model_name, response_format, model_kwargs)
            if cancel_event is None:
                return await asyncio.wait_for(coro, timeout=settings.LLM_TOTAL_TIMEOUT)
            return await run_with_cancel(coro, cancel_event, settings.LLM_TOTAL_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.exception(
                "llm_total_timeout_exceeded",
                timeout_seconds=settings.LLM_TOTAL_TIMEOUT,
            )
            raise LLMUnavailableError(
                detail=f"模型调用超时（{settings.LLM_TOTAL_TIMEOUT}s 总预算）",
            )

    def reset_llm(self) -> None:
        """重置 registry 缓存：改模型名/base_url/API key 后调用。

        只清 registry 缓存（下次 get() 按新配置重建实例）。降级记忆是 provider 能力、
        持久化在 SQLite，与此无关——由 clear_degraded_providers() 单独负责（settings service
        在 LLM 相关字段变更时一并调用）。拆分（2026-08-29）：旧实现把两者绑在 reset_llm 里
        清，导致切 apply_mode 等无关设置也把降级记忆清掉、重启后从头撞 json_schema。
        """
        LLMRegistry.reset()
        logger.info("llm_service_reset_for_settings_change")

    async def load_degraded_providers(self) -> None:
        """启动时把 SQLite 里持久化的降级记忆读回内存（_DEGRADED_PROVIDERS）。

        降级记忆是「这个 base_url 不支持 json_schema」的 provider 能力，与进程无关——
        后端重启不丢，下次结构化调用直接走 json_mode，不再从头撞 3 次 json_schema 重试。
        """
        providers = await get_degraded_providers()
        _DEGRADED_PROVIDERS.clear()
        _DEGRADED_PROVIDERS.update(providers)
        if providers:
            logger.info("degraded_providers_loaded", count=len(providers))

    async def clear_degraded_providers(self) -> None:
        """清空降级记忆（内存 + SQLite）：换 base_url/模型后 provider 能力未知，需重新探测。"""
        _DEGRADED_PROVIDERS.clear()
        await save_degraded_providers([])
        logger.info("degraded_providers_cleared")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(settings.MAX_LLM_CALL_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _invoke_with_retry(self, llm: Any, messages: list[BaseMessage]) -> Any:
        """带 tenacity 重试地调一个 Runnable（只对临时性错误指数退避）。

        重试谓词（2026-08-29 收窄）：只重试限流/超时/连接中断/5xx 这类**换个时刻再试
        可能成功**的临时错误。4xx 客户端错误（BadRequestError 等）是**请求本身错了**，
        重试 100 次也一样——首次即穿透、抛给 _call_once 的降级钩子（json_schema 不支持
        时立刻降级 json_mode，不再白耗 3 次重试 ~10s）。之前的谓词写了宽泛的 APIError，
        把 BadRequestError 也网进去重试满 3 次，是「每次上传连撞三次墙」的根因。
        """
        try:
            response = await llm.ainvoke(messages)
            logger.debug("llm_call_successful")
            return response
        except (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError) as e:
            logger.warning(
                "llm_call_failed_retrying",
                error_type=type(e).__name__,
                error=str(e),
                exc_info=True,
            )
            raise
        except OpenAIError as e:
            logger.exception(
                "llm_call_failed",
                error_type=type(e).__name__,
                error=str(e),
            )
            raise

    def _build_chain(self, base: Any, response_format: Optional[Type[BaseModel]], method: str) -> Any:
        """按 method 构造（结构化）调用链。

        - response_format=None：原样返回 base（纯文本调用）。
        - json_schema：langchain 官方严格模式（schema 强制）。
        - json_mode：bind json_object + 宽松解析。不用 langchain 的 with_structured_output
          （内部 PydanticOutputParser 严格校验，字段名不一致即抛 500）——用 lenient parser
          归一化字段名（type→category 等）并兜底默认值。前置注入 JSON 输出指令
          （DeepSeek json_object 硬校验 prompt 须含 "json"，2026-08-20 曾因此 400）。
          注入与否只看「走没走到 json_mode 这个分支」——这里是结构化输出调用的唯一入口，
          能进来就说明调用方要 JSON（2026-09-11 去掉「嗅探 prompt 文本」的旧判据）。
        """
        if response_format is None:
            return base
        if method == "json_schema":
            return base.with_structured_output(response_format)
        return (
            _prepend_json_directive
            | base.bind(response_format={"type": "json_object"})
            | lenient_structured_parser(response_format)
        )

    async def _call_once(
        self,
        messages: list,
        model_name: Optional[str],
        response_format: Optional[Type[BaseModel]],
        model_kwargs: dict,
    ) -> Union[BaseMessage, BaseModel]:
        """单模型调用：建独立实例 → 重试 →（结构化不支持时）降级 json_mode 重试同实例。

        结构化降级：部分 OpenAI 兼容端点（DeepSeek）不支持 json_schema 严格模式，但支持
        json_object。structured output 因 response_format 不支持而 400 时，自动降级 json_mode
        重试同一实例，并记进 _DEGRADED_PROVIDERS（后续同 base_url 直接 json_mode）。
        """
        lc_messages = _normalize_messages(messages)
        base = LLMRegistry.get(model_name, **model_kwargs)

        method = "json_schema"
        provider_key = settings.OPENAI_BASE_URL or "default"
        # 记忆降级：该 base_url 已确认不支持 json_schema → 直接 json_mode，跳过无谓重试。
        if response_format is not None and provider_key in _DEGRADED_PROVIDERS:
            method = "json_mode"
            logger.info("structured_output_using_remembered_json_mode", provider=provider_key)

        try:
            return await self._invoke_with_retry(
                self._build_chain(base, response_format, method), lc_messages
            )
        except OpenAIError as e:
            # 降级钩子：仅当结构化调用因 response_format 不支持而 400、且还在 json_schema 档时，
            # 降级 json_mode 同实例重试（json_mode 失败后无法再降，两级到头）。
            if (
                response_format is not None
                and method == "json_schema"
                and isinstance(e, BadRequestError)
                and "response_format" in str(e).lower()
            ):
                _DEGRADED_PROVIDERS.add(provider_key)
                await save_degraded_providers(sorted(_DEGRADED_PROVIDERS))
                logger.warning(
                    "structured_output_degraded_to_json_mode",
                    model=model_name or settings.DEFAULT_LLM_MODEL,
                    provider=provider_key,
                    error=str(e),
                )
                try:
                    return await self._invoke_with_retry(
                        self._build_chain(base, response_format, "json_mode"), lc_messages
                    )
                except OpenAIError as e2:
                    logger.exception("llm_call_failed_after_degrade", error=str(e2))
                    raise LLMUnavailableError(detail=f"模型调用失败（已降级重试）：{e2}")
            logger.exception(
                "llm_call_failed_after_retries", model=model_name or settings.DEFAULT_LLM_MODEL, error=str(e)
            )
            raise LLMUnavailableError(detail=f"模型调用失败：{e}")


llm_service = LLMService()
