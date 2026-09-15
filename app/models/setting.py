"""数据表模型：全局设置（单行 JSON blob）。"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class Setting(SQLModel, table=True):
    """全局设置，单行存储整份 JSON（key 固定 'app'）。

    单行 blob 而非逐字段列：设置项会随功能边做边加，blob 不用改表结构。
    """

    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: str = Field(default="{}")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
