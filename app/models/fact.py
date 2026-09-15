"""数据表模型：用户信息事实库（user_facts）。

存"对用户的了解"——自然语言事实 + 基本分类，简历/优化/对话生成/市场调研共享的地基。
不用向量（那是 Chatter RAG 的事）；冲突不删旧行，标 superseded 留 history。
2026-08-07 重构：嵌套模型取代平铺+group_id——经历类事实一条 = title（总条目）+ points（子要点数组），
层级天然在一条内表达，不再靠负索引/父 id 外键。无 points = 单句事实（如 basic/skill）。
occurred_at 记事实发生的时间线（时间上的来源，用于回滚/对账）。
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class UserFact(SQLModel, table=True):
    """一条用户信息事实。category/title/points/source/status 见 app/schemas/facts.py。"""

    __tablename__ = "user_facts"

    id: int | None = Field(default=None, primary_key=True)
    category: str = Field(index=True)  # basic/education/work/projects/skill/other
    title: str = Field(default="")  # 总条目（如 "腾讯 后端工程师 2021-至今"）；单句事实也是它
    points: str = Field(default="[]")  # 子要点数组 JSON（如 ["订单系统开发", "性能优化40%"]）
    source: str = Field(default="manual")  # resume_upload/chat/manual
    status: str = Field(default="active", index=True)  # active/superseded
    on_resume: bool = Field(default=True)  # 该不该上简历（ADR 0011）；方向/敏感→False
    occurred_at: str | None = Field(default=None)  # 事实时间线（如 "2018-2021"），时间上的来源
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
