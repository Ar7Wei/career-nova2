"""抓取编排 service：事件驱动 kick + 续页游标 + 单平台三态（apply.md §8 / ADR 0008）。

分层：Router → Service → Repository。本层编排 crawl_state / jobs repository + 猎聘
connector，抛 AppError；不碰 HTTP。

核心（2026-08-31 grill 定稿）：
- **零计时器**：爬虫只被用户事件（kickCrawl）叫醒，醒来那一刻惰性看钟（throttled
  冷却 / exhausted 转天都靠「事件到来时看钟」比对），不用 setTimeout/setInterval。
- **kick 同步锁**：猎聘一轮后端自循环、自记账（限流信号在后端）；前程无忧一轮
  Electron 驱动、报结果（限流信号在 Electron）。kick 只推进猎聘，前程无忧的推进靠
  Electron 报 crawl-state。
- **三态是组级**：exhausted/throttled 记到查询组粒度——组 1 空页 = 组 1 exhausted、
  游标移到组 2；所有组都到底才整家 exhausted。
- **游标二维 (组, 页)**：page 存平台原生页号（猎聘 0 起 / 前程无忧 1 起）。撞 cap →
  回第 1 页（组 0 原点）；exhausted 转天回第 1 页；quota 凑满 → 游标前进。
"""

import asyncio
from datetime import UTC, datetime
from typing import Literal, cast

from app.connectors.liepin import LiepinBlockedError, fetch_liepin
from app.core.errors import EpochChangedError
from app.core.logging import logger
from app.repositories import crawl_state as crawl_repo
from app.repositories import jobs as jobs_repo
from app.repositories.crawl_state import PAGE_ORIGIN
from app.repositories.settings import get_data_epoch
from app.schemas.crawl_state import CrawlGateReason, CrawlGroupState, CrawlSource, CrawlStateRead, CrawlStateReport, CrawlStateResult, KickResponse
from app.schemas.jobs import JobIngestItem
from app.services.direction import get_direction
from app.services.jobs import ingest_jobs
from app.services.settings import get_settings

# 限流惰性冷却期（秒）：下次 kick 时距上次失败 < 此值 → 跳过。与 exhausted「转天」
# 同构——一个机制两种单位（墙钟 vs 日期），都不靠计时器主动唤醒。
THROTTLE_COOLDOWN_S = 15 * 60

# 抓取轮次 id（2026-09-14，apply.md §11.7.8）：进程内单调递增计数，identify「同一轮抓取」。
# **一轮 = 一次 kick**（后端猎聘一段 + Electron 前程无忧一段），两平台共用同一个 id——
# §11.7.4 的批次分析本就假定"一批岗位来自两个平台、分别处理"，故批次键必须跨平台。
# 与 found_by_query 同属**首触即定**——写进 jobs.crawl_round_id 只在插入时生效，后轮不改。
# 单进程单用户，重启后从 0 重来（顶多让重启前后两轮撞号；同号无害，分析按接口传的 id 走）。
# 显式传参（不用 ContextVar）——显式即正确：谁抓谁带轮次。
_round_counter = 0


def next_round_id() -> int:
    """分配下一个抓取轮次 id（进程内单调）。一次 kick 调一次，两平台共用。"""
    global _round_counter
    _round_counter += 1
    return _round_counter


def _reset_round_counter_for_tests() -> None:
    """测试用：轮次计数归零（conftest 每测试复位，免跨测试串号）。"""
    global _round_counter
    _round_counter = 0


# 抓取三态 → 检索反馈信号（apply.md §3.1「哪组词好」）：fresh 有增量 / exhausted 空页 /
# throttled 被限流。三态是组级，归到时用**该组的召回词**标注（probe 文本与 found_by_query 同源）。
FeedbackKind = Literal["added", "exhausted", "throttled"]


def _record_feedback(source: str, query: str, city: str, kind: FeedbackKind, added: int) -> None:
    """埋检索反馈信号的点（apply.md §3.1）。

    **本波只留钩子，不落库**——§3.1 的 `crawl_query_feedback` 表属另一条需求（"哪组词好"，
    要配投递反馈一起看），此处仅把「该记什么」写死、留 TODO，免后人在三态各处乱写。
    """
    # TODO(§3.1): upsert crawl_query_feedback(source, query, city, kind, added, round_id, at=_now_iso())
    logger.info("crawl_query_feedback", source=source, query=query, city=city, kind=kind, added=added)

# kick 防重入锁（decision 4）：猎聘一轮几十秒，锁内即防并发 kick 重复翻页。
_kick_lock = asyncio.Lock()


def _today() -> str:
    """UTC 日期字符串（exhausted 打标记用；转天 = 日期不同）。"""
    return datetime.now(UTC).date().isoformat()


def _now_iso() -> str:
    """UTC 时间戳 ISO 字符串（throttled 记失败时间用）。"""
    return datetime.now(UTC).isoformat()


def _align_groups(groups: list[CrawlGroupState], n: int) -> list[CrawlGroupState]:
    """把 groups 对齐到方向查询组数 n（改方向会 reset，正常恒等；防御性补齐/截断）。"""
    out: list[CrawlGroupState] = []
    for i in range(n):
        if i < len(groups):
            out.append(groups[i])
        else:
            out.append(CrawlGroupState())
    return out


def _is_exhausted_today(g: CrawlGroupState, today: str) -> bool:
    return g.exhausted_date == today


def _is_throttled_now(g: CrawlGroupState, now: datetime) -> bool:
    ts = g.throttled_at
    if not ts:
        return False
    try:
        failed_at = datetime.fromisoformat(ts)
    except ValueError:
        return False
    if failed_at.tzinfo is None:
        failed_at = failed_at.replace(tzinfo=UTC)
    return (now - failed_at).total_seconds() < THROTTLE_COOLDOWN_S


def _crawlable_groups(groups: list[CrawlGroupState], now: datetime, today: str) -> list[int]:
    """可爬的组下标（当天未 exhausted 且冷却期外的组）。"""
    out: list[int] = []
    for i, g in enumerate(groups):
        if _is_exhausted_today(g, today) or _is_throttled_now(g, now):
            continue
        out.append(i)
    return out


def _query_groups(direction) -> list[tuple[str, str]]:
    """方向二维查询组 → 探针数组 `[(query, city)]`（2026-09-13 ADR 0019）。

    query = 组内词空格 join（AND），组间 OR；city = 该组自带城市（挂到组上）。探针顺序 =
    组顺序，故游标 `group_idx` 天然对齐探针下标——**组数/三态/游标维度全不变**。组内全空/
    查询为空的组跳过（城市随组一起丢弃）。
    """
    probes: list[tuple[str, str]] = []
    for i, g in enumerate(direction.keywords):
        q = " ".join(w for w in g if w)
        if q:
            probes.append((q, direction.cities[i] if i < len(direction.cities) else ""))
    return probes


async def _global_gate() -> CrawlGateReason:
    """全局门控三段（apply_mode / 方向 / backlog）。crawled = 通过（平台级三态另判）。

    返回原因供 kick 顶层 gate_reason（前端指示器信号）——门控拒时 liepin/job51 都 None，
    靠这里区分「冷却期/当天爬完」与「backlog 满」。
    """
    s = await get_settings()
    if not s.apply_mode:
        return "apply_mode_off"
    direction = await get_direction()
    probes = _query_groups(direction)
    if not probes:
        logger.info("crawl_gate_no_direction")
        return "no_direction"
    unprocessed = await jobs_repo.count_unprocessed_jobs()
    if unprocessed >= s.crawl_threshold:
        logger.info("crawl_gate_backlog_full", unprocessed=unprocessed, threshold=s.crawl_threshold)
        return "backlog_full"
    return "crawled"


async def _source_crawlable(source: str) -> bool:
    """平台级可爬：该平台尚有当天未 exhausted / 冷却期外的组。"""
    direction = await get_direction()
    probes = _query_groups(direction)
    state = await crawl_repo.get_crawl_state(source)
    groups = _align_groups(state.groups if state else [], len(probes))
    return bool(_crawlable_groups(groups, datetime.now(UTC), _today()))


async def _should_crawl(source: str) -> bool:
    """门控（decision 4/10）：apply_mode 开 + 方向非空 + 未处理 < 触发值 + 该平台尚有可爬组。

    返回 False = 该平台本轮不爬（Electron 据此跳过 DOM 一轮 / 后端跳过猎聘一轮）。
    """
    if await _global_gate() != "crawled":
        return False
    if not await _source_crawlable(source):
        logger.info("crawl_gate_all_exhausted_or_throttled", source=source)
        return False
    return True


async def _gate_reason() -> CrawlGateReason:
    """这一脚整体的门控结果（前端指示器信号）。crawled = 至少一家尚有可爬组。"""
    reason = await _global_gate()
    if reason != "crawled":
        return reason
    if await _source_crawlable("liepin") or await _source_crawlable("job51"):
        return "crawled"
    # 两家都无可爬组 → 受限；细分 throttled（冷却期，等 15 分钟）vs exhausted（当天爬完）。
    for source in ("liepin", "job51"):
        direction = await get_direction()
        probes = _query_groups(direction)
        state = await crawl_repo.get_crawl_state(source)
        groups = _align_groups(state.groups if state else [], len(probes))
        if any(_is_throttled_now(g, datetime.now(UTC)) for g in groups):
            return "throttled"
    return "exhausted"


async def _run_liepin_round(s, direction, epoch: int, round_id: int) -> CrawlStateResult:
    """猎聘一轮：从游标续爬（组间顺序消费），翻页凑 quota / 撞 cap / 空页，推进游标 + 标三态。

    自循环、自记账（decision 3）——限流信号（LiepinBlockedError）在后端产生就归 throttled。
    核爆守卫：ingest_jobs 带 epoch，代次变了抛 EpochChangedError → 上层丢弃、不推进游标。
    round_id：本轮的归属戳（与同一次 kick 的前程无忧那段共用一个，见 next_round_id）。
    """
    probes = _query_groups(direction)
    quota = s.crawl_quota.liepin
    cap = s.crawl_cap

    state = await crawl_repo.get_crawl_state("liepin")
    groups = _align_groups(state.groups if state else [], len(probes))
    cur_gi = state.group_idx if state else 0
    cur_page = state.page if state else PAGE_ORIGIN["liepin"]
    origin = PAGE_ORIGIN["liepin"]

    now = datetime.now(UTC)
    today = _today()
    added = 0
    updated = 0
    pages_scanned = 0
    seen: set[str] = set()

    stop: str | None = None  # None / "quota" / "cap"
    final_gi = cur_gi
    final_page = cur_page

    for gi in range(cur_gi, len(probes)):
        g = groups[gi]
        if _is_exhausted_today(g, today) or _is_throttled_now(g, now):
            continue  # 该组当天跳过（exhausted）/ 冷却期（throttled）
        q, city = probes[gi]  # 该组自带城市（挂到组上，ADR 0019）
        recall_label = f"{q} | {city}"  # 召回词文本（与方向 fact 的 "组词 | 城市" 同构）
        p = cur_page if gi == cur_gi else origin
        while True:
            try:
                raw_jobs = await fetch_liepin(q, city=city, page=p)
            except LiepinBlockedError as e:
                # 限流 → throttled：记失败时间戳，游标停在本组本页（别当抓干）。
                groups[gi].throttled_at = _now_iso()
                groups[gi].exhausted_date = None
                await crawl_repo.set_crawl_state("liepin", gi, p, groups)
                _record_feedback("liepin", q, city, "throttled", 0)
                logger.info("crawl_liepin_throttled", group=gi, page=p, error=str(e))
                return CrawlStateResult(source="liepin", kind="throttled", group_idx=gi, page=p, added=added, updated=updated)
            pages_scanned += 1

            if not raw_jobs:
                # 空页 = 该组到底 → exhausted（记日期），游标移到下一组（原点）。
                groups[gi].exhausted_date = today
                groups[gi].throttled_at = None
                _record_feedback("liepin", q, city, "exhausted", 0)
                logger.info("crawl_liepin_group_exhausted", group=gi, page=p)
                break  # 到下一组

            # 有卡片：本轮内 external_id 去重（跨页/跨组），只捡没见过的 → 标归属 → ingest。
            fresh = [j for j in raw_jobs if j.get("external_id") not in seen]
            for j in fresh:
                eid = j.get("external_id")
                if eid is not None:
                    seen.add(str(eid))
            if fresh:
                for j in fresh:
                    # 首触即定的归属戳（repo 只在插入时采用，后轮不改）。
                    j["crawl_round_id"] = round_id
                    j["found_by_query"] = recall_label
                resp = await jobs_repo_ingest(fresh, epoch)
                added += resp.added
                updated += resp.updated
                _record_feedback("liepin", q, city, "added", resp.added)
            p += 1  # 游标前进（有货翻下一页）

            if added >= quota:
                stop = "quota"
                final_gi, final_page = gi, p
                break
            if pages_scanned >= cap:
                stop = "cap"
                break

        if stop is not None:
            break

    if stop == "quota":
        # 凑满 quota：游标前进到 (gi, p)，下次续爬。
        await crawl_repo.set_crawl_state("liepin", final_gi, final_page, groups)
        logger.info("crawl_liepin_round_quota", added=added, updated=updated, group=final_gi, page=final_page)
        return CrawlStateResult(source="liepin", kind="fresh", group_idx=final_gi, page=final_page, added=added, updated=updated)

    if stop == "cap":
        # 撞 cap（后面还有货，但预算用完）：游标回第 1 页，下一轮从头扫（绕满一圈）。
        await crawl_repo.set_crawl_state("liepin", 0, origin, groups)
        logger.info("crawl_liepin_round_cap", added=added, updated=updated, pages=pages_scanned)
        return CrawlStateResult(source="liepin", kind="fresh", group_idx=0, page=origin, added=added, updated=updated)

    # 循环自然结束 = 所有组都空页 → 整家 exhausted（当天跳过，转天回第 1 页重查）。
    await crawl_repo.set_crawl_state("liepin", 0, origin, groups)
    logger.info("crawl_liepin_round_exhausted", added=added, updated=updated)
    return CrawlStateResult(source="liepin", kind="exhausted", group_idx=0, page=origin, added=added, updated=updated, exhausted=True)


async def jobs_repo_ingest(raw_jobs: list[dict], epoch: int | None):
    """薄封装：批量入库（复用 jobs service 的 ingest_jobs，带 epoch 核爆守卫）。

    raw_jobs 是 connector 归一化后的 dict（猎聘 fetch_liepin 产出），这里过
    JobIngestItem 定型（校验 + 落默认值）再交给 ingest_jobs。独立函数便于测试 monkeypatch。
    """
    items = [JobIngestItem.model_validate(j) for j in raw_jobs]
    return await ingest_jobs(items, data_epoch=epoch)


async def _job51_advice(s, direction, round_id: int) -> CrawlStateResult | None:
    """前程无忧只读建议：该不该爬 + 当前游标起点（Electron 从这开始 DOM 一轮）。

    round_id 由 kick 统一分配（与猎聘那段共用一个）——Electron 用它戳 `crawl_round_id`，
    投递页分析据此取「本轮那批」（两个平台的岗位同属一轮，§11.7.4）。
    """
    if not await _should_crawl("job51"):
        return None
    state = await crawl_repo.get_crawl_state("job51")
    gi = state.group_idx if state else 0
    page = state.page if state else PAGE_ORIGIN["job51"]
    return CrawlStateResult(source="job51", kind="fresh", group_idx=gi, page=page, exhausted=False, round_id=round_id)


async def kick() -> KickResponse:
    """事件驱动的一脚（前端 kickCrawl → 后端）。

    门控 + 持锁跑猎聘一轮（自循环 + 自记账）→ 返回猎聘结果 + 前程无忧只读建议。
    前程无忧的实际爬取由 Electron 在本响应之后驱动、报 crawl-state 记账。
    """
    s = await get_settings()
    direction = await get_direction()
    epoch = await get_data_epoch()
    # 本轮 id：一次 kick 一个，猎聘那段与返回给 Electron 的前程无忧那段共用（§11.7.4）。
    # 分配即用——即使某平台那轮被门控拒/失败/核爆丢弃，这个号也只是空号（计数单调）。
    round_id = next_round_id()

    liepin: CrawlStateResult | None = None
    if await _should_crawl("liepin"):
        async with _kick_lock:  # 防重入：锁内跑完整轮
            # 持锁后再读一次状态（锁外门控与锁内推进之间方向可能变/核爆可能发生）
            s = await get_settings()
            direction = await get_direction()
            if await _should_crawl("liepin"):
                try:
                    liepin = await _run_liepin_round(s, direction, epoch, round_id)
                except EpochChangedError as e:
                    # 核爆（数据代次已变）：协作式取消，丢弃本轮、不推进游标。预期收尾，不是故障。
                    logger.info("crawl_liepin_round_aborted", error=str(e))
                    liepin = None
                except Exception:
                    # 真异常：出声（带堆栈），本轮无猎聘结果——但不静默吞（旧写法把它降级成 None）。
                    logger.exception("crawl_liepin_round_failed")
                    liepin = None

    job51 = await _job51_advice(s, direction, round_id)
    # gate_reason：这一脚整体的门控结果（前端指示器信号）。猎聘跑过 / 前程无忧有建议 = crawled；
    # 否则按全局门控原因，或两家都无货 → all_exhausted_or_throttled。在猎聘一轮**之后**再判——
    # 一轮刚撞限流/exhausted 会立刻把该平台标记成不可爬，此时重判才能反映「受限」真值。
    gate_reason = await _gate_reason()
    return KickResponse(
        liepin=liepin,
        job51=job51,
        data_epoch=epoch,
        job51_quota=s.crawl_quota.job51,
        cap=s.crawl_cap,
        gate_reason=gate_reason,
        round_id=round_id,
    )


async def read_crawl_state(source: str) -> CrawlStateRead:
    """读某平台游标（Electron 前程无忧一轮的起点 + 方向组数 + 是否整家 exhausted）。"""
    direction = await get_direction()
    probes = _query_groups(direction)
    state = await crawl_repo.get_crawl_state(source)
    gi = state.group_idx if state else 0
    page = state.page if state else PAGE_ORIGIN.get(source, 0)
    groups = _align_groups(state.groups if state else [], len(probes))
    now = datetime.now(UTC)
    today = _today()
    exhausted = not _crawlable_groups(groups, now, today) and len(probes) > 0
    # source 由 API 层 Query 收口为 job51/liepin；显式断言以喂 Literal 类型。
    src = cast(CrawlSource, source)
    return CrawlStateRead(source=src, group_idx=gi, page=page, group_count=len(probes), exhausted=exhausted)


async def report_crawl_state(report: CrawlStateReport) -> CrawlStateResult:
    """Electron 前程无忧一轮报结果 → 后端记账（推进游标 + 标三态，decision 3/12）。

    kind=throttled：记失败时间戳，游标停在本组本页。
    kind=exhausted：本组空页打日期标记，游标移下一组；所有组都到底 → 整家 exhausted。
    kind=fresh：reason=quota 游标前进 / reason=cap 游标回第 1 页。
    """
    direction = await get_direction()
    probes = _query_groups(direction)
    state = await crawl_repo.get_crawl_state(report.source)
    groups = _align_groups(state.groups if state else [], len(probes))
    origin = PAGE_ORIGIN.get(report.source, 0)
    today = _today()
    # 该组的召回词（检索反馈信号标注用，apply.md §3.1）；越界（防御）→ 空。
    q, city = probes[report.group_idx] if 0 <= report.group_idx < len(probes) else ("", "")

    if report.kind == "throttled":
        if 0 <= report.group_idx < len(groups):
            groups[report.group_idx].throttled_at = _now_iso()
            groups[report.group_idx].exhausted_date = None
        await crawl_repo.set_crawl_state(report.source, report.group_idx, report.page, groups)
        _record_feedback(report.source, q, city, "throttled", report.added)
        return CrawlStateResult(source=report.source, kind="throttled", group_idx=report.group_idx, page=report.page, added=report.added, updated=report.updated)

    if report.kind == "exhausted":
        if 0 <= report.group_idx < len(groups):
            groups[report.group_idx].exhausted_date = today
            groups[report.group_idx].throttled_at = None
        # 游标移下一组（原点）；已是最后一组则回第 1 页（转天重查）
        next_gi = report.group_idx + 1 if report.group_idx + 1 < len(probes) else 0
        next_page = origin if report.group_idx + 1 < len(probes) else origin
        await crawl_repo.set_crawl_state(report.source, next_gi, next_page, groups)
        _record_feedback(report.source, q, city, "exhausted", 0)
        exhausted = not _crawlable_groups(groups, datetime.now(UTC), today)
        return CrawlStateResult(source=report.source, kind="exhausted", group_idx=next_gi, page=next_page, added=report.added, updated=report.updated, exhausted=exhausted)

    # fresh
    _record_feedback(report.source, q, city, "added", report.added)
    if report.reason == "cap":
        await crawl_repo.set_crawl_state(report.source, 0, origin, groups)
        return CrawlStateResult(source=report.source, kind="fresh", group_idx=0, page=origin, added=report.added, updated=report.updated)
    # quota（或未标停因的 fresh）：游标前进
    await crawl_repo.set_crawl_state(report.source, report.group_idx, report.page, groups)
    return CrawlStateResult(source=report.source, kind="fresh", group_idx=report.group_idx, page=report.page, added=report.added, updated=report.updated)
