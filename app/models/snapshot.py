"""数据表模型：事实库快照（与简历文档同代，按 document_id 锚定）。

每次生成下一版简历时，给 user_facts 整表打一份 JSON 快照，document_id 对应当前
文档（唯一身份）。回滚文档时若用户选择"连事实一起回滚"，调对应文档快照还原事实库。
快照粒度先按"每次生成"；对话中途细粒度回滚留线头。
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class ResumeSnapshot(SQLModel, table=True):
    """一份 user_facts 整表快照，document_id 对应当前文档（唯一身份）。

    2026-08-12 身份锚定：按 **document_id**（id 唯一、永不复用），不按 version——
    version 是显示标签（软作废后可复用），同号复用会让按 version 打的快照互相覆盖失真。
    """

    __tablename__ = "resume_snapshots"

    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(index=True)  # 对应文档 id（唯一身份；旧列名 resume_version，init_db 迁移改名）
    facts_json: str = Field(default="[]")  # user_facts 整表 JSON（list[dict]）
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
