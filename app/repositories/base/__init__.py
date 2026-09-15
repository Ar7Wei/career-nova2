"""Repository 层地基的对外入口（B4 拆分，2026-09-11）。

engine / async_session_maker 在此 re-export——各 repository 模块的
`from app.repositories.base import engine` 等既有写法不受拆分影响。
"""

from sqlmodel import SQLModel, select

from app.core.config import settings
from app.core.logging import logger
from app.repositories.base._engine import async_session_maker, checkpoint_and_dispose, ensure_data_dir, engine
from app.repositories.base._migrations import run_migrations

__all__ = [
    "async_session_maker",
    "dispose_engine",
    "engine",
    "health_check",
    "init_db",
]


async def init_db() -> None:
    """建表（新表 create_all；已有表不重建）+ 跑迁移清单。"""
    ensure_data_dir()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await run_migrations()
    logger.info("database_initialized", database_url=settings.DATABASE_URL)


async def health_check() -> bool:
    """健康检查：能执行 SELECT 1 即视为可用。"""
    try:
        async with async_session_maker() as session:
            await session.exec(select(1))
        return True
    except Exception as e:
        logger.exception("database_health_check_failed", error=str(e))
        return False


async def dispose_engine() -> None:
    """释放连接（应用关闭时调用）。先折叠 WAL 让主库自包含（见 checkpoint_and_dispose）。"""
    await checkpoint_and_dispose(engine)
