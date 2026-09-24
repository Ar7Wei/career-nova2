"""工作台快照（resume_snapshots）传输模型。

快照 = **某版开始时的工作台**（资料集 + 改动记录）——与文档版本同代，回滚到 vN 时调出来还原。
2026-09-24：原「事实库还原点」扩大为工作台，并加 records；旧快照 records=None。
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


class SnapshotChange(BaseModel):
    """快照里的一条改动记录（从 change_records 拷整行，含 id、子项状态、结清溯源）。

    2026-09-24 加：恢复快照时按原 id 重建，子项 `status` 与 `resolved_in_document_id` 原样
    回来——「v5 开始时还没定论的点」回滚后仍是待定、能接着聊；历史上已结清的行（属于
    v1..v4 的溯源）也一并保住，§11.8「这版改了哪些点」不因回滚而断链。
    **全表都进快照**（含已结清）——少存已结清行会丢版本溯源。
    """

    model_config = {"extra": "ignore"}

    id: int
    reason: str = ""
    changes: str = "[]"  # 子项 JSON 列（原样搬，status 在内）
    status: str = "pending"
    kind: str = "change"
    origin: str = "agent"
    session_id: int = 0
    document_id: int = 0
    resolved_in_document_id: int | None = None


class SnapshotRecord(BaseModel):
    """一份快照（读模型）。document_id = 对应文档（唯一身份）。

    records=None（旧快照）→ 回滚降级为「只还原事实、不动改动记录」。
    """

    model_config = {"extra": "ignore"}

    id: int
    document_id: int
    facts: list[SnapshotFact] = Field(default_factory=list)
    records: list[SnapshotChange] | None = None
    created_at: str = ""
