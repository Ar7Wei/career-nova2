"""optimization repository：优化点（change_records）的读写 + 旧表停用期的最小读口。

不知道 LLM 存在（分层红线）；返回 schemas 层模型（RORO），不裸表行。
- **change_records**（唯一活表，2026-09-23）：优化点 = 改动记录，`reason`（为什么）+
  `changes`（改什么，JSON 复合子项，各自带 id/status）。子项级操作、软结清、关键词查询都在这里。
- **optimization_pending / preferences**：**停用，待 DROP**。只保留「读一条 proposed 处方」
  与「软结清旧行」两个口（`promote_suggestion` 收录处方时用），其余读写函数已删。
"""

import json

from datetime import UTC, datetime

from sqlmodel import select

from app.models.optimization import ChangeRecord, OptimizationPending, Preference
from app.repositories.base import async_session_maker
from app.schemas.optimization import (
    ChangeItem,
    ChangeRecord as ChangeRecordSchema,
    PendingPrescription,
)


def _to_prescription(row: OptimizationPending) -> PendingPrescription:
    """旧表行 → 只读传输模型（「改进」收录期用）。"""
    return PendingPrescription(
        id=row.id or 0,
        type=row.type,  # type: ignore[arg-type]
        target=row.target,
        original=row.original,
        suggested=row.suggested,
        reason=row.reason,
        severity=row.severity,  # type: ignore[arg-type]
        status=row.status,
        origin=row.origin,  # type: ignore[arg-type]
        document_id=row.document_id,
        resolved_in_document_id=row.resolved_in_document_id,
    )


async def get_suggestion(suggestion_id: int) -> PendingPrescription | None:
    """按 id 读一条旧处方行（「改进」收录用）。**旧表停用**，只此一处读口。"""
    async with async_session_maker() as session:
        row = (
            await session.exec(
                select(OptimizationPending).where(OptimizationPending.id == suggestion_id)  # type: ignore[arg-type]
            )
        ).first()
        return _to_prescription(row) if row is not None else None
async def soft_settle_by_status(
    statuses: set[str],
    resolved_in_document_id: int,
    *,
    discard: bool = False,
    ids: set[int] | None = None,
) -> list[PendingPrescription]:
    """版本变更统一结清（2026-08-20 软标记，取代物理删除）：按状态把活跃建议软标成终态，不删行。

    ⚠️ **旧表 `optimization_pending` 已停用（2026-09-23）**——本函数只剩一个活调用：
    `promote_suggestion` 把一条 proposed 处方收录进 change_records 后、软结清原行。

    - confirmed → applied（已应用进新稿）；pending / rejected → archived（结清未应用）。
    - discard=True（回滚）：confirmed 也 → archived——回滚是**丢弃**这轮确认的改动，没应用进
      任何稿（目标稿是旧的上传稿），标 applied 会谎称「目标稿应用了这些改动」。
    - 记 resolved_in_document_id（在哪个版本被结清）——溯源：哪版产生/哪版结清。
    - discussing 不传即保留（聊一聊留到下一版本继续聊）。
    - ids 给了就只结清这些行（单行收录用），否则按状态全量结清。
    - **返回的行保留结清前的原状态**——供 service 计数 + 记拒绝偏好用；库里存的已是终态。

    与 clear_by_status（旧物理删）的区别：行全留存，仅状态迁移。
    """
    async with async_session_maker() as session:
        stmt = select(OptimizationPending).where(OptimizationPending.status.in_(statuses))  # type: ignore[attr-defined]
        # 只结清活跃的（已终态的不重复结清）
        stmt = stmt.where(OptimizationPending.resolved_in_document_id.is_(None))  # type: ignore[union-attr]
        if ids is not None:
            stmt = stmt.where(OptimizationPending.id.in_(ids))  # type: ignore[union-attr]
        rows = (await session.exec(stmt)).all()
        snapshot = [_to_prescription(r) for r in rows]  # 先快照原状态
        for row in rows:
            if discard:
                row.status = "archived"  # 回滚：confirmed 也是被丢弃，不应用进任何稿
            else:
                row.status = "applied" if row.status == "confirmed" else "archived"
            row.resolved_in_document_id = resolved_in_document_id
        await session.commit()
        return snapshot


async def clear_all_preferences() -> int:
    """清空全部旧判定偏好（核爆用），返回删除行数。

    `preferences` 表已停用（2026-09-23，见模块 docstring）——遗留数据在核爆时连壳清掉，
    免得上段求职的痕迹被新一段继承。读写接口已删（无调用方）。
    """
    async with async_session_maker() as session:
        rows = (await session.exec(select(Preference))).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)


# ---- 改动记录（change_records）：原因(why) + 改动点(what, 复合子项) ----


def _load_changes(raw: str) -> list[dict]:
    """JSON 列 → 子项 dict 列表（坏数据兜底为空，不抛）。"""
    try:
        data = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _dump_changes(items: list[dict]) -> str:
    """子项列表 → JSON 字符串（对齐 UserFact.points 的序列化，ensure_ascii=False）。"""
    return json.dumps(items, ensure_ascii=False)


def _to_change_record(row: ChangeRecord) -> ChangeRecordSchema:
    """表行 → 传输模型（changes 从 JSON 列反序列化）。"""
    items = [ChangeItem(**c) for c in _load_changes(row.changes)]
    return ChangeRecordSchema(
        id=row.id or 0,
        reason=row.reason,
        changes=items,
        status=row.status,  # type: ignore[arg-type]
        kind=row.kind,  # type: ignore[arg-type]
        origin=row.origin,
        document_id=row.document_id,
        resolved_in_document_id=row.resolved_in_document_id,
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


async def add_change_record(
    *,
    reason: str,
    changes: list[dict] | None = None,
    session_id: int = 0,
    document_id: int = 0,
    status: str = "pending",
    kind: str = "change",
    origin: str = "agent",
) -> ChangeRecordSchema:
    """落库一条改动记录（原因 + 可选子项）。子项 id 由本函数分配（记录内唯一，从 1 起）。

    子项 dict 字段：`{target, original, suggested}` 必带，`type`/`severity` 可选
    （缺省 structure/medium——供面板展示改哪类、多要紧）。
    """
    items: list[dict] = []
    for i, c in enumerate(changes or [], start=1):
        items.append({"id": i, "status": "pending", "type": "structure", "severity": "medium", **c})
    row = ChangeRecord(
        reason=reason,
        changes=_dump_changes(items),
        status=status,
        kind=kind,
        origin=origin,
        session_id=session_id,
        document_id=document_id,
    )
    async with async_session_maker() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_change_record(row)


async def get_change_record(record_id: int) -> ChangeRecordSchema | None:
    """按 id 读一条改动记录。"""
    async with async_session_maker() as session:
        row = await session.get(ChangeRecord, record_id)
        return _to_change_record(row) if row is not None else None


async def list_change_records(
    *,
    status: str | None = None,
    kind: str | None = None,
    origin: str | None = None,
    document_id: int | None = None,
    active_only: bool = False,
) -> list[ChangeRecordSchema]:
    """读改动记录列表。active_only=True 只取未结清（resolved_in_document_id IS NULL）。"""
    async with async_session_maker() as session:
        stmt = select(ChangeRecord).order_by(ChangeRecord.id)  # type: ignore[arg-type]
        if status is not None:
            stmt = stmt.where(ChangeRecord.status == status)
        if kind is not None:
            stmt = stmt.where(ChangeRecord.kind == kind)
        if origin is not None:
            stmt = stmt.where(ChangeRecord.origin == origin)
        if document_id is not None:
            stmt = stmt.where(ChangeRecord.document_id == document_id)
        if active_only:
            stmt = stmt.where(ChangeRecord.resolved_in_document_id.is_(None))  # type: ignore[union-attr]
        rows = (await session.exec(stmt)).all()
        return [_to_change_record(r) for r in rows]


async def update_change_item_status(record_id: int, change_id: int, status: str) -> bool:
    """子项级状态迁移（操作粒度 = 单条子项）。找不到记录/子项返回 False。"""
    async with async_session_maker() as session:
        row = await session.get(ChangeRecord, record_id)
        if row is None:
            return False
        items = _load_changes(row.changes)
        hit = False
        for it in items:
            if it.get("id") == change_id:
                it["status"] = status
                hit = True
                break
        if not hit:
            return False
        row.changes = _dump_changes(items)
        row.updated_at = datetime.now(UTC)
        session.add(row)
        await session.commit()
        return True


async def append_change_items(record_id: int, changes: list[dict]) -> ChangeRecordSchema | None:
    """把新子项 append 进已有记录（同原因不新开行）。新子项 id 接在现有最大 id 之后。"""
    async with async_session_maker() as session:
        row = await session.get(ChangeRecord, record_id)
        if row is None:
            return None
        items = _load_changes(row.changes)
        next_id = max((int(it.get("id", 0)) for it in items), default=0) + 1
        for c in changes:
            items.append({"id": next_id, "status": "pending", "type": "structure", "severity": "medium", **c})
            next_id += 1
        row.changes = _dump_changes(items)
        row.updated_at = datetime.now(UTC)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_change_record(row)


async def update_record_status(record_id: int, status: str) -> None:
    """记录级状态迁移（整条存废/兜底）。不存在则 no-op。"""
    async with async_session_maker() as session:
        row = await session.get(ChangeRecord, record_id)
        if row is None:
            return
        row.status = status
        row.updated_at = datetime.now(UTC)
        session.add(row)
        await session.commit()


async def query_change_records(keyword: str) -> list[ChangeRecordSchema]:
    """按关键词查历史改动记录（LIKE 匹配 reason + changes JSON，量小无需向量）。"""
    kw = f"%{keyword}%"
    async with async_session_maker() as session:
        stmt = (
            select(ChangeRecord)
            .where(ChangeRecord.reason.like(kw) | ChangeRecord.changes.like(kw))  # type: ignore[attr-defined]
            .order_by(ChangeRecord.id)  # type: ignore[arg-type]
        )
        rows = (await session.exec(stmt)).all()
        return [_to_change_record(r) for r in rows]


async def list_records_settled_in(document_id: int) -> list[ChangeRecordSchema]:
    """查「在指定版本被结清/应用」的改动记录（溯源"这版改了哪些点"，§11.8）。"""
    async with async_session_maker() as session:
        stmt = (
            select(ChangeRecord)
            .where(ChangeRecord.resolved_in_document_id == document_id)
            .order_by(ChangeRecord.id)  # type: ignore[arg-type]
        )
        rows = (await session.exec(stmt)).all()
        return [_to_change_record(r) for r in rows]


async def clear_all_change_records() -> int:
    """核爆：清空全部改动记录，返回删除行数。"""
    async with async_session_maker() as session:
        rows = (await session.exec(select(ChangeRecord))).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)


async def soft_settle_change_records(
    statuses: set[str],
    resolved_in_document_id: int,
    *,
    included: set[tuple[int, int]] | None = None,
) -> list[ChangeRecordSchema]:
    """版本变更统一结清（软标记）：活跃记录的**子项**按状态迁移 + 记录级 archived + 记 resolved_in_document_id。

    **子项级迁移**（2026-09-23，与「操作粒度 = 单条子项」对齐）：面板/agent 是在子项上操作的，
    故结清也必须逐子项——confirmed 子项 → applied（这版真应用了它），pending/rejected → archived。
    记录本身也标 archived（整条脱离活跃集）。

    **included（件 1，2026-09-24）**：本版**真进了图**的已确认改动点 `{(record_id, change_id)}`——
    confirmed 子项只有在集合里才标 `applied`，不在就标 `archived`。`applied` 的字面语义是
    「这版应用了它」：`generate_resume` 这条路**不带**已确认改动，旧实现不看带了什么、一律标
    applied，导致新版开场引导（读该版 resolved 记录）谎报「这版做了这些调整」。
    **None = 调用方不关心带了什么**（旧口径，全部 confirmed → applied）。

    ⚠️ **本函数只服务于「生成确认 / 开始改」**（前向版本变更）。**回滚不走这里**（2026-09-24）：
    回滚 = 整份恢复目标版开始时的工作台快照，改动记录连状态一起换回——旧的 `discard` 分支已删。

    只结清活跃（resolved IS NULL）的；返回结清前快照（供 service 计数 / 记"拒掉这一类"）。
    """
    async with async_session_maker() as session:
        stmt = select(ChangeRecord).where(ChangeRecord.resolved_in_document_id.is_(None))  # type: ignore[union-attr]
        rows = (await session.exec(stmt)).all()
        snapshot = [_to_change_record(r) for r in rows]
        now = datetime.now(UTC)
        for row in rows:
            items = _load_changes(row.changes)
            touched = False
            for it in items:
                st = str(it.get("status", ""))
                if st not in statuses:
                    continue
                touched = True
                if st == "confirmed":
                    # 没带进图的 confirmed 是「被结清但未应用」——跟 pending/rejected 同一下场。
                    took_it = included is None or (row.id or 0, int(it.get("id", 0))) in included
                    it["status"] = "applied" if took_it else "archived"
                else:
                    it["status"] = "archived"
            if not touched:
                continue  # 无子项命中（如只剩 discussing 的记录，或纯 decision）→ 保持活跃
            row.changes = _dump_changes(items)
            row.status = "archived"
            row.resolved_in_document_id = resolved_in_document_id
            row.updated_at = now
        await session.commit()
        return snapshot
