"""Repository 层地基：SQLite + aiosqlite 异步 engine/session（B4 拆分，2026-09-11）。

engine / session maker / SQL echo 监听 / 数据目录守卫住这里；迁移清单在 `_migrations.py`，
对外入口（init_db/health_check/dispose_engine）在 `__init__.py`。

这是唯一碰数据库的层，不知道 LLM 存在。本地单用户，单文件 SQLite，无需连接池调优。
"""

import time
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.logging import logger

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# SQL echo 降噪（2026-08-09）：内置 echo=True 每句 SQL 打 3-4 行（含 parameters、
# 重复的 BEGIN/ROLLBACK），DEBUG 下会把 Electron main.log 写爆（实测 327MB）。
# 改挂事件监听器，DEBUG 时输出紧凑单行（事件名 + 压缩 SQL + 耗时），且不记
# parameters——避免简历原文 / API key 等敏感内容进日志。
_sql_echo_enabled = settings.DEBUG
_sql_echo_start: dict[int, float] = {}


def _compact_sql(stmt: str) -> str:
    """把多行 SQL 压成一行（换行/连续空格 → 单空格）。"""
    return " ".join(stmt.split())


@event.listens_for(engine.sync_engine, "before_cursor_execute")
def _on_before_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    if not _sql_echo_enabled:
        return
    _sql_echo_start[id(context)] = time.perf_counter()


@event.listens_for(engine.sync_engine, "after_cursor_execute")
def _on_after_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    if not _sql_echo_enabled:
        return
    start = _sql_echo_start.pop(id(context), None)
    elapsed = round((time.perf_counter() - start) * 1000, 1) if start is not None else None
    # 只打 SELECT/INSERT/UPDATE/DELETE/PRAGMA 等实质语句，忽略空串/事务控制
    stmt = _compact_sql(statement)
    if not stmt:
        return
    if elapsed is not None:
        logger.debug("sql_echo", sql=stmt, ms=elapsed)
    else:
        logger.debug("sql_echo", sql=stmt)


def ensure_data_dir() -> None:
    """从 SQLite 连接串里取出文件路径，确保其父目录存在。"""
    prefix = "sqlite+aiosqlite:///"
    if settings.DATABASE_URL.startswith(prefix) and ":memory:" not in settings.DATABASE_URL:
        db_path = Path(settings.DATABASE_URL[len(prefix):])
        db_path.parent.mkdir(parents=True, exist_ok=True)


async def checkpoint_and_dispose(target_engine=None) -> None:
    """折叠 WAL 再释放连接：让主库文件自包含、`-wal` 归零。

    为什么需要：WAL 模式下主库文件**不随提交实时增长**——新页先追加进 `-wal`，只在
    「最后一个连接正常关闭 / 超过 1000 页阈值 / 显式 checkpoint」时才折回主库。而本地桌面
    应用的常态是**被强杀**（Electron `taskkill /F /T`），连接不会走到优雅关闭那条路，
    于是 WAL 不折叠：主库停在旧快照（可能只有几 KB 的壳），几个 MB 的最新数据全悬在
    `-wal` 里。这既让「拷贝数据库」变得脆（只拷主库等于拷空壳），也让关闭后的磁盘占用虚高。

    这里在 dispose 前显式做一次 `wal_checkpoint(TRUNCATE)`：把 WAL 全部折回主库并把
    `-wal` 文件截断为 0。非 WAL 库（业务库默认 delete 模式）上是安全的 no-op。

    失败不阻断关闭（连接已不可用 / 库只读等）——折叠是优化，不该让退出流程炸。

    Args:
        target_engine: 要折叠的 engine；默认全局 engine（测试可传自建 engine 避免碰单例）。
    """
    eng = target_engine if target_engine is not None else engine
    try:
        async with eng.begin() as conn:
            result = await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            row = result.fetchone()
            # (busy, log_pages, checkpointed_pages)；busy=1 表示有读者占用没折成。
            if row and row[0]:
                logger.warning("wal_checkpoint_busy", result=str(tuple(row)))
            else:
                logger.info("wal_checkpoint_done", result=str(tuple(row)) if row else None)
    except Exception as e:  # noqa: BLE001 - 折叠失败不阻断关闭，留痕即可
        logger.warning("wal_checkpoint_failed", error=str(e))
    await eng.dispose()
