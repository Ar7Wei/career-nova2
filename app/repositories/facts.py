"""user_facts repository：用户信息事实库的读写。唯一碰 user_facts 表的地方。

不知道 LLM 存在（分层红线）；返回 schemas 层的 Fact 模型（RORO），不裸表行。
2026-08-07 重构：嵌套模型——一条记录 = title（总条目）+ points（子要点数组），
层级在一条内表达，不再用 group_id 外键/负索引。
"""

import json

from datetime import UTC, datetime

from sqlmodel import select

from app.models import UserFact
from app.repositories.base import async_session_maker
from app.schemas.facts import Fact, FactCategory, FactCreate, FactStatus, FactUpdate


def _to_fact(row: UserFact) -> Fact:
    """表行 → 传输模型（points 从 JSON 列反序列化）。"""
    return Fact(
        id=row.id or 0,
        category=row.category,  # type: ignore[arg-type]
        title=row.title,
        points=json.loads(row.points) if row.points else [],
        source=row.source,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        on_resume=row.on_resume,
        occurred_at=row.occurred_at,
        updated_at=row.updated_at.isoformat(),
    )


async def create_facts(facts: list[FactCreate]) -> list[Fact]:
    """批量写入事实（确认入库 / 对话记录），返回带 id 的完整模型。

    每条 = title + points 一行存（points 序列化为 JSON 列）。无层级重建——
    嵌套在一条内表达。
    """
    if not facts:
        return []
    async with async_session_maker() as session:
        rows: list[UserFact] = []
        for f in facts:
            row = UserFact(
                category=f.category,
                title=f.title,
                points=json.dumps(f.points, ensure_ascii=False),
                source=f.source,
                status="active",
                on_resume=f.on_resume,
                occurred_at=f.occurred_at,
            )
            session.add(row)
            await session.flush()
            rows.append(row)
        await session.commit()
        for row in rows:
            await session.refresh(row)
        return [_to_fact(r) for r in rows]


async def list_facts(
    category: FactCategory | None = None,
    status: FactStatus | None = "active",
) -> list[Fact]:
    """按分类/状态过滤读事实（默认只读 active）。"""
    async with async_session_maker() as session:
        stmt = select(UserFact).order_by(UserFact.id)  # type: ignore[arg-type]
        if category is not None:
            stmt = stmt.where(UserFact.category == category)
        if status is not None:
            stmt = stmt.where(UserFact.status == status)
        result = await session.exec(stmt)
        return [_to_fact(r) for r in result.all()]


async def update_fact(fact_id: int, patch: FactUpdate) -> Fact | None:
    """部分更新一条事实；不存在返回 None。"""
    async with async_session_maker() as session:
        row = await session.get(UserFact, fact_id)
        if row is None:
            return None
        data = patch.model_dump(exclude_none=True)
        # points 列是 JSON 字符串（对齐 create_facts 的序列化），直塞 list 会让 SQLite 绑定报错
        if "points" in data:
            data["points"] = json.dumps(data["points"], ensure_ascii=False)
        for key, value in data.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(UTC)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_fact(row)


async def delete_fact(fact_id: int) -> bool:
    """删除一条事实；存在并删除返回 True，不存在返回 False。"""
    async with async_session_maker() as session:
        row = await session.get(UserFact, fact_id)
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        return True


async def delete_all_facts() -> int:
    """清空事实库（重置简历连事实时用）：删除全部行，返回删除行数。"""
    async with async_session_maker() as session:
        rows = (await session.exec(select(UserFact))).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)
