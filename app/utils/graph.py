"""LangGraph 消息处理纯函数工具。已去除 tiktoken 依赖与 token 裁剪（本轮不做 trim）。"""

import re

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.core.logging import logger
from app.schemas import Message


def messages_to_lc(messages: list[Message]) -> list[BaseMessage]:
    """唯一消息转换器：对话 Message → LangChain 消息。

    收窄契约（2026-08-26）：Message 语义 = 对话消息（用户/助手），转换器只认这两种角色。
    - user → HumanMessage；assistant → AIMessage（必须是具体子类，裸 BaseMessage(type="ai")
      在 langchain-openai 转换 payload 时抛 "Got unknown type"，2026-08-07 坑）。
    - event（系统气泡）：不进 LLM 上下文，调用方（chat.py）注入前已滤掉；传到这 = 调用方
      漏滤，抛 ValueError 出声，不静默兜底成 SystemMessage。
    - system：system prompt 由调用方直接造 SystemMessage 注入，不伪装成对话
      Message 过转换器；传到这 = 误用，抛 ValueError。
    - 内部一次性 LLM prompt：不过本转换器，直接造 HumanMessage（content 常超 Message 的
      3000 字对话护栏，本就不该走对话消息类型）。
    """
    out: list[BaseMessage] = []
    for m in messages:
        if not isinstance(m, Message):
            raise ValueError(f"messages_to_lc 只接受 pydantic Message，got {type(m).__name__}")
        if m.role == "user":
            out.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            out.append(AIMessage(content=m.content))
        else:
            raise ValueError(
                f"messages_to_lc 只转换 user/assistant 对话消息，got role={m.role!r}"
            )
    return out


# 版本名标记：LLM 产出内容时顺带在正文前给一句话版本名（S8，2026-08-14），
# 用 <summary>...</summary> 包住——尖括号标记在 Markdown 正文里罕见，不冲突。
_SUMMARY_RE = re.compile(r"<summary>\s*(.*?)\s*</summary>", re.DOTALL)


def split_summary(text: str) -> tuple[str, str]:
    """从 LLM 输出里剥出 <summary>版本名</summary> 和正文。

    返回 (summary, body)。无标记（模型没按格式吐）→ summary 空串、body 原样，
    由调用方回退到规则名——坏格式不崩、不污染正文。
    """
    m = _SUMMARY_RE.search(text)
    if not m:
        return "", text.strip()
    summary = m.group(1).strip()
    body = (text[: m.start()] + text[m.end():]).strip()
    return summary, body


def extract_text_content(content: str | list) -> str:
    """从 LLM content 中提取纯文本。

    兼容简单字符串与 GPT-5/Responses API 的结构化块列表：
    [{'type': 'reasoning', ...}, {'type': 'text', 'text': '...'}]
    """
    if isinstance(content, str):
        return content

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "reasoning":
                logger.debug("reasoning_block_received", reasoning_id=block.get("id"), has_summary=bool(block.get("summary")))
    return "".join(parts)


def process_llm_response(response: BaseMessage) -> BaseMessage:
    """把原始 LLM 响应的 content 归一化为纯字符串。"""
    if isinstance(response.content, list):
        response.content = extract_text_content(response.content)
        logger.debug("processed_structured_content", extracted_length=len(response.content))
    return response
