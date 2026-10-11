"""投递页分析 service（apply.md §11.7）。

**方向修正（2026-09-15，经确认）**：批次分析（batch）**已 graph 化**——骨架是
`graphs/analysis.py` 的线性图（load → prepare → compute_stats → analyze → persist），
analyze 用**带工具位的 Agent**（`agents/analysis.py`）。原「不建 graph、不用 agent」是
未与共识即落文档的表述，作废（见 docs/design/apply.md §11.7.6 修订）。graph 化的目的 =
节点即扩展点，将来加「查市场/公司情报/人门」直接插节点，不颠覆。daily/interview 仍线性
（数据薄、无编排需求），待批次 graph 跑顺再迁。

两种数据源两种分析法（§11.7.4）——信息类型不同就分别处理：
- **猎聘**：无 JD，但结构化（skills 标签/薪资/学历/年限）→ 代码聚合标签 → 综合分析。
- **job51**：有 JD 全文，结构化字段空 → 逐岗读 JD 提炼 → 合并 → 综合分析。
逐岗 JD 提炼（逐岗 LLM 的重活）留在本 service 的 prepare 阶段，不进 graph。

三个 scope（§11.7.5）：batch 未处理批次 / daily 已投递日报 / interview 单场面试。

**算一次存一次**：结果落 `analysis_reports`——batch/daily 存块流报告、interview 存叙事报告；
读不到才补算。由 `refresh_*` 入口统一做「有则读、无则算」的门控，避免重复烧 token。

**2026-10-11（ADR 0023）重新定位为「供给分析」**：批次分析只分析爬来的岗位供给本身，
**不再产出「改简历」的处方**（原 `optimization_pending` / change_records origin=job_analysis
一条路径已删）——处方大半是技能类、无数据支撑（投递前拿不到 JD）。改进建议仍由聊天 agent
的 `suggest_improvements` 出，不受影响。
"""

import json
import time

from datetime import UTC, date, datetime, timedelta
from typing import Any

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.agents.analysis import BatchAnalysisOut  # noqa: F401 - re-export 兼容既有引用/测试
from app.core.errors import EmptyOutputError
from app.core.logging import logger
from app.graphs.analysis import run_analysis
from app.prompts import (
    load_analysis_distill_jd_prompt,
    load_analysis_interview_prompt,
)
from app.repositories import analysis as reports_repo
from app.repositories import documents as docs_repo
from app.repositories import jobs as jobs_repo
from app.repositories.facts import list_facts
from app.schemas.analysis import AnalysisReportOut
from app.schemas.analysis_graph import AnalysisState, UserProfile
from app.schemas.jobs import Job
from app.schemas.report_block import ChartBlock, ReportBlock, TextBlock
from app.services.analysis_stats import extract_user_profile
from app.services.llm import llm_service
from app.utils.facts import flatten_facts

# 「已投递」日报的观察窗（往回看 N 天，§11.7.5）——趋势/存量看这段，截至本地日。
DAILY_LOOKBACK_DAYS = 14

# 在途分析防重入（§11.7.5）：同一 (scope, key) 同时在算时，第二次进来直接返回 computing，
# 不排队不重算（LLM 慢且贵，重复踢只该是空转）。单进程内一把锁 + 一个在途集合即可。
_inflight: set[tuple[str, str]] = set()

# 失败留痕 + 冷却重试（2026-10-10）：一轮分析失败后，前端轮询会立刻**再起一个后台任务重跑**
# （每次重跑都烧 LLM、白等）。失败也记一笔，冷却期内路由返回 missing（前端停轮询）；冷却过后
# 允许重试一次（transient 失败能自愈，不用非得重启后端）。
_failed_at: dict[tuple[str, str], float] = {}
_FAILED_RETRY_COOLDOWN_S = 20.0


def is_computing(scope: str, key: str) -> bool:
    """该 (scope, key) 是否正在算（前端轮询到 computing 就继续等）。"""
    return (scope, key) in _inflight


def should_skip_after_failure(scope: str, key: str) -> bool:
    """该 (scope, key) 是否「刚失败、还在冷却期」——是则路由返回 missing，不重跑。

    冷却期一过就把留痕清掉、返回 False（放行一次重试）。用一个短冷却而非永久标记：
    永久标记会让 transient 失败再也自愈不了；无标记则会 1.5s 一次地反复重跑烧 token。
    """
    failed_at = _failed_at.get((scope, key))
    if failed_at is None:
        return False
    if time.monotonic() - failed_at > _FAILED_RETRY_COOLDOWN_S:
        _failed_at.pop((scope, key), None)
        return False
    return True


def reset_inflight_for_tests() -> None:
    """测试用：清空在途集合 + 失败留痕。"""
    _inflight.clear()
    _failed_at.clear()


async def latest_batch_round_id() -> int | None:
    """「本轮那批」的批次键 = jobs 表里最大的 `crawl_round_id`（2026-10-10）。

    旧实现信 kick 返回的进程内轮次号——但那个计数器重启归零、且门控拦下的空踢也占号，
    于是重启 / 被门控后前端会指向一个**没岗位的空轮次**（显示「本批暂无岗位可分析」，
    真报告躺在别处读不到）。改从**持久数据**推：哪一轮真正进了岗位，就以那一轮为本轮那批。
    无任何岗位 → None（路由据此返回 missing）。
    """
    jobs = await jobs_repo.list_jobs()
    rounds = [j.crawl_round_id for j in jobs if j.crawl_round_id is not None]
    return max(rounds) if rounds else None


# ---------------------------------------------------------------------------
# LLM 结构化输出 schema
# ---------------------------------------------------------------------------
# BatchAnalysisOut / DailyAnalysisOut 已上移 agents/analysis.py（analyze Agent 的产出契约）。
# DistilledJD（逐岗提炼）/ NarrativeOut（日报/面试纯叙事）是本 service 线性路径的产出，仍在此。


class DistilledJD(BaseModel):
    """逐岗 JD 提炼产出（job51 有 JD 的那批）。"""

    skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    seniority: str = ""
    highlights: list[str] = Field(default_factory=list)


class NarrativeOut(BaseModel):
    """纯叙事报告（日报 / 面试准备——只有嘱咐，没有处方）。"""

    headline: str = ""
    body: str = ""


# ---------------------------------------------------------------------------
# 本地日（坑：代码全 UTC，但"今天"是本地日 UTC+8，§11.7.5）
# ---------------------------------------------------------------------------

# 本地时区偏移（小时）。单用户中国本地应用——纯 UTC 会把今天投的岗算进昨天。
_LOCAL_TZ_OFFSET_HOURS = 8


def local_day_of(dt: datetime) -> str:
    """一个时间点落在哪个**本地日**（YYYY-MM-DD，UTC+8）——日报 scope_key 的取值。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (dt + timedelta(hours=_LOCAL_TZ_OFFSET_HOURS)).date().isoformat()


def _yesterday_local() -> str:
    """本地日的"昨天"（日报截至此日，§11.7.5）。"""
    now_local = datetime.now(UTC) + timedelta(hours=_LOCAL_TZ_OFFSET_HOURS)
    return (now_local - timedelta(days=1)).date().isoformat()


def daily_scope_key() -> str:
    """日报的缓存键（本地日的昨天）——路由/前端作查询键用（公开，不暴露私有实现）。"""
    return _yesterday_local()


# ---------------------------------------------------------------------------
# 渲染：把岗位渲染成喂 LLM 的文本
# ---------------------------------------------------------------------------


def _job_line(j: Job) -> str:
    """一行岗位摘要（标题/公司/城市/薪资/经验/学历 + 来源查询组）。

    2026-10-11（ADR 0023）删掉 `技能标签` 尾巴：`j.skills` 恒为空（猎聘搜索 API 不返回
    标签，见 ADR 0019 实测），此尾巴是**死代码**；且提示词抬头写「结构化标签」会让模型
    以为手里有标签、拿标题脑补。两个谎一并撤掉，模型面前就是它真正拿得到的那几个字段。

    带 `found_by_query`（召回它的检索词组，**首触即定**）：供给分析要答「哪组检索词捞歪了」，
    必须让模型看得到「每个岗是哪组词捞的」。
    """
    parts = [p for p in (j.title, j.company, j.city, j.salary_text, j.experience, j.degree) if p]
    line = f"- {' | '.join(parts)}"
    if j.found_by_query:
        line += f"  〖检索词：{j.found_by_query}〗"
    return line


async def _facts_text() -> str:
    """当前 active facts 的展平文本（喂分析用的"用户材料"）。"""
    facts = await list_facts(status="active")
    return flatten_facts(facts, bullet_title=True)


# ---------------------------------------------------------------------------
# batch：未处理批次分析（本轮那批）
# ---------------------------------------------------------------------------


async def _distill_with_jd(jobs: list[Job]) -> list[str]:
    """job51 那批：逐岗读 JD 提炼 → 合并成一段结构化摘要（§11.7.4，~1 次/岗）。"""
    lines: list[str] = []
    for j in jobs:
        try:
            distilled = await llm_service.call(
                [HumanMessage(content=load_analysis_distill_jd_prompt(j.description))],
                response_format=DistilledJD,
            )
        except Exception:  # noqa: BLE001 - 单岗提炼失败不该拖垮整批
            logger.warning("analysis_distill_failed", job_id=j.id)
            distilled = None
        bits: list[str] = [f"- {j.title} @ {j.company}（{j.city}）"]
        if distilled is not None:
            if distilled.skills:
                bits.append(f"  要求：{'、'.join(distilled.skills)}")
            if distilled.responsibilities:
                bits.append(f"  职责：{'；'.join(distilled.responsibilities[:3])}")
            if distilled.seniority:
                bits.append(f"  门槛：{distilled.seniority}")
            if distilled.highlights:
                bits.append(f"  加分：{'；'.join(distilled.highlights[:2])}")
        lines.append("\n".join(bits))
    return lines


async def read_batch(round_id: int) -> AnalysisReportOut | None:
    """读缓存的批次报告；没算过返回 None（§11.7.5 有则读）。"""
    cached = await reports_repo.get_report("batch", str(round_id))
    if cached is None:
        return None
    content, created = cached
    return _batch_out_from_content(str(round_id), content, created)


async def batch_has_input(round_id: int) -> bool:
    """本轮是否真有岗位可分析。

    没有就别起后台任务——否则后台算完发现空手、不写报告，前端会永远轮询 computing。
    路由用它区分 "computing（真在算）" 与 "missing（这轮没东西可算）"。
    """
    return bool(await jobs_repo.list_jobs(crawl_round_id=round_id))


async def summarize_and_analyze_batch(round_id: int, *, force: bool = False) -> AnalysisReportOut | None:
    """未处理 tab 的批次分析：读本轮那批岗位 → 总览报告（落库）。

    缓存键 = 轮次 id（`scope=batch, scope_key=round_id`）。`force=False` 且已有报告 → 直接读。
    本轮无岗位 → 返回 None（不写空报告；UI 显示"本批暂无岗位"）。
    失败留痕：跑挂了记 `_failed`，路由据此返回 missing（前端停轮询，别再反复重跑烧 token）。
    """
    key = str(round_id)
    if not force:
        hit = await read_batch(round_id)
        if hit is not None:
            return hit

    _inflight.add(("batch", key))
    _failed_at.pop(("batch", key), None)
    try:
        return await _compute_batch(round_id, key)
    except BaseException:  # noqa: BLE001 - 失败也留痕（含取消），记完原样抛出
        _failed_at[("batch", key)] = time.monotonic()
        raise
    finally:
        _inflight.discard(("batch", key))


async def _compute_batch(round_id: int, key: str) -> AnalysisReportOut | None:
    jobs = await jobs_repo.list_jobs(crawl_round_id=round_id)
    if not jobs:
        logger.info("analysis_batch_empty_round", round_id=round_id)
        return None

    state = await run_analysis(
        AnalysisState(scope="batch", scope_key=key, round_id=round_id),
        load_jobs=lambda rid: jobs_repo.list_jobs(crawl_round_id=rid),
        prepare_materials=_prepare_batch_materials,
        persist=_persist_batch,
    )
    return AnalysisReportOut(
        scope="batch",
        scope_key=key,
        headline=state.headline,
        blocks=state.blocks,
        meta=state.meta,
    )


async def _prepare_batch_materials(jobs: list[Job], scope: str) -> dict[str, Any]:
    """喂 analyze 的素材：facts + 岗位摘要（含 job51 逐岗 JD 提炼，重活留 service）+ 口径注 + 用户画像。

    用户门槛画像（年限/学历）从**当前简历成品**（`resume_json`，DB 访问留 service）现算——
    compute_stats 据此算门槛对表（ADR 0023）。无简历 → None（门槛不做，报告诚实降级）。
    """
    liepin_jobs = [j for j in jobs if j.source == "liepin"]
    job51_jobs = [j for j in jobs if j.source == "job51"]

    sections: list[str] = []
    if liepin_jobs:
        sections.append("# 猎聘（搜索列表字段，无 JD）")
        sections.extend(_job_line(j) for j in liepin_jobs)
    if job51_jobs:
        sections.append("")
        sections.append("# 前程无忧（JD 全文）")
        sections.extend(await _distill_with_jd(job51_jobs))
    round_summary = "\n".join(sections)

    # 「深度/概览」覆盖情况——别让用户以为覆盖不全都一样深（§11.7.4）。
    with_jd = sum(1 for j in job51_jobs if j.jd_status == "fetched")
    tone_note = (
        f"（本批 {len(jobs)} 个岗位：猎聘 {len(liepin_jobs)} 个为概览（无 JD），"
        f"前程无忧 {len(job51_jobs)} 个中 {with_jd} 个有 JD 可深度分析。）"
    )
    return {
        "facts": await _facts_text(),
        "round_summary": round_summary,
        "tone_note": tone_note,
        "user_profile": await _user_profile(),
    }


async def _user_profile() -> UserProfile | None:
    """用户的门槛画像（从当前简历成品 JSON 现算；无简历 → None）。"""
    doc = await docs_repo.latest_document()
    if doc is None:
        return None
    return extract_user_profile(doc.resume_json, today=date.today())


async def _persist_batch(state: AnalysisState) -> None:
    """落库：报告（含块流）→ analysis_reports。

    2026-10-11（ADR 0023）：原「处方 → change_records（origin=job_analysis）」一条路径已删——
    批次分析不再产处方。落库只剩报告本身。
    """
    content = json.dumps(
        {
            "headline": state.headline,
            "blocks": [b.model_dump() for b in state.blocks],
            "meta": state.meta,
        },
        ensure_ascii=False,
    )
    await reports_repo.save_report("batch", state.scope_key, content)
    logger.info("analysis_batch_done", scope_key=state.scope_key, total=state.meta.get("total"))


def _parse_blocks(data: dict) -> list[ReportBlock]:
    """Content JSON → 正文块流。

    优先读新版 `blocks`；坏块（kind 不认得 / chart spec 配错）单独丢弃、不拖垮整份报告。
    兼容旧缓存：没有 `blocks` 只有 `body` 时 → 折成单个 text 块（旧报告照样能读）。
    """
    raw = data.get("blocks")
    if isinstance(raw, list):
        blocks: list[ReportBlock] = []
        for item in raw:
            try:
                if not isinstance(item, dict):
                    continue
                if item.get("kind") == "text":
                    md = str(item.get("md", ""))
                    if md.strip():
                        blocks.append(TextBlock(md=md))
                elif item.get("kind") == "chart":
                    blocks.append(ChartBlock(spec=item["spec"]))  # ChartSpec 校验在此拦坏 spec
            except Exception:  # noqa: BLE001 - 坏块丢弃，不拖垮整份报告
                logger.warning("analysis_block_invalid")
        return blocks
    body = str(data.get("body", ""))
    return [TextBlock(md=body)] if body.strip() else []


def _batch_out_from_content(key: str, content: str, created: str) -> AnalysisReportOut:
    """JSON 文本 → 读模型（缓存命中路径）。块流经 _parse_blocks 兜底（坏块丢、旧 body 兼容）。"""
    data = json.loads(content)
    return AnalysisReportOut(
        scope="batch",
        scope_key=key,
        headline=str(data.get("headline", "")),
        blocks=_parse_blocks(data),
        meta=data.get("meta", {}),
        created_at=created,
    )


# ---------------------------------------------------------------------------
# daily：已投递投递日报（截至昨天）
# ---------------------------------------------------------------------------


async def read_daily() -> AnalysisReportOut | None:
    """读缓存的日报（scope_key = 本地日的昨天）；没算过返回 None。"""
    day = _yesterday_local()
    cached = await reports_repo.get_report("daily", day)
    if cached is None:
        return None
    content, created = cached
    return _narrative_out("daily", day, content, created)


async def daily_has_input() -> bool:
    """窗口内（截至昨天、往回 N 天）是否真有投递活动可分析。

    没有就别起后台任务——否则后台算完发现空手、不写报告，前端会永远轮询 computing。
    路由用它区分 "computing（真在算）" 与 "missing（这段没东西可算）"，与 batch/interview 对称。
    取数口径与 `_load_daily_material` 一致（同一窗口）。
    """
    material = await _load_daily_material(_yesterday_local())
    return bool(material.get("daily_events"))


async def refresh_daily(*, force: bool = False) -> AnalysisReportOut:
    """已投递 tab 的日报：走 graph 算一份「截至昨天」的报告（落库缓存）。

    scope_key = **本地日的昨天**（§11.7.5）——报告是日期的纯函数，同一日期永只算一份、
    永不需复算（用户定：日报就该是"昨天发生了什么"，不在上班中间自己更新）。

    2026-09-15：与 batch 同构——走 `graphs/analysis.py`（load → prepare → compute_stats →
    analyze → persist），analyze 用带工具位的 Agent，前置统计用代码算（投递**活动**视角），
    产出块流（文/图穿插）。取数/备料/落库由本 service 注入节点（graph/node 不碰 DB）。
    """
    day = _yesterday_local()
    if not force:
        hit = await read_daily()
        if hit is not None:
            return hit

    _inflight.add(("daily", day))
    _failed_at.pop(("daily", day), None)
    try:
        state = await run_analysis(
            AnalysisState(scope="daily", scope_key=day),
            load_daily=_load_daily_material,
            prepare_materials=_prepare_daily_materials,
            persist=_persist_daily,
        )
        return AnalysisReportOut(scope="daily", scope_key=day, headline=state.headline, blocks=state.blocks)
    except BaseException:  # noqa: BLE001 - 失败留痕，防前端轮询反复重跑
        _failed_at[("daily", day)] = time.monotonic()
        raise
    finally:
        _inflight.discard(("daily", day))


async def _load_daily_material(scope_key: str) -> dict:
    """Daily 取数：窗口内的投递活动（跟进事件 + 岗位对）+ 观察窗口径（service 注入节点）。"""
    as_of = scope_key or _yesterday_local()
    end = datetime.fromisoformat(f"{as_of}T23:59:59.999999+08:00")
    start = end - timedelta(days=DAILY_LOOKBACK_DAYS)
    events = await jobs_repo.list_events_in_range(start, end)
    return {"daily_events": events, "daily_as_of": as_of, "daily_window_days": DAILY_LOOKBACK_DAYS}


async def _prepare_daily_materials(events: list, scope: str) -> dict[str, str]:
    """Daily 备料：把窗口内活动渲染成摘要（喂 analyze 的 round_summary）+ 口径注。"""
    as_of = _yesterday_local()
    return {"round_summary": _render_daily_summary(events, as_of), "tone_note": "", "facts": ""}


async def _persist_daily(state: AnalysisState) -> None:
    """Daily 落库：报告（含块流）→ analysis_reports。日报无处方。"""
    content = json.dumps(
        {"headline": state.headline, "blocks": [b.model_dump() for b in state.blocks]},
        ensure_ascii=False,
    )
    await reports_repo.save_report("daily", state.scope_key, content)
    logger.info("analysis_daily_done", day=state.scope_key, total_events=len(state.daily_events or []))


# 「不再追踪」原因的人话（喂日报素材）。
_REASON_LABEL: dict[str, str] = {
    "withdrawn": "主动放弃",
    "failed": "未通过",
    "job_closed": "岗位关闭",
    "duplicate": "重复投递",
}


def _render_daily_summary(events: list, day: str) -> str:
    """把窗口内投递活动（事件 + 岗位对）渲染成日报素材文本。

    2026-10-11（ADR 0023）补 `ev.reason`（不再追踪的原因）进素材——§11.4 早说「拒绝理由有
    研究价值」，旧实现却只按 status 分组、拒因没喂给模型，日报于是讲不了「为什么没成」。
    """
    if not events:
        return "（最近没有投递活动记录）"
    lines = [f"截至 {day} 的投递活动（往回看 {DAILY_LOOKBACK_DAYS} 天）："]
    by_status: dict[str, list] = {}
    for ev, job in events:
        by_status.setdefault(ev.status, []).append((ev, job))
    label = {
        "applied": "已投递",
        "interviewing": "待面试",
        "offered": "录用",
        "not_pursuing": "不再追踪",
    }
    for st, pairs in by_status.items():
        lines.append(f"\n## {label.get(st, st)}（{len(pairs)} 个）")
        for ev, job in pairs[:10]:
            line = _job_line(job)
            if ev.reason:
                line += f"｜原因：{_REASON_LABEL.get(ev.reason, ev.reason)}"
            lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# interview：单场面试准备
# ---------------------------------------------------------------------------


async def read_interview(interview_id: int) -> AnalysisReportOut | None:
    """读缓存的单场面试准备；没算过返回 None。"""
    key = str(interview_id)
    cached = await reports_repo.get_report("interview", key)
    if cached is None:
        return None
    content, created = cached
    return _narrative_out("interview", key, content, created)


async def interview_has_input(interview_id: int) -> bool:
    """该场次是否存在（不存在就别起后台任务——同上，免得前端空转）。"""
    return await jobs_repo.get_interview(interview_id) is not None


async def refresh_interview(interview_id: int, *, force: bool = False) -> AnalysisReportOut:
    """面试 tab 的单场准备（展开某场时算一份，scope_key=场次 id）。

    JD 缺失（猎聘侧无 description）→ 诚实标注，只给基于岗位名的通用准备（不硬编）。
    """
    key = str(interview_id)
    if not force:
        hit = await read_interview(interview_id)
        if hit is not None:
            return hit

    _inflight.add(("interview", key))
    _failed_at.pop(("interview", key), None)
    try:
        return await _compute_interview(interview_id, key)
    except BaseException:  # noqa: BLE001 - 失败留痕，防前端轮询反复重跑
        _failed_at[("interview", key)] = time.monotonic()
        raise
    finally:
        _inflight.discard(("interview", key))


async def _compute_interview(interview_id: int, key: str) -> AnalysisReportOut:
    iv = await jobs_repo.get_interview(interview_id)
    if iv is None:
        raise EmptyOutputError("面试场次不存在")
    job = await jobs_repo.get_job(iv.job_id)
    company = job.company if job else ""
    title = job.title if job else ""
    jd = job.description if job else ""

    prompt = load_analysis_interview_prompt(
        company=company,
        title=title,
        scheduled_at=iv.scheduled_at,
        jd=jd,
        facts=await _facts_text(),
    )
    result = await llm_service.call([HumanMessage(content=prompt)], response_format=NarrativeOut)
    if not (result.headline.strip() or result.body.strip()):
        raise EmptyOutputError("面试准备产出空报告")
    content = json.dumps({"headline": result.headline, "body": result.body}, ensure_ascii=False)
    await reports_repo.save_report("interview", key, content)
    logger.info("analysis_interview_done", interview_id=interview_id, has_jd=bool(jd))
    return AnalysisReportOut(
        scope="interview",
        scope_key=key,
        headline=result.headline,
        blocks=[TextBlock(md=result.body)] if result.body.strip() else [],
    )


def _narrative_out(scope: str, key: str, content: str, created: str) -> AnalysisReportOut:
    """JSON 文本 → 读模型（纯叙事报告：daily / interview）。

    daily 已 graph 化 → 存 `blocks`；interview 仍线性 → 存 `body`。两者都经 `_parse_blocks`
    兜底（新版读 blocks、旧版把 body 折成单个 text 块、坏块丢弃）。
    """
    data = json.loads(content)
    return AnalysisReportOut(
        scope=scope,  # type: ignore[arg-type]
        scope_key=key,
        headline=str(data.get("headline", "")),
        blocks=_parse_blocks(data),
        created_at=created,
    )
