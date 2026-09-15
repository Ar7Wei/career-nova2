"""聊天相关传输模型。"""

import re
from typing import List, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import BaseResponse


class Message(BaseModel):
    """对话消息。role 为 user/assistant/system/event，content 做基础安全校验。

    event = 系统气泡（上传/确认/回滚/错误等，见 services/sessions.py record_event_message、ADR 0017）。
    event 角色仅作 UI 区分（前端渲染系统气泡），不进 LLM 上下文（chat.py 注入前过滤）。

    2026-09-01（ADR 0017 系统气泡三档）：补 kind/ref_document_id 透传——仅 event 行有值，
    前端按 kind 判定呈现（error_*→错误红条 / 其余（含 upload）→事件灰条），不靠文案匹配。
    系统卡片档现无产品实例（「去生成」卡 2026-09-08 删，冲突卡 2026-09-09 改对话内反问）。
    非 event 行 kind 空串、ref_document_id None。
    """

    model_config = {"extra": "ignore"}

    role: Literal["user", "assistant", "system", "event"] = Field(..., description="消息发送者角色；event = 系统气泡")
    content: str = Field(..., description="消息内容", min_length=1, max_length=3000)
    kind: str = Field(default="", description="结构化动作类型（仅 event 行）：upload/generated/rolled_back/suggestion_hint/error_* 等，三档呈现判定用")
    ref_document_id: int | None = Field(default=None, description="事件引用的文档 id（仅 event 行；rolled_back = 被覆盖稿）")

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        """拒绝脚本注入与空字节。"""
        if re.search(r"<script.*?>.*?</script>", v, re.IGNORECASE | re.DOTALL):
            raise ValueError("Content contains potentially harmful script tags")
        if "\0" in v:
            raise ValueError("Content contains null bytes")
        return v


class ChatRequest(BaseModel):
    """聊天请求：一组对话消息。"""

    messages: List[Message] = Field(..., description="对话消息列表", min_length=1)


class ChatResponse(BaseResponse):
    """聊天响应：一组对话消息。"""

    messages: List[Message] = Field(..., description="对话消息列表")


class StreamResponse(BaseResponse):
    """流式聊天响应分片。"""

    content: str = Field(default="", description="当前分片内容")
    done: bool = Field(default=False, description="流是否结束")
