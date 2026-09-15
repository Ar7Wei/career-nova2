"""jobs / job_followup_events repository：岗位聚合 + 投递跟进状态时间线的读写。

唯一碰 jobs / job_followup_events 表的地方（分层红线）。不知道 LLM / connector 存在。
返回 schemas 层模型（RORO），不裸表行。

apply.md §4/§5/§12：
- `jobs` 一条 = 一个平台的源岗位；同源幂等 upsert（UNIQUE source_name + external_id）。
- 跨源不去重（同一岗位多平台都留）。增量 = upsert 只更新岗位字段，不动跟进事件。
- `job_followup_events` 投递跟进时间线（2026-08-30 取代 job_screening）：追加式，
  当前态 = 最新事件（派生）；未处理 = 无事件。
"""

import asyncio
import json
from contextlib import asynccontextmanager

from datetime import UTC, datetime

from sqlmodel import desc, select

from app.core.errors import EpochChangedError
from app.models.job import Interview, Job, JobFollowupEvent
from app.repositories.base import async_session_maker
from app.repositories.settings import get_data_epoch
from app.schemas.jobs import FollowupEventCreate
from app.schemas.jobs import InterviewCreate, InterviewUpdate
from app.schemas.jobs import Interview as InterviewOut
from app.schemas.jobs import Job as JobOut
from app.schemas.jobs import JobBrief
from app.schemas.jobs import JobCounts
from app.schemas.jobs import JobFollowupEvent as FollowupOut

# 同源去重唯一键（语义唯一 source_name + external_id）由仓库层 upsert 兜底
# （select-then-insert，非数据库 UNIQUE 约束）；仓库层做 upsert（已存在则更新，不存在则插入）。

# upsert 命中既有行时**不覆盖**的字段（2026-09-14，apply.md §11.7.8）：
# - 唯一键：定位用，写回无意义。
# - 归属字段：首触即定——"这岗第一次是谁抓的/哪个词召回的"，被后一轮改写即错乱。
_IMMUTABLE_ON_UPDATE: frozenset[str] = frozenset(
    {"source_name", "external_id", "crawl_round_id", "found_by_query"}
)

# 数据代次守卫锁（2026-08-21 方案②）：把「读 epoch 比对 + 写 jobs」串成原子单元。
# reset_all 先 bump epoch 再删 jobs；ingest/refresh 若在开抓后读到 epoch 变了（核爆），
# 必须丢弃在途、不回写——否则孤儿岗位写回刚清空的表。单进程 FastAPI + SQLite 单写者，
# 进程内一把 asyncio.Lock 与 BEGIN IMMEDIATE 等价，且与 document_task/_upload_lock 同构。
_epoch_guard = asyncio.Lock()


@asynccontextmanager
async def epoch_guard(expect_epoch: int | None):
    """核爆安全的写事务边界：持锁期间比对代次后再写 jobs。

    expect_epoch = None（手动刷新，无在途快照）→ 不比对，但**仍持锁**——与核爆的清空
    串行化，避免「核爆清空 jobs 的间隙里写进旧数据」。
    expect_epoch = N（后台批爬）→ 比对当前 epoch；已变（核爆）→ 抛 EpochChangedError 丢弃。
    asyncio.Lock 保证「比对 + 写」原子：reset_all 的 bump 与 jobs 写入不交错（单进程内
    await 点互斥，等价于 BEGIN IMMEDIATE 全程持写锁）。
    """
    async with _epoch_guard:
        if expect_epoch is not None and await get_data_epoch() != expect_epoch:
            raise EpochChangedError("数据已被重置（重新开始），丢弃这批在途抓取结果")
        yield


@asynccontextmanager
async def epoch_write_guard():
    """核爆写侧守卫：reset_all 在 bump data_epoch 时持锁。

    与 epoch_guard 共用同一把锁——bump 一旦持锁，任何「比对 + 写 jobs」的 ingest/refresh
    都排到它之后，之后看到新 epoch 即丢弃；反之 ingest 先持锁写完提交，bump 后到，
    reset 的清空会把那批已提交的岗位一起删掉。两者都无孤儿。
    """
    async with _epoch_guard:
        yield


def _to_job(row: Job) -> JobOut:
    """表行 → 传输模型（JSON 列反序列化）。followup 态由调用方（list_jobs）注入。"""
    return JobOut(
        id=row.id or 0,
        source=row.source_name,  # type: ignore[arg-type]
        external_id=row.external_id,
        title=row.title,
        company=row.company,
        city=row.city,
        salary_text=row.salary_text,
        experience=row.experience,
        degree=row.degree,
        skills=json.loads(row.skills) if row.skills else [],
        job_labels=json.loads(row.job_labels) if row.job_labels else [],
        welfare=json.loads(row.welfare) if row.welfare else [],
        description=row.description,
        jd_status=row.jd_status,  # type: ignore[arg-type]
        source_url=row.source_url,
        apply_url=row.apply_url,
        liveness=row.liveness,  # type: ignore[arg-type]
        last_seen_at=row.last_seen_at.isoformat(),
        is_new=row.is_new,
        crawl_round_id=row.crawl_round_id,
    )


def _to_followup(row: JobFollowupEvent) -> FollowupOut:
    """跟进事件表行 → 传输模型。"""
    return FollowupOut(
        id=row.id or 0,
        job_id=row.job_id,
        status=row.status,  # type: ignore[arg-type]
        reason=row.reason,  # type: ignore[arg-type]
        stage=row.stage,
        note=row.note,
        source=row.source,
        at=row.at.isoformat(),
    )


def _to_interview(row: Interview) -> InterviewOut:
    """面试场次表行 → 传输模型。scheduled_at 读回可能 naive（SQLite 丢时区），当 UTC 补 Z。"""
    dt = row.scheduled_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return InterviewOut(
        id=row.id or 0,
        job_id=row.job_id,
        scheduled_at=dt.isoformat().replace("+00:00", "Z"),
        location=row.location,
        round=row.round,
        outcome=row.outcome,  # type: ignore[arg-type]
        next_round_id=row.next_round_id,
    )


async def upsert_job(job: dict) -> tuple[JobOut, bool]:
    """同源幂等 upsert：存在则更新字段 + last_seen_at，不存在则插入。

    job：connector 归一化后的原始字段 dict（含 source_name/external_id/...）。
    返回 (带 id 的完整模型, 是否本次新增) —— added=True 是新插入（前端"新"角标），
    updated=False。is_new 列：插入时 True，后续刷新命中置 False（不再是本轮新增）。

    **首触即定**（2026-09-14，apply.md §11.7.8）：`crawl_round_id` / `found_by_query` 是
    "这岗第一次是谁抓的/哪个词召回的"——属历史事实。猎聘每轮重抓旧岗送 upsert，若让它们
    进更新分支就会被后一轮改写 → 归属错乱。故与 source_name/external_id 一并跳过。
    """
    async with async_session_maker() as session:
        now = datetime.now(UTC)
        row = (
            await session.exec(
                select(Job).where(Job.source_name == job["source_name"], Job.external_id == job["external_id"])
            )
        ).first()
        # JSON 列（skills/job_labels/welfare）收 list → 序列化成 JSON 文本列
        data = dict(job)
        for key in ("skills", "job_labels", "welfare"):
            value = data.get(key)
            if isinstance(value, list):
                data[key] = json.dumps(value, ensure_ascii=False)
        if row is not None:
            # 已存在：更新字段（不动 created_at / 用户反馈 / 归属字段），liveness 由 connector 刷新
            for key, value in data.items():
                if key in _IMMUTABLE_ON_UPDATE:
                    continue
                setattr(row, key, value)
            row.last_seen_at = now
            row.updated_at = now
            row.is_new = False  # 刷新命中 = 不再是本轮新增
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_job(row), False
        # 不存在：插入（is_new 默认 True = 本轮新增）
        row = Job(**data, last_seen_at=now, created_at=now, updated_at=now)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_job(row), True


async def _latest_followup_map(session, job_ids: list[int]) -> dict[int, JobFollowupEvent]:
    """读一组岗位各自最新一条跟进事件（当前态 = 派生）。"""
    if not job_ids:
        return {}
    rows = (
        await session.exec(
            select(JobFollowupEvent)
            .where(JobFollowupEvent.job_id.in_(job_ids))  # type: ignore[attr-defined]
            .order_by(JobFollowupEvent.id)  # type: ignore[arg-type]  # 稳定升序，后同 job_id 覆盖为最新
        )
    ).all()
    latest: dict[int, JobFollowupEvent] = {}
    for r in rows:
        latest[r.job_id] = r  # id 升序 → 同 job_id 最后一个即最新
    return latest


async def _latest_interview_map(session, job_ids: list[int]) -> dict[int, Interview]:
    """读一组岗位各自最新一场面试（按 scheduled_at 降序取第一 = 最近排的那场）。

    用于惰性派生「已面试」：最新事件 interviewing 时，看最近那场面试的约定时间是否已过。
    """
    if not job_ids:
        return {}
    rows = (
        await session.exec(
            select(Interview)
            .where(Interview.job_id.in_(job_ids))  # type: ignore[attr-defined]
            .order_by(desc(Interview.scheduled_at), desc(Interview.id))
        )
    ).all()
    latest: dict[int, Interview] = {}
    for r in rows:
        if r.job_id not in latest:
            latest[r.job_id] = r  # 已按降序 → 第一个即最新
    return latest


def _derive_interviewed(ev: JobFollowupEvent | None, iv: Interview | None, now: datetime) -> str | None:
    """惰性派生：interviewing 事件 → 约定时间已过或 outcome 已非 scheduled → interviewed。

    只把 interviewing 派生为 interviewed，其余状态原样返回。无事件 = None（未处理）。
    """
    if ev is None:
        return None
    if ev.status != "interviewing":
        return ev.status
    if iv is not None:
        if iv.outcome != "scheduled":
            return "interviewed"
        if iv.scheduled_at.tzinfo is None:
            iv_scheduled = iv.scheduled_at.replace(tzinfo=UTC)
        else:
            iv_scheduled = iv.scheduled_at
        if now >= iv_scheduled:
            return "interviewed"
    return "interviewing"


async def list_jobs(
    sort: str = "time",
    order: str = "desc",
    status: str | None = None,
    reason: str | None = None,
    start: str | None = None,
    end: str | None = None,
    crawl_round_id: int | None = None,
) -> list[JobOut]:
    """列岗位列表（跨平台统一列表，含排序/筛选/状态 tab）。

    sort（ADR 0013：单一「时间」维度，freshness/relevance/query 已砍 2026-09-08）：
    - time：按 last_seen_at（未处理 tab：收录时间）。
    - followup：跟进 tab 按当前态事件时间。
    - 缺省/未知值 → time。
    status（2026-08-30）：按当前态过滤——unprocessed = 无事件；其余按最新事件 status。
    reason（2026-08-30）：仅配合 status=not_pursuing 用（不再追踪 tab 的原因筛选）。
    start/end（2026-08-31）：日期范围（闭区间 [start, end]）——未处理按 last_seen_at、
      其余按处理日期（当前态事件 at）过滤。
    crawl_round_id（2026-09-14，apply.md §11.7）：只看某一轮抓到的岗位——投递页分析
      按轮取数用（检索条件会改、轮次间不可比，故分析只吃本轮那批）。
    order：asc / desc。
    """
    async with async_session_maker() as session:
        rows = list((await session.exec(select(Job))).all())
        if crawl_round_id is not None:
            rows = [r for r in rows if r.crawl_round_id == crawl_round_id]
        latest = await _latest_followup_map(session, [r.id or 0 for r in rows])
        latest_iv = await _latest_interview_map(session, [r.id or 0 for r in rows])
        now = datetime.now(UTC)

        def _jid(j: Job) -> int:
            return j.id or 0

        def status_of(j: Job) -> str | None:
            ev = latest.get(_jid(j))
            return _derive_interviewed(ev, latest_iv.get(_jid(j)), now)

        # 处理日期（未处理 = 抓取日 last_seen_at；否则 = 当前态事件 at）。归一 UTC。
        def processing_at(j: Job) -> datetime:
            ev = latest.get(_jid(j))
            dt = ev.at if ev else j.last_seen_at
            return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)

        # 日期范围过滤（闭区间；start/end 是 ISO 字符串，缺省 = 不限）
        if start is not None:
            start_dt = _parse_dt(start)
            rows = [j for j in rows if processing_at(j) >= start_dt]
        if end is not None:
            end_dt = _parse_dt(end)
            rows = [j for j in rows if processing_at(j) <= end_dt]

        # 状态 tab 过滤（reason 仅叠加在 not_pursuing 上）
        if status == "unprocessed":
            rows = [j for j in rows if status_of(j) is None]
        elif status is not None:
            rows = [j for j in rows if status_of(j) == status]
            if reason is not None:
                rows = [j for j in rows if (ev := latest.get(_jid(j))) is not None and ev.reason == reason]

        # 排序在内存做（本地单用户、岗位量小；字段含 JSON 列/中文，SQL 排序反直觉）

        def followup_at(j: Job) -> datetime:
            ev = latest.get(_jid(j))
            return ev.at if ev else j.last_seen_at

        def score(j: Job) -> tuple:
            if sort == "followup":
                return (followup_at(j).timestamp(),)
            return (j.last_seen_at.timestamp(),)

        rows.sort(key=score, reverse=(order == "desc"))

        result: list[JobOut] = []
        for j in rows:
            out = _to_job(j)
            ev = latest.get(_jid(j))
            if ev is not None:
                out.followup_status = _derive_interviewed(ev, latest_iv.get(_jid(j)), now)  # type: ignore[assignment]
                out.followup_reason = ev.reason  # type: ignore[assignment]
                out.followup_at = ev.at.isoformat()
            result.append(out)
        return result


async def count_jobs() -> JobCounts:
    """各状态徽标数（GET /jobs 顶层 counts，apply.md §12.11⑤）。

    unprocessed/applied/offered/not_pursuing = 岗位数（按派生当前态）；interviewing =
    待面试场次数（outcome=scheduled 且 scheduled_at >= now，非岗位数——与「面试」tab
    徽标一致）。全局计数，不受 source/status/date 筛选影响。
    """
    async with async_session_maker() as session:
        rows = list((await session.exec(select(Job))).all())
        job_ids = [r.id or 0 for r in rows]
        latest = await _latest_followup_map(session, job_ids)
        latest_iv = await _latest_interview_map(session, job_ids)
        now = datetime.now(UTC)

        counts = JobCounts()
        for j in rows:
            ev = latest.get(j.id or 0)
            status = _derive_interviewed(ev, latest_iv.get(j.id or 0), now)
            if status is None:
                counts.unprocessed += 1
            elif status == "applied":
                counts.applied += 1
            elif status == "offered":
                counts.offered += 1
            elif status == "not_pursuing":
                counts.not_pursuing += 1
            # interviewing/interviewed 岗位不直接计入 interviewing 徽标（那是场次数）

        # 待面试场次数：所有 outcome=scheduled 且约定时间未到的场次
        iv_rows = (await session.exec(select(Interview))).all()
        counts.interviewing = sum(
            1
            for iv in iv_rows
            if iv.outcome == "scheduled"
            and _as_utc(iv.scheduled_at) >= now
        )
        return counts


async def get_job(job_id: int) -> JobOut | None:
    """按 id 读一条岗位；不存在返回 None。"""
    async with async_session_maker() as session:
        row = await session.get(Job, job_id)
        if row is None:
            return None
        out = _to_job(row)
        row_id = row.id or 0
        ev = await _latest_followup_map(session, [row_id])
        iv_map = await _latest_interview_map(session, [row_id])
        if ev.get(row_id):
            out.followup_status = _derive_interviewed(ev[row_id], iv_map.get(row_id), datetime.now(UTC))  # type: ignore[assignment]
            out.followup_reason = ev[row_id].reason  # type: ignore[assignment]
            out.followup_at = ev[row_id].at.isoformat()
        return out


async def list_external_ids(source: str) -> list[str]:
    """读某平台已入库的全部 external_id（Electron 点击遍历去重用：只点没抓过的）。"""
    async with async_session_maker() as session:
        rows = (await session.exec(select(Job.external_id).where(Job.source_name == source))).all()
        return [r for r in rows if r]


async def add_followup_event(job_id: int, event: FollowupEventCreate) -> FollowupOut | None:
    """追加一条投递跟进状态事件（时间线，无状态机、任意跳）。

    岗位必须存在（不存在返回 None，由调用方抛 404）。source 恒 user（v1 手动挡）。
    """
    async with async_session_maker() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return None
        row = JobFollowupEvent(
            job_id=job_id,
            status=event.status,
            reason=event.reason,
            stage=event.stage or "",
            note=event.note or "",
            source="user",
            at=datetime.now(UTC),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_followup(row)


async def list_followup_events(job_id: int) -> list[FollowupOut]:
    """读某岗位的完整状态时间线（按 at 升序）。"""
    async with async_session_maker() as session:
        rows = (
            await session.exec(
                select(JobFollowupEvent)
                .where(JobFollowupEvent.job_id == job_id)
                .order_by(JobFollowupEvent.id)  # type: ignore[arg-type]
            )
        ).all()
        return [_to_followup(r) for r in rows]


async def list_events_in_range(start: datetime, end: datetime) -> list[tuple[FollowupOut, JobOut]]:
    """读某时间段内的跟进事件（按 at 升序）+ 各自岗位（日报趋势/响应率取数用，§11.7.5）。

    「投递活动」= 跟进事件（applied/interviewing/offered/not_pursuing 的每次状态跃迁）——
    日报按事件时间（`at`）落窗口，看这段里发生了什么。返回 (事件, 岗位) 对，一次查询拿齐。
    """
    async with async_session_maker() as session:
        rows = (
            await session.exec(
                select(JobFollowupEvent)
                .where(JobFollowupEvent.at >= start, JobFollowupEvent.at <= end)
                .order_by(JobFollowupEvent.at)  # type: ignore[arg-type]
            )
        ).all()
        out: list[tuple[FollowupOut, JobOut]] = []
        for ev in rows:
            job = await session.get(Job, ev.job_id)
            if job is not None:
                out.append((_to_followup(ev), _to_job(job)))
        return out


async def clear_all_jobs() -> int:
    """清空全部岗位（调试/重置用），返回删除行数。"""
    async with async_session_maker() as session:
        rows = (await session.exec(select(Job))).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)


async def clear_all_followup_events() -> int:
    """清空全部跟进事件（核爆用），返回删除行数。

    与 clear_all_jobs 分开：跟进是用户主观数据，删岗位不能自动连带（平时增量 upsert
    也不动跟进）。核爆时两个都调（先删跟进再删岗位，免留 job_id 指向已删岗位的孤儿行）。
    """
    async with async_session_maker() as session:
        rows = (await session.exec(select(JobFollowupEvent))).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)


async def count_unprocessed_jobs() -> int:
    """数未处理岗位：没有 job_followup_events 记录的岗位数（apply.md §12 投入分层）。"""
    async with async_session_maker() as session:
        followed_ids = set((await session.exec(select(JobFollowupEvent.job_id))).all())
        rows = (await session.exec(select(Job))).all()
        return sum(1 for r in rows if r.id not in followed_ids)


async def delete_unprocessed_jobs() -> int:
    """删除未处理岗位（无 job_followup_events 记录），返回删除行数。已处理无条件保留。"""
    async with async_session_maker() as session:
        followed_ids = set((await session.exec(select(JobFollowupEvent.job_id))).all())
        rows = (await session.exec(select(Job))).all()
        victims = [r for r in rows if r.id not in followed_ids]
        for row in victims:
            await session.delete(row)
        await session.commit()
        return len(victims)


# ---------------------------------------------------------------------------
# 面试场次（interviews，apply.md §12.10 / ADR 0006）
# ---------------------------------------------------------------------------


def _parse_dt(s: str) -> datetime:
    """ISO 字符串 → 时区感知 datetime。SQLite 存 naive，统一转 UTC 存。"""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _as_utc(dt: datetime) -> datetime:
    """Naive datetime → UTC-aware（SQLite 读回丢时区，当 UTC 处理）。"""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


async def create_interview(job_id: int, data: InterviewCreate) -> InterviewOut | None:
    """新建一场面试（默认 outcome=scheduled）。岗位不存在返回 None（调用方 404）。"""
    async with async_session_maker() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return None
        row = Interview(
            job_id=job_id,
            scheduled_at=_parse_dt(data.scheduled_at),
            location=data.location or "",
            round=data.round or "",
            outcome="scheduled",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_interview(row)


async def list_interviews(job_id: int | None = None) -> list[InterviewOut]:
    """列面试场次（日历数据源 = 全表；传 job_id 则只看某岗位）。按 scheduled_at 升序。

    每场内嵌 job 摘要（company/title/description）——日历详情弹窗联查用，不依赖全量 jobs。
    """
    async with async_session_maker() as session:
        stmt = select(Interview)  # type: ignore[arg-type]
        if job_id is not None:
            stmt = stmt.where(Interview.job_id == job_id)
        stmt = stmt.order_by(Interview.scheduled_at, Interview.id)  # type: ignore[arg-type]
        rows = (await session.exec(stmt)).all()
        job_ids = [r.job_id for r in rows]
        jobs = {j.id: j for j in (await session.exec(select(Job).where(Job.id.in_(job_ids)))).all()} if job_ids else {}  # type: ignore[union-attr]
        out: list[InterviewOut] = []
        for r in rows:
            iv = _to_interview(r)
            j = jobs.get(r.job_id)
            if j is not None:
                iv.job = JobBrief(company=j.company, title=j.title, description=j.description)
            out.append(iv)
        return out


async def get_interview(interview_id: int) -> InterviewOut | None:
    """按 id 读一场面试；不存在返回 None。"""
    async with async_session_maker() as session:
        row = await session.get(Interview, interview_id)
        return _to_interview(row) if row is not None else None


async def update_interview(interview_id: int, data: InterviewUpdate) -> InterviewOut | None:
    """更新一场面试（改期 / 改 outcome / 改地点轮次）。只改传入的非 None 字段。

    不维护状态线——终局联动是 service 层职责。存在则更新，不存在返回 None。
    """
    async with async_session_maker() as session:
        row = await session.get(Interview, interview_id)
        if row is None:
            return None
        if data.scheduled_at is not None:
            row.scheduled_at = _parse_dt(data.scheduled_at)
        if data.location is not None:
            row.location = data.location
        if data.round is not None:
            row.round = data.round
        if data.outcome is not None:
            row.outcome = data.outcome
        row.updated_at = datetime.now(UTC)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_interview(row)


async def add_next_round(
    interview_id: int, next_data: InterviewCreate
) -> tuple[InterviewOut | None, InterviewOut | None]:
    """安排下一场：本场标 outcome=next_round + next_round_id 指向新行（新行 scheduled）。

    返回 (更新后的本场, 新建的下一场)。本场不存在返回 (None, None)。链只写一次——
    本场此前必须无 next_round_id（删除场景不存在，见 apply.md §12.10）。
    """
    async with async_session_maker() as session:
        cur = await session.get(Interview, interview_id)
        if cur is None:
            return None, None
        nxt = Interview(
            job_id=cur.job_id,
            scheduled_at=_parse_dt(next_data.scheduled_at),
            location=next_data.location or "",
            round=next_data.round or "",
            outcome="scheduled",
        )
        session.add(nxt)
        await session.flush()  # 拿到 nxt.id 供 cur.next_round_id 引用
        cur.outcome = "next_round"
        cur.next_round_id = nxt.id
        cur.updated_at = datetime.now(UTC)
        session.add(cur)
        await session.commit()
        await session.refresh(cur)
        await session.refresh(nxt)
        return _to_interview(cur), _to_interview(nxt)


async def clear_all_interviews() -> int:
    """清空全部面试场次（核爆用），返回删除行数。与岗位/跟进分开，核爆时各自清。"""
    async with async_session_maker() as session:
        rows = (await session.exec(select(Interview))).all()
        for row in rows:
            await session.delete(row)
        await session.commit()
        return len(rows)
