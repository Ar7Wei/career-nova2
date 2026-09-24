"""settings repository：全局设置单行 JSON 的读写。唯一碰 settings 表的地方。"""

import json

from datetime import UTC, datetime

from sqlmodel import select

from app.models import Setting
from app.repositories.base import async_session_maker

_SETTINGS_KEY = "app"
# 应用元数据行（独立于用户偏好 app 行）：存「数据代次」这类**非用户配置**的内部状态。
# 不放 AppSettings JSON——那是用户偏好，PATCH 合并时可能被整份覆盖；epoch 是系统真相，
# 只增不减、与配置无关，物理隔离最稳。
_META_KEY = "app_meta"
# LLM 结构化输出降级记忆（2026-08-29）：存「已确认不支持 json_schema 的 base_url」清单。
# 这是 provider 能力，与进程无关、也不随核爆清理（reset_all 只删求职数据，settings 表原样
# 保留）——持久化后后端重启不丢，下次结构化调用直接 json_mode，不再从头撞 json_schema。
_DEGRADED_KEY = "llm_degraded"


async def get_settings_json() -> str | None:
    """读出整份设置 JSON；无记录时返回 None。"""
    async with async_session_maker() as session:
        result = await session.exec(select(Setting).where(Setting.key == _SETTINGS_KEY))
        row = result.first()
        return row.value if row else None


async def save_settings_json(value: str) -> None:
    """Upsert 单行设置 JSON。"""
    async with async_session_maker() as session:
        result = await session.exec(select(Setting).where(Setting.key == _SETTINGS_KEY))
        row = result.first()
        if row is None:
            row = Setting(key=_SETTINGS_KEY, value=value)
            session.add(row)
        else:
            row.value = value
            row.updated_at = datetime.now(UTC)
        await session.commit()


async def get_degraded_providers() -> list[str]:
    """读已确认不支持 json_schema 的 base_url 清单；无记录返回空列表。"""
    async with async_session_maker() as session:
        row = (await session.exec(select(Setting).where(Setting.key == _DEGRADED_KEY))).first()
        if row is None:
            return []
        try:
            value = json.loads(row.value)
            return value if isinstance(value, list) else []
        except (json.JSONDecodeError, ValueError):
            return []


async def save_degraded_providers(providers: list[str]) -> None:
    """Upsert 降级记忆 base_url 清单（JSON 数组）。"""
    async with async_session_maker() as session:
        row = (await session.exec(select(Setting).where(Setting.key == _DEGRADED_KEY))).first()
        value = json.dumps(providers)
        if row is None:
            row = Setting(key=_DEGRADED_KEY, value=value)
            session.add(row)
        else:
            row.value = value
            row.updated_at = datetime.now(UTC)
        await session.commit()


def _read_meta(row_value: str | None) -> dict:
    """解析 app_meta 行 JSON 为 dict；无记录/坏 JSON → 空 dict（各计数器走 .get 默认 0）。

    app_meta 一行存多个系统计数器（data_epoch 爬虫守卫、resume_epoch 简历世界代次）——
    读写都先合并再写回，**不整份覆盖**，否则两个 bump 会互相抹掉对方的键。
    """
    if row_value is None:
        return {}
    try:
        value = json.loads(row_value)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


async def _write_meta(meta: dict) -> None:
    """整份写回 app_meta 行（调用方负责先 _read_meta 合并好再传）。"""
    async with async_session_maker() as session:
        row = (await session.exec(select(Setting).where(Setting.key == _META_KEY))).first()
        value = json.dumps(meta)
        if row is None:
            row = Setting(key=_META_KEY, value=value)
            session.add(row)
        else:
            row.value = value
            row.updated_at = datetime.now(UTC)
        await session.commit()


async def get_data_epoch() -> int:
    """读数据代次（核爆计数器）；无记录 = 0（出厂态）。"""
    async with async_session_maker() as session:
        row = (await session.exec(select(Setting).where(Setting.key == _META_KEY))).first()
        meta = _read_meta(row.value if row else None)
        try:
            return int(meta.get("data_epoch", 0))
        except (ValueError, TypeError):
            return 0


async def bump_data_epoch() -> int:
    """数据代次 +1（核爆时调用）：把「世界被重置」这个信号立起来。

    爬虫（Electron）开抓时记下 epoch、循环检查点比对——发现变了说明开抓后数据被核爆，
    丢弃在途批次不回写。返回新代次。
    """
    async with async_session_maker() as session:
        row = (await session.exec(select(Setting).where(Setting.key == _META_KEY))).first()
        meta = _read_meta(row.value if row else None)
    new_epoch = int(meta.get("data_epoch", 0) or 0) + 1
    meta["data_epoch"] = new_epoch
    await _write_meta(meta)
    return new_epoch


async def get_resume_epoch() -> int:
    """读简历世界代次（聊天记忆隔离计数器，2026-09-24）；无记录 = 0（出厂态）。

    职责与 data_epoch 分开：data_epoch 管爬虫核爆守卫，resume_epoch 管「简历世界切换」——
    核爆（reset_all）与版本回滚（rollback）都升它。chat 的 thread_id 带这个代次，
    代次一变旧 thread 永不命中（agent 不会把上一段世界的工作记忆捞回来）。
    """
    async with async_session_maker() as session:
        row = (await session.exec(select(Setting).where(Setting.key == _META_KEY))).first()
        meta = _read_meta(row.value if row else None)
        try:
            return int(meta.get("resume_epoch", 0))
        except (ValueError, TypeError):
            return 0


async def bump_resume_epoch() -> int:
    """简历世界代次 +1（核爆/回滚时调用）。

    与 data_epoch 共存于 app_meta 行、读写合并，互不覆盖。返回新代次。
    """
    async with async_session_maker() as session:
        row = (await session.exec(select(Setting).where(Setting.key == _META_KEY))).first()
        meta = _read_meta(row.value if row else None)
    new_epoch = int(meta.get("resume_epoch", 0) or 0) + 1
    meta["resume_epoch"] = new_epoch
    await _write_meta(meta)
    return new_epoch
