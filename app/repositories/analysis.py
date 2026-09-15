"""analysis_reports repository：投递页分析报告的读写（apply.md §11.7）。

唯一碰 `analysis_reports` 表的地方（分层红线）。不知道 LLM 存在——报告内容由 service
算好传进来（JSON 文本），这里只管存取。
"""

from datetime import UTC, datetime

from sqlmodel import select

from app.models.analysis import AnalysisReport
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
