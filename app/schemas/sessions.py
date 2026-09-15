"""会话相关传输模型。

会话存储（每版本一 session，见 docs/design/resume.md §11）。UI 只显示当前 session
（干净轮回，§11.1 修订）、旧 session 走回看模式（§11bis）。聊天请求增量式——
客户端只发新消息，服务端持有唯一历史。
"""

from pydantic import BaseModel, Field

from app.schemas.chat import Message


class SessionCreate(BaseModel):
    """新建会话。document_id 省略 = 种子会话（尚无简历时开始聊）。"""

    document_id: int | None = Field(default=None, description="会话开始时的当前文档 id（唯一身份）；省略 = 种子会话")


class Session(BaseModel):
    """一个会话的读模型。"""

    model_config = {"extra": "ignore"}

    id: int
    document_id: int | None = None
    message_count: int = 0
    created_at: str = ""


class SessionsResponse(BaseModel):
    """会话列表（版本面板历史 session 只读查看用）。"""

    sessions: list[Session] = Field(default_factory=list)


class ChatMessageIn(BaseModel):
    """客户端发来的一条新消息。"""

    message: Message = Field(..., description="用户新消息")


class ChatRequestIn(BaseModel):
    """增量式聊天请求：当前 session + 一条新消息。

    session_id 省略时服务端新建种子会话。version_bump=True 表示版本变更后开新 session
    （由版本变更端点触发，前端一般不直接调）。
    """

    session_id: int | None = Field(default=None, description="当前会话；省略 = 新建种子会话")
    message: Message = Field(..., description="用户新消息")


class ChatSessionResponse(BaseModel):
    """增量式聊天响应：会话 id + 完整历史 + 本轮产出建议数。

    2026-08-10：建议组落库（suggest_improvements 产出即进面板），不再经此透传；
    suggestion_count = 面板待定 + 正在聊的建议数（前端提示「去面板查看」）。
    2026-08-12：preview_pending = 是否存在待确认的生成预览（agent 生成工具产出后
    前端据此显示「确认保存」入口）。
    2026-08-13：version_changed = 本轮 agent 是否写了新版本。2026-09-09（ADR 0015）起
    恒为 false——出稿/开始改都改走人门挂起态（不写库），确认落库走独立端点；字段保留仅契约兼容。
    2026-08-25（§12.6 D8）：proposal 字段删除——提议卡 + propose_suggestion 工具废除。
    2026-08-25（方向口头化）：direction_proposal 删除（方向卡片废除）；新增
    pending_disposal_count——agent commit 方向后待处置的未处理岗位数（>0 前端就地弹确认）。
    2026-08-30（方向跨页同步）：direction_changed——本轮 agent 是否落定了方向
    （commit_direction 后置位）；前端据此刷新 directionStore，让投递页检索控制台标签
    （关键词组数 · 城市）跨页/最小化也能同步。
    """

    session_id: int
    messages: list[Message] = Field(default_factory=list)
    suggestion_count: int = Field(default=0, description="建议面板未定论的建议数（待定 + 正在聊）")
    preview_pending: bool = Field(default=False, description="是否有待确认的生成预览")
    pending_disposal_count: int = Field(default=0, description="agent 落定方向后待处置的未处理岗位数（>0 前端就地弹岗位处置确认，2026-08-25）")
    direction_changed: bool = Field(default=False, description="本轮 agent 是否落定了方向（前端据此刷新 directionStore，2026-08-30）")
    version_changed: bool = Field(default=False, description="本轮 agent 是否写了新版本（2026-09-09 ADR 0015 起恒为 false——出稿走人门挂起、不写库）")


class MessagesResponse(BaseModel):
    """某 session 的完整消息列表（历史 session 只读查看 + 挂载恢复用）。"""

    messages: list[Message] = Field(default_factory=list)


class ChatStopRequest(BaseModel):
    """POST /chat/stop 请求（2026-09-09 撤回）。

    retract=True = 本次停止顺带撤回最后一条 user 消息（取消重新输入），原文回填输入框。
    缺省 False = 纯停止（兜底调用 cancelUpload/removeResume/loadChatHistory 复用）。
    """

    retract: bool = Field(default=False, description="停止后是否撤回最后一条 user 消息")


class ChatStopResponse(BaseModel):
    """POST /chat/stop 响应（2026-08-14 真停止；2026-09-09 加撤回）。"""

    stopped: bool = Field(default=False, description="是否确有在途对话被取消（False = 无在途，幂等）")
    retracted: bool = Field(default=False, description="是否成功撤回最后一条 user 消息（仅 retract=True 且未切 session 时为 True）")
    retracted_text: str = Field(default="", description="被撤回的原文（前端回填输入框用；未撤回为空）")
