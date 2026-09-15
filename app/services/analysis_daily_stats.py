"""已投递**日报**的前置统计：把投递活动算成 DailyStatsPack（确定性、不调 LLM、不碰 DB）。

与 batch 的 `analysis_stats.compute_stats`（岗位**画像**：技能/城市/薪资）互补——日报讲的是
「投递这件事本身」的起伏，故统计的是**跟进事件**（状态分布 / 来源分布 / 逐日投递趋势 / 响应率）。
统计用代码算（零幻觉、零成本），既是 analyze Agent 的 prompt 素材，又是日报配图的数据源。

纯函数：吃「事件 + 岗位」对（service 从 repo 取好喂进来），吐 DailyStatsPack。**不碰 DB**。
"""

from collections import Counter

from datetime import datetime, timedelta

from app.schemas.analysis_graph import DailyStatsPack
from app.schemas.jobs import Job, JobFollowupEvent
from app.schemas.market import DistributionSlice
from app.services.market import _distribution

# 状态展示标签（与前端 applyLabels 同口径，喂 LLM / 配图用）。
_STATUS_LABEL: dict[str, str] = {
    "applied": "已投递",
    "interviewing": "待面试",
    "interviewed": "已面试",
    "offered": "录用",
    "not_pursuing": "不再追踪",
}
# 来源展示标签。
_SOURCE_LABEL: dict[str, str] = {"liepin": "猎聘", "job51": "前程无忧"}
# 「有回音」的当前态：进入面试流程或拿到录用（not_pursuing 可能是被拒，不算回音）。
_RESPONDED_STATUSES = {"interviewing", "interviewed", "offered"}


def _day_of(dt: datetime) -> str:
    """本地日（UTC+8）的 YYYY-MM-DD——与 service 的 local_day_of 同口径（趋势按本地日切）。"""
    return (dt + timedelta(hours=8)).date().isoformat()


def compute_daily_stats(
    events: list[tuple[JobFollowupEvent, Job]],
    *,
    as_of: str,
    window_days: int,
) -> DailyStatsPack:
    """把窗口内的投递活动聚合成 DailyStatsPack（纯函数，可单测）。

    events = service 取好的「跟进事件 + 岗位」对；as_of = 截至本地日；window_days = 观察窗。
    """
    status_counts: Counter = Counter()
    source_counts: Counter = Counter()
    trend_counts: Counter = Counter()
    applied = responded = 0
    for ev, job in events:
        status_counts[ev.status] += 1
        if job.source:
            source_counts[job.source] += 1
        trend_counts[_day_of(datetime.fromisoformat(ev.at))] += 1
        if ev.status == "applied":
            applied += 1
        elif ev.status in _RESPONDED_STATUSES:
            responded += 1

    as_of_dt = datetime.fromisoformat(f"{as_of}T00:00:00+08:00")
    days = [(as_of_dt - timedelta(days=i)).date().isoformat() for i in range(window_days - 1, -1, -1)]

    return DailyStatsPack(
        window_days=window_days,
        as_of=as_of,
        total_events=len(events),
        # 状态切成展示标签（LLM 读得懂、配图 x 轴直接可用）。
        by_status=[DistributionSlice(label=_STATUS_LABEL.get(s.label, s.label), count=s.count) for s in _distribution(status_counts)],
        by_source=[DistributionSlice(label=_SOURCE_LABEL.get(s.label, s.label), count=s.count) for s in _distribution(source_counts)],
        daily_trend=[DistributionSlice(label=d, count=trend_counts.get(d, 0)) for d in days],  # 逐日补齐（含 0，折线不断）
        applied_count=applied,
        responded_count=responded,
        response_rate=round(responded / applied, 3) if applied else 0.0,
    )


def render_daily_stats_summary(stats: DailyStatsPack) -> str:
    """把 DailyStatsPack 渲染成喂 analyze Agent 的统计文本（纯文本给 LLM 读）。"""
    lines = [f"观察窗：截至 {stats.as_of}、往回 {stats.window_days} 天（共 {stats.total_events} 个投递活动）"]
    for title, slices in (
        ("当前状态分布", stats.by_status),
        ("来源分布", stats.by_source),
    ):
        if slices:
            body = "、".join(f"{s.label}×{s.count}" for s in slices)
            lines.append(f"{title}：{body}")
    responded = f"，其中 {stats.responded_count} 个有回音"
    rate = f"，响应率 {stats.response_rate:.0%}" if stats.applied_count else ""
    lines.append(f"投递（applied）：{stats.applied_count} 个{responded}{rate}")
    return "\n".join(lines)
