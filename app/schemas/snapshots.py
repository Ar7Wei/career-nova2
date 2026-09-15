"""简历文档快照（resume_snapshots）传输模型。

快照是"事实库的一次性还原点"——与文档版本同代，回滚时调出来还原。
"""

from pydantic import BaseModel, Field

from app.schemas.facts import FactCategory


class SnapshotFact(BaseModel):
    """快照里的一条事实（从 user_facts 拷贝）。

    2026-08-07 重构：嵌套模型。层级在一条内（title + points），无需 id/group_id 外键。
    """

    model_config = {"extra": "ignore"}

    category: FactCategory
    title: str
    points: list[str] = Field(default_factory=list)
    source: str = "resume_upload"
    on_resume: bool = True  # 上简历维度（ADR 0011）：快照与事实库同代，回滚时一并还原
    occurred_at: str | None = None
    created_at: str = ""


class SnapshotRecord(BaseModel):
    """一份快照（读模型）。document_id = 对应文档（唯一身份）。"""

    model_config = {"extra": "ignore"}

    id: int
    document_id: int
    facts: list[SnapshotFact] = Field(default_factory=list)
    created_at: str = ""
