"""前置统计：把一批岗位算成 StatsPack（确定性、不调 LLM、不碰 DB）。

「前置动作用代码做」是 2026-09-15 定稿的架构——统计用代码算最稳最快（零幻觉、零成本），
算好打包喂给 analyze Agent：既是 prompt 素材，又是 charts 的数据源。薪资分桶/解析直接复用
`services/market.py`（同一套口径，别另起炉灶）。

2026-10-11（ADR 0023）加门槛对表：投递分析重新定位为**供给分析**，第一段判词要答
「这批岗你要不要得起」——`extract_user_profile` 从简历 JSON 现算用户年限/学历，
`compute_gate` 与岗位 experience/degree 做确定性三档对表（达标/差一点/明显不够）。
"""

import json
import re

from collections import Counter
from datetime import date
from typing import Any

from app.schemas.analysis_graph import GatePack, StatsPack, UserProfile
from app.schemas.jobs import Job
from app.schemas.market import DistributionSlice
from app.services.market import _distribution, _parse_salary_to_k, bucket_salary
from app.utils.job_match import (
    DEGREE_LABEL,
    degree_rank,
    gate_of_job,
)

# 技能词频只保留 top N（够 analyze 看共性、配图用，不刷屏）。
_TOP_SKILLS = 15


def _slice_text(title: str, slices: list[DistributionSlice]) -> str:
    """一段分布渲染成「标题：A×n、B×n」一行（喂 prompt 用）。"""
    if not slices:
        return ""
    body = "、".join(f"{s.label}×{s.count}" for s in slices)
    return f"{title}：{body}"


def _render_gate(gate: GatePack | None) -> str:
    """门槛对表渲染成喂 prompt 的一行（无画像 / 无明确门槛 → 空串，别占位）。"""
    if gate is None:
        return ""
    if gate.exp_known == 0 and gate.deg_known == 0:
        return ""
    bits: list[str] = []
    if gate.exp_known:
        bits.append(f"经验达标 {gate.exp_clear}/{gate.exp_known}")
        if gate.exp_risky:
            bits.append(f"差一点 {gate.exp_risky}")
        if gate.exp_blocked:
            bits.append(f"明显不够 {gate.exp_blocked}")
    if gate.deg_known:
        bits.append(f"学历达标 {gate.deg_clear}/{gate.deg_known}")
    head = f"门槛（我 {gate.my_years if gate.my_years is not None else '?'} 年经验 / {gate.my_degree}）"
    tail = f"；双门槛都达标 {gate.both_clear}/{gate.both_known}" if gate.both_known else ""
    return head + "：" + "、".join(bits) + tail


def render_stats_summary(stats: StatsPack) -> str:
    """把 StatsPack 渲染成喂 analyze Agent 的统计文本（配图的取数来源）。

    纯文本、给 LLM 读；结构化数据仍在 stats 里（配图时按 label/count 取）。
    """
    lines = [f"岗位总数：{stats.total}（猎聘 {stats.liepin} / 前程无忧 {stats.job51}，其中 {stats.with_jd} 个有 JD）"]
    for line in (_render_gate(stats.gate),) + tuple(
        _slice_text(title, slices)
        for title, slices in (
            ("技能词频", stats.skill_freq),
            ("城市分布", stats.by_city),
            ("学历分布", stats.by_degree),
            ("经验分布", stats.by_experience),
            ("薪资段", stats.salary_buckets),
        )
    ):
        if line:
            lines.append(line)
    return "\n".join(lines)


def compute_stats(jobs: list[Job], profile: UserProfile | None = None) -> StatsPack:
    """把一批岗位聚合成 StatsPack（纯函数，可单测）。空字段不计入分布。

    profile 非空时附带门槛对表（ADR 0023）；为 None → `gate=None`（无画像不硬算）。
    """
    skills: Counter = Counter()
    cities: Counter = Counter()
    degrees: Counter = Counter()
    experiences: Counter = Counter()
    salaries: Counter = Counter()
    liepin = job51 = with_jd = 0
    for j in jobs:
        if j.source == "liepin":
            liepin += 1
        elif j.source == "job51":
            job51 += 1
            if j.jd_status == "fetched":
                with_jd += 1
        for sk in j.skills:
            if sk:
                skills[sk] += 1
        if j.city:
            cities[j.city] += 1
        if j.degree:
            degrees[j.degree] += 1
        if j.experience:
            experiences[j.experience] += 1
        parsed = _parse_salary_to_k(j.salary_text)
        salaries["面议" if parsed is None else bucket_salary(*parsed)] += 1

    top_skills = [DistributionSlice(label=k, count=v) for k, v in skills.most_common(_TOP_SKILLS)]
    return StatsPack(
        total=len(jobs),
        liepin=liepin,
        job51=job51,
        with_jd=with_jd,
        skill_freq=top_skills,
        by_city=_distribution(cities),
        by_degree=_distribution(degrees),
        by_experience=_distribution(experiences),
        salary_buckets=_distribution(salaries),
        gate=compute_gate(jobs, profile),
    )


# ---------------------------------------------------------------------------
# 门槛对表（ADR 0023，2026-10-11）：用户年限/学历 × 岗位门槛
# ---------------------------------------------------------------------------


def _parse_ym(text: str) -> tuple[int, int] | None:
    """把日期串解析成 (年, 月)。支持 '2018-03' '2019/07' '2019年9月' '2020'。

    解析不出（'至今' / 空 / 自由文本）→ None。月份缺省按 1 月（起点偏早=年限偏长，保守）。
    """
    t = (text or "").strip()
    m = re.search(r"(\d{4})\D{0,2}(\d{1,2})?", t)
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else 1
    if not 1 <= month <= 12:
        month = 1
    return year, month


def extract_user_profile(resume_json: str, *, today: date) -> UserProfile | None:
    """从简历成品 JSON 现算用户门槛画像（ADR 0023）。

    - 年限 = 最早一段工作的起始 → today（**保守取最早**；口径见 ADR 0023，不用并集——
      简历有跳槽空窗时并集算出的数会低于 HR 目测，伤「敢不敢投」的判断）。
    - 学历 = 最高档（比较时归一：统招本科→本科）。
    - 空 / 坏 JSON / 无 work 段 → 相应字段空（无 work = 应届生，years=0.0）。
    全空（无简历）→ None（门槛整体不做）。
    """
    if not resume_json or not resume_json.strip():
        return None
    try:
        data = json.loads(resume_json)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    raw_work = data.get("work")
    work: list[Any] = raw_work if isinstance(raw_work, list) else []
    raw_education = data.get("education")
    education: list[Any] = raw_education if isinstance(raw_education, list) else []
    raw_basics = data.get("basics")
    basics: dict[str, Any] = raw_basics if isinstance(raw_basics, dict) else {}
    years: float | None = None
    years_basis = ""
    starts = [(ym, w) for w in work if isinstance(w, dict) and (ym := _parse_ym(str(w.get("startDate", ""))))]
    if starts:
        (sy, sm), earliest = min(starts, key=lambda p: p[0])
        months = (today.year - sy) * 12 + (today.month - sm)
        years = round(max(0, months) / 12.0, 1)
        years_basis = f"{earliest.get('name', '')} {sy:04d}-{sm:02d}"
    elif work:
        years = None  # 有工作段但日期都解析不出 → 年限未知
    else:
        years = 0.0  # 无工作段 = 应届/零经验

    degree, degree_r = "", 0
    for e in education:
        if not isinstance(e, dict):
            continue
        raw = str(e.get("studyType", "")).strip()
        r = degree_rank(raw) or 0
        if r > degree_r:
            degree, degree_r = DEGREE_LABEL.get(r, raw), r

    raw_loc = basics.get("location")
    location: dict[str, Any] = raw_loc if isinstance(raw_loc, dict) else {}
    return UserProfile(
        years=years,
        years_basis=years_basis,
        degree=degree,
        degree_rank=degree_r,
        target=str(basics.get("label", "")).strip(),
        city=str(location.get("city", "")).strip(),
    )


def compute_gate(jobs: list[Job], profile: UserProfile | None) -> GatePack | None:
    """用户 × 岗位门槛三档对表（纯函数）。无画像 → None。门槛不明确的岗不进分母。

    单岗判定复用 `utils/job_match.gate_of_job`（与匹配度排序同一套口径）——两处不分家。
    """
    if profile is None:
        return None
    gate = GatePack(
        my_years=profile.years,
        my_degree=profile.degree,
        my_target=profile.target,
        my_city=profile.city,
    )
    for j in jobs:
        exp, deg = gate_of_job(j, profile)
        if exp != "unknown":
            gate.exp_known += 1
            if exp == "clear":
                gate.exp_clear += 1
            elif exp == "risky":
                gate.exp_risky += 1
            else:
                gate.exp_blocked += 1
        if deg != "unknown":
            gate.deg_known += 1
            if deg == "clear":
                gate.deg_clear += 1
            else:
                gate.deg_blocked += 1
        if exp != "unknown" and deg != "unknown":
            gate.both_known += 1
            if exp == "clear" and deg == "clear":
                gate.both_clear += 1
    return gate
