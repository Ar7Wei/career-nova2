"""数据表模型：工作台快照（与简历文档同代，按 document_id 锚定）。

**2026-09-24 扩大为「工作台快照」**：原先只快照 user_facts，现在连 change_records 一起
（资料集 + 改动记录 = 这一版的工作台），回滚时一并还原。原语义「每次生成下一版时给被替换的
当前版本打」改为「**每版创建时给这一版打**」——快照 = **该版开始时的工作台**，
回滚到 vN 就恢复到「vN 刚生成」那一刻。
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class ResumeSnapshot(SQLModel, table=True):
    """一份工作台快照（facts + change_records），document_id 对应当前文档（唯一身份）。

    2026-08-12 身份锚定：按 **document_id**（id 唯一、永不复用），不按 version——
    version 是显示标签（软作废后可复用），同号复用会让按 version 打的快照互相覆盖失真。
    2026-09-24 加 records_json：快照 = 工作台（不只是资料集），回滚要连改动记录一起还原。
    """

    __tablename__ = "resume_snapshots"

    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(index=True)  # 对应文档 id（唯一身份；旧列名 resume_version，init_db 迁移改名）
    facts_json: str = Field(default="[]")  # user_facts 整表 JSON（list[dict]）
    # change_records 整表 JSON（list[dict]；2026-09-24 加）——旧行无此列为 NULL，回滚时
    # 按「不动改动记录」降级（旧快照只还原事实），不清空当前记录。
    records_json: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
