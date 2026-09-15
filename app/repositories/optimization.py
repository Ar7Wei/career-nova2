"""preferences / optimization_pending repository：用户判定偏好与优化建议的读写。

不知道 LLM 存在（分层红线）；返回 schemas 层模型（RORO），不裸表行。
- preferences：拒绝项（记"拒掉这一类"）+ 自定义标准（resume.md §5）。
- optimization_pending：建议落库（2026-08-10 升级），完整状态机——
  pending 待定 / confirmed 已确认 / rejected 已拒绝 / discussing 正在聊。
  suggest_improvements 产出即落库 pending；面板三栏直接读它；聊一聊/裁决更新状态。
"""

from datetime import UTC, datetime

from sqlmodel import select

from app.models.optimization import OptimizationPending, Preference
from app.repositories.base import async_session_maker
from app.schemas.optimization import PendingSuggestion, Preference as PreferenceSchema, Suggestion


def _to_preference(row: Preference) -> PreferenceSchema:
    """表行 → 传输模型。"""
    return PreferenceSchema(
        id=row.id or 0,
        kind=row.kind,
        scope=row.scope,
        content=row.content,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


def _to_pending(row: OptimizationPending) -> PendingSuggestion:
    """表行 → 传输模型。"""
    return PendingSuggestion(
        id=row.id or 0,
        type=row.type,  # type: ignore[arg-type]
        target=row.target,
        original=row.original,
        suggested=row.suggested,
        reason=row.reason,
        severity=row.severity,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        origin=row.origin,  # type: ignore[arg-type]
        document_id=row.document_id,
        resolved_in_document_id=row.resolved_in_document_id,
        split_from=row.split_from,
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


async def add_suggestion(
    session_id: int, document_id: int, s: Suggestion, *, status: str = "pending", origin: str = "agent"
) -> PendingSuggestion:
    """落库一条建议（suggest_improvements 产出 → status=pending）。document_id = 建议基于的文档 id。

    status（2026-09-14，apply.md §11.7.3）：投递页分析产的「处方」传 proposed（不进面板，
    点「改进」才转 pending）；聊天 agent 提的走默认 pending。
    origin（2026-09-14）：agent / job_analysis——分不清来源就分不清该不该带「改进」按钮。
    """
    async with async_session_maker() as session:
        now = datetime.now(UTC)
        row = OptimizationPending(
            session_id=session_id,
            document_id=document_id,
            type=s.type,
            target=s.target,
            original=s.original,
            suggested=s.suggested,
            reason=s.reason,
            severity=s.severity,
            status=status,
            origin=origin,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_pending(row)


async def get_suggestion(suggestion_id: int) -> PendingSuggestion | None:
    """按 id 读一条建议（裁决/聊一聊用），返回传输模型（RORO，不裸表行）。"""
    async with async_session_maker() as session:
        row = (
            await session.exec(
                select(OptimizationPending).where(OptimizationPending.id == suggestion_id)  # type: ignore[arg-type]
            )
        ).first()
        return _to_pending(row) if row is not None else None


async def update_suggestion_status(suggestion_id: int, status: str, *, reject_reason: str = "") -> None:
    """更新建议状态（proposed/pending/confirmed/rejected/discussing）。刷 updated_at（变化标记比对用）。

    reject_reason（2026-09-14）：仅 status=rejected 时有意义——拒绝理由落库（此前只 logger
    丢了，而"能力不到"这类拒因是方向降级的证据）。
    """
    async with async_session_maker() as session:
        row = (
            await session.exec(
                select(OptimizationPending).where(OptimizationPending.id == suggestion_id)  # type: ignore[arg-type]
            )
        ).first()
        if row is not None:
            row.status = status
            if reject_reason:
                row.reject_reason = reject_reason
            row.updated_at = datetime.now(UTC)
            await session.commit()


async def update_suggestion_content(suggestion_id: int, s: Suggestion, status: str = "pending") -> None:
    """更新建议内容（suggested/reason/target/original/type/severity），并置状态。

    2026-08-13：status 参数化——refine 裁决回 pending（旧行为）；proposal 确认
    （收进已确认）置 confirmed、继续聊置 discussing（保留草稿）。
    """
    async with async_session_maker() as session:
        row = (
            await session.exec(
                select(OptimizationPending).where(OptimizationPending.id == suggestion_id)  # type: ignore[arg-type]
            )
        ).first()
        if row is not None:
            row.type = s.type
            row.target = s.target
            row.original = s.original
            row.suggested = s.suggested
            row.reason = s.reason
            row.severity = s.severity
            row.status = status
            row.updated_at = datetime.now(UTC)
            await session.commit()


async def set_split_from(suggestion_id: int, split_from: int) -> None:
    """标记分裂来源（2026-08-10 裁决 split：新条 split_from = 原条 id）。"""
    async with async_session_maker() as session:
        row = (
            await session.exec(
                select(OptimizationPending).where(OptimizationPending.id == suggestion_id)  # type: ignore[arg-type]
            )
        ).first()
        if row is not None:
            row.split_from = split_from
            await session.commit()


async def list_suggestions(document_id: int | None = None, status: str | None = None, include_rejected: bool = False) -> list[PendingSuggestion]:
    """读建议列表；可按文档 id/状态过滤。

    2026-08-12：**默认不返回 rejected**（历史行为，面板四栏读取时显式
    include_rejected=True）——rejected 是"版本内可撤回"，仍要展示在灰栏。
    2026-09-14：**默认也不返回 proposed**——处方未点「改进」前不进面板四栏
    （按状态显式传 status="proposed" 可取）。活跃集（resolved IS NULL）自然只含活跃行。
    """
    async with async_session_maker() as session:
        stmt = select(OptimizationPending).order_by(OptimizationPending.id)  # type: ignore[arg-type]
        if document_id is not None:
            stmt = stmt.where(OptimizationPending.document_id == document_id)
        if status is not None:
            stmt = stmt.where(OptimizationPending.status == status)
        else:
            # 活跃 = 未结清（resolved_in_document_id 为空）；终态行（applied/archived）不在活跃集
            stmt = stmt.where(OptimizationPending.resolved_in_document_id.is_(None))  # type: ignore[union-attr]
            if not include_rejected:
                stmt = stmt.where(OptimizationPending.status != "rejected")
            stmt = stmt.where(OptimizationPending.status != "proposed")  # 处方不进面板（见上）
        rows = (await session.exec(stmt)).all()
        return [_to_pending(r) for r in rows]


async def list_applied_for_document(document_id: int) -> list[PendingSuggestion]:
    """查某版本「应用了哪些建议」（status=applied + resolved_in_document_id=该版）。

    §11.8「已生成/已回滚」引导说「这版改了什么」的数据源；不给 resume_documents 加
    changes 列，复用本表（一份数据两处用）。
    """
    async with async_session_maker() as session:
        stmt = (
            select(OptimizationPending)
            .where(OptimizationPending.status == "applied")
            .where(OptimizationPending.resolved_in_document_id == document_id)  # type: ignore[attr-defined]
            .order_by(OptimizationPending.id)  # type: ignore[arg-type]
        )
        rows = (await session.exec(stmt)).all()
        return [_to_pending(r) for r in rows]


async def clear_pending() -> int:
    """清空待执行建议（apply 后卡片清零）。返回删除行数。"""
    async with async_session_maker() as session:
        rows = (await session.exec(select(OptimizationPending))).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)


async def soft_settle_by_status(
    statuses: set[str],
    resolved_in_document_id: int,
    *,
    discard: bool = False,
) -> list[PendingSuggestion]:
    """版本变更统一结清（2026-08-20 软标记，取代物理删除）：按状态把活跃建议软标成终态，不删行。

    - confirmed → applied（已应用进新稿）；pending / rejected → archived（结清未应用）。
    - discard=True（回滚）：confirmed 也 → archived——回滚是**丢弃**这轮确认的改动，没应用进
      任何稿（目标稿是旧的上传稿），标 applied 会谎称「目标稿应用了这些改动」。
    - 记 resolved_in_document_id（在哪个版本被结清）——溯源：哪版产生/哪版结清。
    - discussing 不传即保留（聊一聊留到下一版本继续聊）。
    - **返回的行保留结清前的原状态**（pending/confirmed/rejected）——供 service 计数 +
      记拒绝偏好用；库里存的已是终态。这是「返回值 = 结清前快照」的刻意设计。

    与 clear_by_status（旧物理删）的区别：行全留存，仅状态迁移。
    """
    async with async_session_maker() as session:
        stmt = select(OptimizationPending).where(OptimizationPending.status.in_(statuses))  # type: ignore[attr-defined]
        # 只结清活跃的（已终态的不重复结清）
        stmt = stmt.where(OptimizationPending.resolved_in_document_id.is_(None))  # type: ignore[union-attr]
        rows = (await session.exec(stmt)).all()
        snapshot = [_to_pending(r) for r in rows]  # 先快照原状态
        for row in rows:
            if discard:
                row.status = "archived"  # 回滚：confirmed 也是被丢弃，不应用进任何稿
            else:
                row.status = "applied" if row.status == "confirmed" else "archived"
            row.resolved_in_document_id = resolved_in_document_id
        await session.commit()
        return snapshot


async def add_preference(kind: str, scope: str, content: str) -> None:
    """记一条用户判定偏好（拒绝项/自定义标准）。"""
    async with async_session_maker() as session:
        row = Preference(kind=kind, scope=scope, content=content)
        session.add(row)
        await session.commit()


async def list_preferences(kind: str | None = None) -> list[PreferenceSchema]:
    """读用户判定偏好；kind 可选过滤。返回传输模型（RORO，不裸表行）。"""
    async with async_session_maker() as session:
        stmt = select(Preference).order_by(Preference.id)  # type: ignore[arg-type]
        if kind is not None:
            stmt = stmt.where(Preference.kind == kind)
        rows = (await session.exec(stmt)).all()
        return [_to_preference(r) for r in rows]


async def clear_all_preferences() -> int:
    """清空全部用户判定偏好（核爆用），返回删除行数。

    偏好是上一段求职的判定痕迹（拒掉这一类/自定义标准），核爆=从头开始时不继承。
    """
    async with async_session_maker() as session:
        rows = (await session.exec(select(Preference))).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)
