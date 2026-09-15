"""数据表模型：会话（chat_sessions / chat_messages）。

会话存储（1.2/1.3 共享底座，见 docs/design/resume.md §11）。纯 SQLite 表——本表是
**前端 UI 真相源**（原文 + 系统气泡，Append-only；ADR 0016）。对话 agent 的工作记忆是
graph 的 checkpointer（deepagents），二者是两份投影、同生共死。
每版本一个 session：版本变更（生成/回滚/应用建议）各开新 session，旧 session 只读。
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class ChatSession(SQLModel, table=True):
    """一个对话会话 = 一段"从版本 A 到版本变更"的对话。

    document_id = 会话开始时的当前文档 id（唯一身份；旧列名 document_version，
    init_db 迁移改名）；NULL = 种子会话（尚无简历时开始聊）。
    当前 session = id 最大的那个，无需状态字段。
    """

    __tablename__ = "chat_sessions"

    id: int | None = Field(default=None, primary_key=True)
    document_id: int | None = Field(default=None, index=True)  # NULL = 种子会话
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChatMessage(SQLModel, table=True):
    """会话内的一条消息（用户 / 助手回复 / 系统事件）。

    seq 保证会话内顺序——UI 显示当前 session、发送序列含当前 session，都靠它。
    role 可为 event（系统事件气泡，§11.3：前端渲染系统气泡、chat.py 注入前过滤）。
    """

    __tablename__ = "chat_messages"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    role: str = Field(default="user")  # user / assistant / system / event
    content: str = Field(default="")
    # 结构化动作类型（2026-08-20 §11.8）：仅 event 行用（upload/generated/rolled_back/…），
    # 阶段判定读它而非匹配中文文案（apply.md §7.3 铁律：不靠猜文案关键词）。非 event 行空串。
    kind: str = Field(default="")
    # 事件引用的文档 id（2026-08-21 §11.8，仅 event 行用）：rolled_back 记录「被覆盖稿」
    # 的 id——回滚引导要精确说出「上一版改了什么」，不能事后从 DB 反推（连续回滚时
    # 「id 最大的 superseded 稿」不是最近被覆盖的那版）。非 event 行 / 非回滚事件 NULL。
    ref_document_id: int | None = Field(default=None, index=True)
    seq: int = Field(default=0)  # 会话内顺序（从 1 递增）
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
