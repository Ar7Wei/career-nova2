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


async def get_data_epoch() -> int:
    """读数据代次（核爆计数器）；无记录 = 0（出厂态）。"""
    async with async_session_maker() as session:
        row = (await session.exec(select(Setting).where(Setting.key == _META_KEY))).first()
        if row is None:
            return 0
        try:
            return int(json.loads(row.value).get("data_epoch", 0))
        except (json.JSONDecodeError, ValueError, AttributeError):
            return 0


async def bump_data_epoch() -> int:
    """数据代次 +1（核爆时调用）：把「世界被重置」这个信号立起来。

    爬虫（Electron）开抓时记下 epoch、循环检查点比对——发现变了说明开抓后数据被核爆，
    丢弃在途批次不回写。返回新代次。
    """
    async with async_session_maker() as session:
        row = (await session.exec(select(Setting).where(Setting.key == _META_KEY))).first()
        current = 0
        if row is not None:
            try:
                current = int(json.loads(row.value).get("data_epoch", 0))
            except (json.JSONDecodeError, ValueError, AttributeError):
                current = 0
        new_epoch = current + 1
        value = json.dumps({"data_epoch": new_epoch})
        if row is None:
            row = Setting(key=_META_KEY, value=value)
            session.add(row)
        else:
            row.value = value
            row.updated_at = datetime.now(UTC)
        await session.commit()
        return new_epoch
