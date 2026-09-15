"""crawl_state repository：抓取进度（游标 + 组级三态）的唯一读写处。

apply.md §8 / ADR 0008：续页游标 + 单平台三态统一存后端 SQLite——猎聘（后端 HTTP）
与前程无忧（Electron 读 DOM）两边只报结果，后端统一记账推进游标 / 标三态。

本层是纯数据读写，不知道 LLM / connector 存在；游标「存平台原生页号」，不碰平台
翻页语义。「回第 1 页」的起点由 PAGE_ORIGIN 定义（猎聘 currentPage 0 起、前程无忧
pageNum 1 起）。组级三态（exhausted_date / throttled_at）的**何时判、怎么比**是
service 层职责，本层只存 groups 列表原样。
"""

import json
from datetime import UTC, datetime

from sqlmodel import select

from app.models.crawl_state import CrawlState
from app.repositories.base import async_session_maker
from app.schemas.crawl_state import CrawlCursor, CrawlGroupState

# 各平台「回第 1 页」的原生起点页号（猎聘 currentPage 0 起 / 前程无忧 pageNum 1 起）。
PAGE_ORIGIN: dict[str, int] = {"liepin": 0, "job51": 1}


async def get_crawl_state(source: str) -> CrawlCursor | None:
    """读某平台游标 + 组级三态；无记录返回 None。

    返回定型的 CrawlCursor（group_idx / page / groups），不裸 dict（RORO）。
    groups 每项 = CrawlGroupState（exhausted_date / throttled_at），长度 = 方向查询组数。
    """
    async with async_session_maker() as session:
        row = await session.get(CrawlState, source)
        if row is None:
            return None
        raw_groups = json.loads(row.groups) if row.groups else []
        return CrawlCursor(
            group_idx=row.group_idx,
            page=row.page,
            groups=[CrawlGroupState.model_validate(g) for g in raw_groups],
        )


async def set_crawl_state(source: str, group_idx: int, page: int, groups: list[CrawlGroupState]) -> None:
    """Upsert 游标 + 组级三态。groups 长度 = 方向查询组数（由 service 层按方向对齐）。"""
    async with async_session_maker() as session:
        row = await session.get(CrawlState, source)
        groups_json = json.dumps([g.model_dump() for g in groups], ensure_ascii=False)
        if row is None:
            row = CrawlState(source_name=source, group_idx=group_idx, page=page, groups=groups_json)
            session.add(row)
        else:
            row.group_idx = group_idx
            row.page = page
            row.groups = groups_json
            row.updated_at = datetime.now(UTC)
        await session.commit()


async def reset_crawl_state(source: str) -> None:
    """改方向：游标归零 + 清组级三态（groups 清空，下轮按新方向组数重建）。"""
    async with async_session_maker() as session:
        row = await session.get(CrawlState, source)
        if row is None:
            return
        row.group_idx = 0
        row.page = PAGE_ORIGIN.get(source, 0)
        row.groups = "[]"
        row.updated_at = datetime.now(UTC)
        await session.commit()


async def clear_all_crawl_state() -> int:
    """核爆：清空全部抓取进度（当求职数据随 reset 一起清，不归「配置保留」）。返回删除行数。"""
    async with async_session_maker() as session:
        rows = (await session.exec(select(CrawlState))).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)
