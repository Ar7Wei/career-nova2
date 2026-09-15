"""resume_documents repository：版本化简历文档的读写。唯一碰 resume_documents 表的地方。

不知道 LLM 存在（分层红线）；返回 schemas 层模型（RORO），不裸表行。
版本语义：**整张表 = 单用户的唯一简历文档流**（封闭通道，一次只有一份简历）。
每行是一版（version 从 1 递增，v1=上传转出，vN=生成/修改版）。
"当前" = version 最大的**非 superseded** 行；软作废回滚 = 目标稿之后全标
superseded（数据保留 UI 不显示），目标稿变当前，下一个版本 = 目标+1。
"""

from sqlalchemy import desc, func
from sqlmodel import select

from app.models.document import ResumeDocument
from app.repositories.base import async_session_maker
from app.schemas.documents import ResumeDocument as ResumeDocumentOut
from app.schemas.resume import Typography, typography_from_json, typography_to_json


def _to_out(row: ResumeDocument) -> ResumeDocumentOut:
    """表行 → 传输模型。typography 列优先（2026-09-02 四参数）；空则回退旧 scale 列（迁移期）。"""
    typography = typography_from_json(row.typography) if row.typography.strip() else Typography(scale=row.scale)
    return ResumeDocumentOut(
        id=row.id or 0,
        version=row.version,
        markdown=row.markdown,
        resume_json=row.resume_json,
        html=row.html,
        summary=row.summary,
        source=row.source,  # type: ignore[arg-type]
        typography=typography,
        original_name=row.original_name,
        original_ext=row.original_ext,
        superseded=row.superseded,
        created_at=row.created_at.isoformat(),
    )


async def latest_document() -> ResumeDocumentOut | None:
    """当前（最新）简历文档：version 最大的**非 superseded** 行。"""
    async with async_session_maker() as session:
        row = (
            await session.exec(
                select(ResumeDocument)
                .where(ResumeDocument.superseded == False)  # noqa: E712
                .order_by(desc(ResumeDocument.version), desc(ResumeDocument.id))  # type: ignore[arg-type,call-overload]
            )
        ).first()
        return _to_out(row) if row else None


async def list_versions() -> list[ResumeDocumentOut]:
    """列当前稿序列（非 superseded，v1→vN 升序）。软作废稿不显示（数据保留）。"""
    async with async_session_maker() as session:
        rows = (
            await session.exec(
                select(ResumeDocument)
                .where(ResumeDocument.superseded == False)  # noqa: E712
                .order_by(ResumeDocument.version)  # type: ignore[arg-type,call-overload]
            )
        ).all()
        return [_to_out(r) for r in rows]


async def list_all_versions() -> list[ResumeDocumentOut]:
    """列全部版本（含软作废稿，时间线聚合用）。"""
    async with async_session_maker() as session:
        rows = (await session.exec(select(ResumeDocument).order_by(ResumeDocument.version))).all()  # type: ignore[arg-type,call-overload]
        return [_to_out(r) for r in rows]


async def get_document_by_id(document_id: int) -> ResumeDocumentOut | None:
    """按 id 读一条文档（唯一身份锚定，2026-08-12）。

    回看按 id 精确取原件/文档——version 是显示标签（软作废后复用），
    id 才是唯一身份（同号稿可能有多条：一作废一当前）。
    """
    async with async_session_maker() as session:
        row = (
            await session.exec(
                select(ResumeDocument).where(ResumeDocument.id == document_id)  # type: ignore[arg-type]
            )
        ).first()
        return _to_out(row) if row else None


async def create_document(markdown: str, source: str, html: str = "", original_name: str = "", original_ext: str = "", summary: str = "", resume_json: str = "", typography: Typography | None = None) -> ResumeDocumentOut:
    """存一版文档：version = 已有非 superseded 最高版本 + 1（软作废后从目标+1 继续）。

    markdown：上传 v1 内容层（抽取输入）；生成版空。
    resume_json：生成版结构化真身（JSON-Resume + layout）；上传 v1 空。
    html：生成版渲染快照（固定模板从 resume_json 渲染）；上传 v1 空。
    summary：一句话版本简述（S8，Git 意味；上传 v1 规则名，LLM 产出顺带吐）。
    typography：排版自由度配置（2026-09-02 四参数挂版本）；None=默认 Typography；新生成稿由 service 传上一版。
    """
    typo = typography or Typography()
    async with async_session_maker() as session:
        max_ver = (
            await session.exec(
                select(func.max(ResumeDocument.version)).where(ResumeDocument.superseded == False)  # type: ignore[arg-type]  # noqa: E712
            )
        ).one_or_none() or 0
        row = ResumeDocument(version=int(max_ver) + 1, markdown=markdown, resume_json=resume_json, html=html, source=source, original_name=original_name, original_ext=original_ext, summary=summary, scale=typo.scale, typography=typography_to_json(typo))
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_out(row)


async def update_document_typography_and_html(document_id: int, typography: Typography, html: str) -> ResumeDocumentOut | None:
    """原子更新一版的 typography + 重渲染后的 html（排版自由度防抖回后端的落点，2026-09-02）。

    按 **id** 锚定（version 可复用，id 才是唯一身份）。目标不存在返回 None。
    typography 与 html 必须同写——typography 是排版参数、html 是用它渲染的产物，分开写会出现
    「参数变了 html 还是旧的」的中间态。不动 resume_json（内容层没变，只改排版）。
    """
    async with async_session_maker() as session:
        row = (
            await session.exec(
                select(ResumeDocument).where(ResumeDocument.id == document_id)  # type: ignore[arg-type]
            )
        ).first()
        if row is None:
            return None
        row.typography = typography_to_json(typography)
        row.scale = typography.scale  # 同步遗留列，保一致性（读侧已以 typography 为准）
        row.html = html
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_out(row)


async def soft_rollback(document_id: int) -> ResumeDocumentOut | None:
    """软作废回滚（按 document_id 身份锚定）：目标稿之后所有稿标 superseded，目标稿变当前。

    2026-08-12 A3：按 **id** 取目标 + 圈定作废范围，不按 version——version 是显示标签
    （软作废后可复用，同号一作废一当前两条），按 version 会撞错稿/圈错范围。
    id 单调递增，是可靠的「唯一身份」与「时间先后」。
    数据不删（session/快照/原件/建议全保留），UI 面板只显示非 superseded。
    目标稿不存在返回 None。
    """
    async with async_session_maker() as session:
        target = (
            await session.exec(select(ResumeDocument).where(ResumeDocument.id == document_id))  # type: ignore[arg-type]
        ).first()
        if target is None:
            return None
        # 目标稿之后（id 更大）的全部标 superseded（数据保留）
        later = (
            await session.exec(
                select(ResumeDocument).where(ResumeDocument.id > document_id, ResumeDocument.superseded == False)  # type: ignore[operator, call-overload]  # noqa: E712
            )
        ).all()
        for row in later:
            row.superseded = True
        # 目标稿回到当前（非 superseded）
        target.superseded = False
        await session.commit()
        await session.refresh(target)
        return _to_out(target)


async def latest_superseded() -> ResumeDocumentOut | None:
    """读「最近被软作废回滚覆盖的那版」：id 最大的 superseded 稿。

    §11.8 已回滚引导「这版改了什么」的数据源——回滚把当前稿（id 最大非 superseded）
    标成 superseded，这条就是被覆盖稿；查它的 applied 建议（list_applied_for_document）
    能说出「上一版改过哪些点、现在撤回了」。无 superseded 稿返回 None。
    """
    async with async_session_maker() as session:
        row = (
            await session.exec(
                select(ResumeDocument).where(ResumeDocument.superseded == True)  # noqa: E712
                .order_by(desc(ResumeDocument.id))  # type: ignore[arg-type]
            )
        ).first()
        return _to_out(row) if row else None


async def delete_all_documents() -> int:
    """清空文档流（重置简历用）：删除全部版本，返回删除行数。"""
    async with async_session_maker() as session:
        rows = (await session.exec(select(ResumeDocument))).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)


