"""resume_snapshots repository：工作台快照（资料集 + 改动记录）的读写。唯一碰 resume_snapshots 表的地方。

**2026-09-24 语义改判**：快照 = **某版开始时的工作台**（user_facts 全表 + change_records 全表），
在**该版创建时**打（`save_upload`）。回滚到 vN = 恢复 vN 开始时的工作台（直回不叠加——
「v5 开始时」已经包含 v4 交下来的东西）。
旧语义「建下一版前给被替换的当前版打」= 任期末，已废（回滚会多带任期内的变动）。
快照是一次性还原点，不是常驻数据源——还原后新事实/新记录照常写入。
"""

import json

from sqlmodel import select

from app.core.logging import logger
from app.models.optimization import ChangeRecord
from app.models.snapshot import ResumeSnapshot
from app.models.fact import UserFact
from app.repositories.base import async_session_maker
from app.schemas.snapshots import SnapshotChange, SnapshotFact, SnapshotRecord


def _to_out(row: ResumeSnapshot) -> SnapshotRecord:
    """表行 → 传输模型。records_json 为 NULL（升级前的旧快照）→ records=None。"""
    return SnapshotRecord(
        id=row.id or 0,
        document_id=row.document_id,
        facts=json.loads(row.facts_json),
        records=None if row.records_json is None else json.loads(row.records_json),
        created_at=row.created_at.isoformat(),
    )


async def create_snapshot(document_id: int) -> SnapshotRecord:
    """给该版打一份**工作台快照**（active facts 全表 + 全部改动记录）。在该版创建时调。

    按 **document_id**（唯一身份）锚定（2026-08-12 A3）——version 可复用会让同号
    快照互相覆盖失真，id 单调递增永不复用。
    同一文档可被重复打（幂等）：已存在该文档快照则先删再插——"最新一次打的"为准，
    get_snapshot 无歧义。快照记的是**活跃态**（未结清），供"还原到该版开始时的工作台"。
    """
    async with async_session_maker() as session:
        # 1) 资料集：active 行（superseded 历史不进快照——快照记的是"当时可用的"）
        fact_rows = (await session.exec(select(UserFact).where(UserFact.status == "active").order_by(UserFact.id))).all()  # type: ignore[arg-type]
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
            for r in fact_rows
        ]
        # 2) 改动记录：**全表**（含已结清）——id/子项状态/resolved 痕迹原样保住，恢复时按 id
        #    精确重建，§11.8「这版改了哪些点」不因回滚断链。
        record_rows = (await session.exec(select(ChangeRecord).order_by(ChangeRecord.id))).all()  # type: ignore[arg-type]
        records = [
            SnapshotChange(
                id=r.id or 0,
                reason=r.reason,
                changes=r.changes,
                status=r.status,
                kind=r.kind,
                origin=r.origin,
                session_id=r.session_id,
                document_id=r.document_id,
                resolved_in_document_id=r.resolved_in_document_id,
            )
            for r in record_rows
        ]
        # 幂等：同一文档只保留一份（最新打的为准）
        old = (await session.exec(select(ResumeSnapshot).where(ResumeSnapshot.document_id == document_id))).all()
        for o in old:
            await session.delete(o)
        record = ResumeSnapshot(
            document_id=document_id,
            facts_json=json.dumps([f.model_dump() for f in facts], ensure_ascii=False),
            records_json=json.dumps([c.model_dump() for c in records], ensure_ascii=False),
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


async def restore_records(records: list[SnapshotChange]) -> None:
    """把改动记录恢复成快照里的样子（**直回不叠加**）。

    语义（2026-09-24）：回滚到 vN = 恢复 vN 开始时的工作台——**当前全部记录先清掉**，
    再按快照里的 id/内容/子项状态重建。vN 之后新建的记录随之消失（不软标、不出现在面板），
    这正是「直接恢复快照」的含义；历史上已结清的行随快照一同回来，版本溯源不断。
    """
    async with async_session_maker() as session:
        for row in (await session.exec(select(ChangeRecord))).all():
            await session.delete(row)
        for c in records:
            session.add(
                ChangeRecord(
                    id=c.id,
                    reason=c.reason,
                    changes=c.changes,
                    status=c.status,
                    kind=c.kind,
                    origin=c.origin,
                    session_id=c.session_id,
                    document_id=c.document_id,
                    resolved_in_document_id=c.resolved_in_document_id,
                )
            )
        await session.commit()
        logger.info("snapshot_records_restored", count=len(records))


async def delete_all_snapshots() -> int:
    """清空快照（重置简历用）：删除全部快照，返回删除行数。"""
    async with async_session_maker() as session:
        rows = (await session.exec(select(ResumeSnapshot))).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)
