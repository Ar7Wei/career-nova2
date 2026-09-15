"""resume_snapshots repository：事实库整表快照的读写。唯一碰 resume_snapshots 表的地方。

快照语义：每次生成下一版简历时，给 user_facts 打一份 JSON 快照，resume_version
对应当前文档版本。回滚文档时若选"连事实一起回滚"，调对应版本快照还原事实库。
快照是"一次性还原点"，不是常驻数据源——还原后新的事实照常写入。
"""

import json

from sqlmodel import select

from app.models.snapshot import ResumeSnapshot
from app.models.fact import UserFact
from app.repositories.base import async_session_maker
from app.schemas.snapshots import SnapshotFact, SnapshotRecord


def _to_out(row: ResumeSnapshot) -> SnapshotRecord:
    """表行 → 传输模型。"""
    return SnapshotRecord(
        id=row.id or 0,
        document_id=row.document_id,
        facts=json.loads(row.facts_json),
        created_at=row.created_at.isoformat(),
    )


async def create_snapshot(document_id: int) -> SnapshotRecord:
    """打一份当前 user_facts 的整表快照（active 行；superseded 历史不进快照）。

    按 **document_id**（唯一身份）锚定（2026-08-12 A3）——version 可复用会让同号
    快照互相覆盖失真，id 单调递增永不复用。
    同一文档可被重复打（幂等）：已存在该文档快照则先删再插——"最新一次打的"为准，
    get_snapshot 无歧义。快照的意义是"还原到该文档的可用事实"，历史行不参与。
    """
    async with async_session_maker() as session:
        stmt = select(UserFact).where(UserFact.status == "active").order_by(UserFact.id)  # type: ignore[arg-type]
        rows = (await session.exec(stmt)).all()
        facts = [
            SnapshotFact(
                category=r.category,  # type: ignore[arg-type]
                title=r.title,
                points=json.loads(r.points) if r.points else [],
                source=r.source,  # type: ignore[arg-type]
                on_resume=r.on_resume,
                occurred_at=r.occurred_at,
                created_at=r.created_at.isoformat(),
            )
            for r in rows
        ]
        # 幂等：同一文档只保留一份（最新打的为准）
        old = (await session.exec(select(ResumeSnapshot).where(ResumeSnapshot.document_id == document_id))).all()
        for o in old:
            await session.delete(o)
        record = ResumeSnapshot(
            document_id=document_id,
            facts_json=json.dumps([f.model_dump() for f in facts], ensure_ascii=False),
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return _to_out(record)


async def get_snapshot(document_id: int) -> SnapshotRecord | None:
    """按文档 id 取快照（若存在）。"""
    async with async_session_maker() as session:
        row = (
            await session.exec(
                select(ResumeSnapshot)
                .where(ResumeSnapshot.document_id == document_id)
                .order_by(ResumeSnapshot.id)  # type: ignore[arg-type]
            )
        ).first()
        return _to_out(row) if row else None


async def delete_all_snapshots() -> int:
    """清空快照（重置简历用）：删除全部快照，返回删除行数。"""
    async with async_session_maker() as session:
        rows = (await session.exec(select(ResumeSnapshot))).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)
