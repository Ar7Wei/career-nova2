"""迁移清单：声明式「旧结构 → 新结构」的补列/改名/重建。

`init_db` 只做建表 + 按这里的顺序跑一遍。把补列从 17 个几乎雷同的 `_migrate_*`
函数收敛成 `_ADD_COLUMNS` 数据表（B4，2026-09-11）；结构性的（重建表 / 改名 /
DROP 表 / 数据回填）仍写函数，因为动作类型不同，强行统一反而不清晰。

铁律：迁移只增不减语义——**DDL 与顺序必须与重构前逐字等价**，靠
tests/test_repository.py 的「旧结构库 → init_db → 新结构」回归兜底。
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlmodel import SQLModel

from app.core.logging import logger
from app.repositories.base._engine import engine

# 「纯补列」迁移表（顺序敏感：同一表内按时间追加；不同表互不依赖，任意序等价）。
# 形式：{表: [(列名, 完整 DDL)]}——DDL 原样保留各自 DEFAULT，语义一字不改。
_ADD_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "resume_documents": [
        ("original_name", "ALTER TABLE resume_documents ADD COLUMN original_name TEXT DEFAULT ''"),
        ("original_ext", "ALTER TABLE resume_documents ADD COLUMN original_ext TEXT DEFAULT ''"),
        ("superseded", "ALTER TABLE resume_documents ADD COLUMN superseded BOOLEAN DEFAULT 0"),
        ("html", "ALTER TABLE resume_documents ADD COLUMN html TEXT DEFAULT ''"),
        ("summary", "ALTER TABLE resume_documents ADD COLUMN summary TEXT DEFAULT ''"),
        ("resume_json", "ALTER TABLE resume_documents ADD COLUMN resume_json TEXT DEFAULT ''"),
        ("scale", "ALTER TABLE resume_documents ADD COLUMN scale REAL DEFAULT 1.0"),
        ("typography", "ALTER TABLE resume_documents ADD COLUMN typography TEXT DEFAULT ''"),
    ],
    "jobs": [
        ("description", "ALTER TABLE jobs ADD COLUMN description TEXT DEFAULT ''"),
        # 2026-09-14 投递页分析（apply.md §11.7.8）：
        ("jd_status", "ALTER TABLE jobs ADD COLUMN jd_status TEXT DEFAULT 'none'"),
        ("crawl_round_id", "ALTER TABLE jobs ADD COLUMN crawl_round_id INTEGER NULL"),
        ("found_by_query", "ALTER TABLE jobs ADD COLUMN found_by_query TEXT DEFAULT ''"),
    ],
    "optimization_pending": [
        ("status", "ALTER TABLE optimization_pending ADD COLUMN status TEXT DEFAULT 'confirmed'"),
        ("split_from", "ALTER TABLE optimization_pending ADD COLUMN split_from INTEGER NULL"),
        ("resolved_in_document_id", "ALTER TABLE optimization_pending ADD COLUMN resolved_in_document_id INTEGER"),
        # 2026-09-14 投递页分析（apply.md §11.7.8）：来源（agent / job_analysis）+ 状态变更时间 + 拒因。
        ("origin", "ALTER TABLE optimization_pending ADD COLUMN origin TEXT DEFAULT 'agent'"),
        ("updated_at", "ALTER TABLE optimization_pending ADD COLUMN updated_at TIMESTAMP NULL"),
        ("reject_reason", "ALTER TABLE optimization_pending ADD COLUMN reject_reason TEXT DEFAULT ''"),
    ],
    "chat_messages": [
        ("kind", "ALTER TABLE chat_messages ADD COLUMN kind TEXT DEFAULT ''"),
        ("ref_document_id", "ALTER TABLE chat_messages ADD COLUMN ref_document_id INTEGER"),
    ],
    "user_facts": [
        ("occurred_at", "ALTER TABLE user_facts ADD COLUMN occurred_at TEXT NULL"),
        ("on_resume", "ALTER TABLE user_facts ADD COLUMN on_resume BOOLEAN DEFAULT 1"),
    ],
}


async def _existing_cols(conn: AsyncConnection, table: str) -> set[str]:
    """读一张表现有列名（PRAGMA table_info 的第 1 列）——各迁移判「列在不在」的唯一入口。"""
    rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
    return {row[1] for row in rows}


async def _add_missing_columns() -> None:
    """按 _ADD_COLUMNS 逐表补缺列（幂等：只补 PRAGMA 里没有的；已有列不动）。"""
    async with engine.begin() as conn:
        for table, columns in _ADD_COLUMNS.items():
            existing = await _existing_cols(conn, table)
            for column, ddl in columns:
                if column not in existing:
                    await conn.execute(text(ddl))
                    logger.info("migration_added_column", table=table, column=column)


async def _backfill_facts_on_resume() -> None:
    """回填方向槽位（幂等：只把仍为 True 的方向槽位行置 False）。

    2026-09-06 ADR 0011：方向槽位（目标岗位：/目标城市：）本就不进成品，升级前靠
    `_DIRECTION_FACT_PREFIXES` 特例豁免，升级后改由 on_resume=False 表达——存量方向
    槽位行回填为 False，行为无缝。与 _ADD_COLUMNS 里 user_facts.on_resume 补列配套。
    """
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE user_facts SET on_resume = 0 "
                "WHERE (title LIKE '目标岗位：%' OR title LIKE '目标城市：%') AND on_resume = 1"
            )
        )


async def _rebuild_user_facts_if_flat() -> None:
    """轻量迁移：user_facts 表结构演进（2026-08-07 嵌套模型取代平铺 + group_id）。

    - 若表仍是旧结构（有 content/group_id 列、无 title）→ 重建表（本地单用户、数据量小，
      且旧数据本就是候选未入库，重建最干净）。
    - 若表已是新结构（有 title 列）→ 不动。
    必须在 _ADD_COLUMNS 之前跑：旧结构没有 occurred_at/on_resume，重建后 create_all 建全，
    补列步骤自然跳过。幂等：只在新旧结构不一致时重建。
    """
    async with engine.begin() as conn:
        existing = await _existing_cols(conn, "user_facts")
        has_old = "content" in existing or "group_id" in existing
        has_new = "title" in existing
        if has_old and not has_new:
            await conn.execute(text("DROP TABLE user_facts"))
            await conn.run_sync(SQLModel.metadata.create_all)
            logger.info("migration_rebuilt_user_facts", reason="flat_group_id_to_nested_title_points")


async def _migrate_optimization_columns() -> None:
    """轻量迁移：optimization_pending 建议落库升级（2026-08-10）——accepted_at → created_at 改名。

    旧表只有「已接受」语义（accepted_at 列），模型列名改为 created_at。缺 status/split_from
    的补列在 _ADD_COLUMNS（新库 created_at 已在，本函数无操作）。幂等：只在新旧结构不一致时改名。
    """
    async with engine.begin() as conn:
        existing = await _existing_cols(conn, "optimization_pending")
        if "accepted_at" in existing and "created_at" not in existing:
            await conn.execute(text("ALTER TABLE optimization_pending RENAME COLUMN accepted_at TO created_at"))
            logger.info("migration_renamed_column", table="optimization_pending", from_col="accepted_at", to_col="created_at")


async def _migrate_id_anchor_columns() -> None:
    """身份锚定迁移（2026-08-12，A3）：version（可复用显示标签）→ document_id（唯一身份）。

    version 软作废后可复用（同号一作废一当前两条），一切"按键取数"的写操作必须按 id。
    三张表的 version 语义列迁为 document_id：
    - `resume_snapshots.resume_version` → `document_id`：**快照表直接重建**（旧快照按可复用
      version 打的，本身就可能因同号复用而失真，作废旧快照最干净——快照是还原点，丢了
      最坏是旧稿不能连事实还原，不影响文档本体）。
    - `chat_sessions.document_version` → `document_id`：RENAME 保留数据（旧值是 version，
      仅作"会话开始时的稿"展示标签，不映射成 id——历史标签无害）。
    - `optimization_pending.document_version` → `document_id`：同上 RENAME 保留。
    幂等：只在新结构缺失时操作。
    """
    async with engine.begin() as conn:
        # 快照表：有旧 resume_version 且无 document_id → 重建（作废旧快照）
        existing = await _existing_cols(conn, "resume_snapshots")
        if "resume_version" in existing and "document_id" not in existing:
            await conn.execute(text("DROP TABLE resume_snapshots"))
            await conn.run_sync(SQLModel.metadata.create_all)
            logger.info("migration_rebuilt_resume_snapshots", reason="resume_version_to_document_id")

        # 会话/建议表：列改名保留数据（旧 version 值仅作展示标签，不映射 id）
        for table in ("chat_sessions", "optimization_pending"):
            existing = await _existing_cols(conn, table)
            if "document_version" in existing and "document_id" not in existing:
                await conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN document_version TO document_id"))
                logger.info("migration_renamed_column", table=table, from_col="document_version", to_col="document_id")


async def _drop_job_screening() -> None:
    """投递跟进定稿迁移（2026-08-30）：删除已作废的 job_screening 表。

    旧 job_screening 是「投了/没投布尔 + 6 值原因」，被 job_followup_events 状态时间线
    取代（apply.md §12.6「清空重来、不迁移」——本地单用户历史反馈量小，不值迁移）。
    create_all 只建新表不删旧表，这里显式 DROP 避免旧表残留孤儿数据误导。
    幂等：表不存在则跳过。
    """
    async with engine.begin() as conn:
        rows = (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='job_screening'"))).fetchall()
        if rows:
            await conn.execute(text("DROP TABLE job_screening"))
            logger.info("migration_dropped_table", table="job_screening", reason="superseded_by_job_followup_events")


async def _drop_boss_jobs() -> None:
    """BOSS 彻底砍迁移（2026-08-31，ADR 0008 决策 8）：删历史 boss 岗位 + 其跟进事件。

    BOSS 抓取/登录/筛选/类型全砍，`JobSource` 字面量已去掉 boss——若不删存量 boss 行，
    GET /jobs 反序列化时 pydantic 校验失败 → 整个列表 500。故连数据一起清（先删指向
    boss 岗位的 followup 事件，再删岗位，免留孤儿外键）。
    幂等：无 boss 行则跳过。
    """
    async with engine.begin() as conn:
        boss_ids = (await conn.execute(text("SELECT id FROM jobs WHERE source_name = 'boss'"))).fetchall()
        if not boss_ids:
            return
        id_list = ",".join(str(r[0]) for r in boss_ids)
        await conn.execute(text(f"DELETE FROM job_followup_events WHERE job_id IN ({id_list})"))
        await conn.execute(text("DELETE FROM jobs WHERE source_name = 'boss'"))
        logger.info("migration_dropped_boss_jobs", count=len(boss_ids), reason="boss_source_removed")


async def run_migrations() -> None:
    """跑全部迁移（顺序敏感：重建表 → 补列/改名 → 回填 → 删表）。

    顺序固定的理由：`_rebuild_user_facts_if_flat` 必须在 `_add_missing_columns` 前
    （旧平铺结构重建后才有新列）；`_backfill_facts_on_resume` 必须在 on_resume 补列后
    （否则 UPDATE 撞「no such column: on_resume」）。其余迁移互不依赖。
    """
    await _rebuild_user_facts_if_flat()
    await _migrate_optimization_columns()
    await _migrate_id_anchor_columns()
    await _add_missing_columns()
    await _backfill_facts_on_resume()
    await _drop_job_screening()
    await _drop_boss_jobs()
