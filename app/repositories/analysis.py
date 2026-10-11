"""analysis_reports / analysis_proposal_state repository：投递页分析报告与提案状态的读写（apply.md §11.7）。

唯一碰这两张表的地方（分层红线）。不知道 LLM 存在——报告内容由 service 算好传进来（JSON 文本），
这里只管存取；提案状态（pending/applied/dismissed）也只在这里落库。
"""

from datetime import UTC, datetime

from sqlmodel import select

from app.models.analysis import AnalysisProposalState, AnalysisReport
from app.repositories.base import async_session_maker


async def get_report(scope: str, scope_key: str) -> tuple[str, str] | None:
    """读一份报告，返回 (content JSON 文本, created_at ISO) ；没有则 None。

    不返回"未就绪"行——表里没有该 (scope, key) = 还没算过，由调用方补算（§11.7.5）。
    """
    async with async_session_maker() as session:
        row = (
            await session.exec(
                select(AnalysisReport)
                .where(AnalysisReport.scope == scope)
                .where(AnalysisReport.scope_key == scope_key)
            )
        ).first()
        if row is None:
            return None
        created = row.created_at.replace(tzinfo=UTC) if row.created_at.tzinfo is None else row.created_at
        return row.content, created.isoformat()


async def save_report(scope: str, scope_key: str, content: str) -> None:
    """落一份报告（同 scope+key 已存在则覆盖——正常由 service 的"算一次存一次"保证不重算）。"""
    async with async_session_maker() as session:
        row = (
            await session.exec(
                select(AnalysisReport)
                .where(AnalysisReport.scope == scope)
                .where(AnalysisReport.scope_key == scope_key)
            )
        ).first()
        now = datetime.now(UTC)
        if row is None:
            row = AnalysisReport(scope=scope, scope_key=scope_key, content=content, created_at=now)
        else:
            row.content = content
            row.created_at = now
        session.add(row)
        await session.commit()


async def clear_all_reports() -> int:
    """清空全部分析报告（核爆用），返回删除行数。报告是上一段求职的派生缓存，核爆不继承。"""
    async with async_session_maker() as session:
        rows = (await session.exec(select(AnalysisReport))).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)


# ---------------------------------------------------------------------------
# 提案状态（analysis_proposal_state，2026-10-11）：报告内容不变、处置单存一张表
# ---------------------------------------------------------------------------


async def get_proposal_states(scope: str, scope_key: str) -> dict[str, str]:
    """读某轮某 scope 下所有提案的处置：`{proposal_kind: status}`。

    **无行 = pending**——只为「已执行 / 已忽略」写行，待办不必占行。缺省返回 `{}`
    （前端据此把每个提案都当 pending）。
    """
    async with async_session_maker() as session:
        rows = (
            await session.exec(
                select(AnalysisProposalState)
                .where(AnalysisProposalState.scope == scope)
                .where(AnalysisProposalState.scope_key == scope_key)
            )
        ).all()
        return {r.proposal_kind: r.status for r in rows}


async def set_proposal_state(scope: str, scope_key: str, proposal_kind: str, status: str) -> None:
    """落某类提案的处置（同 (scope, key, kind) 已存在则覆盖 status + 刷新 updated_at）。"""
    async with async_session_maker() as session:
        row = (
            await session.exec(
                select(AnalysisProposalState)
                .where(AnalysisProposalState.scope == scope)
                .where(AnalysisProposalState.scope_key == scope_key)
                .where(AnalysisProposalState.proposal_kind == proposal_kind)
            )
        ).first()
        now = datetime.now(UTC)
        if row is None:
            row = AnalysisProposalState(
                scope=scope, scope_key=scope_key, proposal_kind=proposal_kind, status=status, updated_at=now
            )
        else:
            row.status = status
            row.updated_at = now
        session.add(row)
        await session.commit()


async def clear_all_proposal_states() -> int:
    """清空全部提案状态（核爆用），返回删除行数。与报告同命——上一段求职的派生态，不继承。"""
    async with async_session_maker() as session:
        rows = (await session.exec(select(AnalysisProposalState))).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)
