"""统一持久化图基建：LangGraph checkpointer 装配 + thread_id 派生（ADR 0012）。

改写（rewrite）与简历抽取（resume-parse）两条「挂起 → 人确认 → 重启恢复」链路共用
同一套持久化编排基建，不各造一份。

- **checkpointer**：`langgraph-checkpoint-sqlite` 的 `AsyncSqliteSaver`（图走 ainvoke，
  用 async 版）。checkpoint 存**独立文件**（`checkpoints.db`），与业务库（`career_nova.db`）
  分离——编排数据与业务数据生命周期不同，混在一个库会把「回滚文档」和「回滚编排状态」
  绑在一起。
- **thread_id 派生式**：`{graph}:{scope}`（单用户同一时刻一条在途 → 单值），**不落库、
  不管理**。重启恢复 = 按固定规则查 checkpointer 有没有挂起的，查到就接着确认。
- **checkpointer 属编排基础设施，不违反「Graph 不碰 DB」红线**——它是编译时注入的编排
  设施，节点既不 import 也不调它；真正写业务库的仍是 service 层（见 docs/adr/0012）。

生命周期：`init_checkpoint_store()`（lifespan 启动）→ `get_checkpointer()`（图编译时
注入）→ `close_checkpoint_store()`（lifespan 关闭）。业务库 `:memory:`（测试）时无文件
可派生 → 返回 None，图退回无 checkpointer（内存跑，行为与接入前一致）。
"""

from pathlib import Path

import aiosqlite
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.core.config import settings
from app.core.logging import logger

# 模块级装配单例：进程内一把连接。生产 lifespan 启动建、关闭释放；测试显式 reset。
_conn: aiosqlite.Connection | None = None
_saver: AsyncSqliteSaver | None = None


def checkpoint_db_path() -> str | None:
    """从业务库 DATABASE_URL 派生 checkpoint 独立文件路径（同目录，`checkpoints.db`）。

    业务库 `:memory:`（测试）无文件可派生 → 返回 None，checkpointer 退回 None。
    """
    url = settings.DATABASE_URL
    if ":memory:" in url:
        return None
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return None
    return str(Path(url[len(prefix):]).with_name("checkpoints.db"))


def _ensure_parent(path: str) -> None:
    """确保 checkpoint 文件父目录存在（数据目录可能首次启动才建）。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def thread_id(graph: str, scope: str = "current") -> str:
    """派生 thread_id：`{graph}:{scope}`。单用户单值，不落库不管理。"""
    return f"{graph}:{scope}"


def graph_config(graph: str, scope: str = "current") -> RunnableConfig:
    """给图 ainvoke 的 config（RunnableConfig）：注入派生 thread_id。

    无 checkpointer 时 thread_id 键无害（LangGraph 忽略未知 configurable 键，
    实测无 checkpointer 的图带 thread_id 正常跑）。
    """
    return {"configurable": {"thread_id": thread_id(graph, scope)}}


async def init_checkpoint_store() -> None:
    """装配 AsyncSqliteSaver（独立 checkpoint 文件），幂等。生产 lifespan 启动调用。

    不可派生文件（`:memory:`）→ 跳过（无 checkpointer，图内存跑）。
    """
    global _conn, _saver
    if _saver is not None:
        return
    path = checkpoint_db_path()
    if path is None:
        logger.info("checkpoint_store_disabled", reason="in_memory_business_db")
        return
    _ensure_parent(path)
    _conn = await aiosqlite.connect(path)
    _saver = AsyncSqliteSaver(_conn)
    await _saver.setup()  # 建 checkpoints/writes 表（幂等，内部自防重复）
    logger.info("checkpoint_store_initialized", checkpoint_path=path)


async def close_checkpoint_store() -> None:
    """释放 checkpoint 连接，幂等。生产 lifespan 关闭调用。

    关前先 `wal_checkpoint(TRUNCATE)`：checkpoint 库由 `AsyncSqliteSaver.setup()` 设成
    WAL 模式，且 saver **从不主动折叠**——不折的话内容常年悬在 `-wal` 里（实测可涨到数 MB），
    主库只剩几 KB 空壳，拷贝/备份会拿到不完整快照。折叠失败不阻断关闭（优化，非正确性）。
    """
    global _conn, _saver
    if _conn is None:
        return
    try:
        await _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        await _conn.commit()
        logger.info("checkpoint_wal_truncated")
    except Exception as e:  # noqa: BLE001 - 折叠失败不阻断关闭
        logger.warning("checkpoint_wal_truncate_failed", error=str(e))
    await _conn.close()
    _conn = None
    _saver = None
    logger.info("checkpoint_store_closed")


def get_checkpointer() -> AsyncSqliteSaver | None:
    """读当前装配的 checkpointer（未初始化返回 None）。图编译时注入。"""
    return _saver


async def reset_checkpoint_store() -> None:
    """测试复位：关闭并清空装配单例（与 extract_state.reset_state 同构）。"""
    await close_checkpoint_store()
