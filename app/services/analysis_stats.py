"""前置统计：把一批岗位算成 StatsPack（确定性、不调 LLM、不碰 DB）。

「前置动作用代码做」是 2026-09-15 定稿的架构——统计用代码算最稳最快（零幻觉、零成本），
算好打包喂给 analyze Agent：既是 prompt 素材，又是 charts 的数据源。薪资分桶/解析直接复用
`services/market.py`（同一套口径，别另起炉灶）。
"""

from collections import Counter

from app.schemas.analysis_graph import StatsPack
from app.schemas.jobs import Job
from app.schemas.market import DistributionSlice
from app.services.market import _distribution, _parse_salary_to_k, bucket_salary

# 技能词频只保留 top N（够 analyze 看共性、配图用，不刷屏）。
_TOP_SKILLS = 15


def _slice_text(title: str, slices: list[DistributionSlice]) -> str:
    """一段分布渲染成「标题：A×n、B×n」一行（喂 prompt 用）。"""
    if not slices:
        return ""
    body = "、".join(f"{s.label}×{s.count}" for s in slices)
    return f"{title}：{body}"


def render_stats_summary(stats: StatsPack) -> str:
    """把 StatsPack 渲染成喂 analyze Agent 的统计文本（配图的取数来源）。

    纯文本、给 LLM 读；结构化数据仍在 stats 里（配图时按 label/count 取）。
    """
    lines = [f"岗位总数：{stats.total}（猎聘 {stats.liepin} / 前程无忧 {stats.job51}，其中 {stats.with_jd} 个有 JD）"]
    for line in (
        _slice_text("技能词频", stats.skill_freq),
        _slice_text("城市分布", stats.by_city),
        _slice_text("学历分布", stats.by_degree),
        _slice_text("经验分布", stats.by_experience),
        _slice_text("薪资段", stats.salary_buckets),
    ):
        if line:
            lines.append(line)
    return "\n".join(lines)


def compute_stats(jobs: list[Job]) -> StatsPack:
    """把一批岗位聚合成 StatsPack（纯函数，可单测）。空字段不计入分布。"""
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
    )
