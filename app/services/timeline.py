"""时间线计算（calc_timeline 的领域逻辑）：时长 / 距今 / 空窗 gap / 重叠。

纯逻辑、不碰 DB、不知道 LLM 存在（分层铁律）。被 chat_tools 的 calc_timeline 工具调用：
agent 把一组时间段（start/end，end 可为「至今」）喂进来，拿回算好的结果做权重/gap/重叠判断，
不靠心算（跨年、含「至今」、多段求并集都易错）。

calc 与 render 分离（参考 market 的 _title_hits / render_snapshot）：calc 出结构化结果，
render 成给 agent 看的可读文本。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

# 「至今」的各种写法 → 按 today 算（该段进行中）
_PRESENT_ALIASES = {"至今", "现在", "目前", "今", "present", "now", "current", "today", ""}


@dataclass
class TimelinePeriod:
    """一段输入时间线。start/end 支持 "YYYY"、"YYYY-MM"、"YYYY-MM-DD"；end 为「至今」表进行中。"""

    start: str
    end: str | None = None  # None/「至今」= 进行中
    label: str = ""  # 这段叫什么（公司/项目名），便于在报告里指认


@dataclass
class PeriodResult:
    label: str
    start: str
    end: str  # 「至今」原样保留（可读）
    months: int  # 时长（含两端月份）
    duration_text: str  # 「X 年 Y 个月」
    is_current: bool  # end 是否「至今」
    ago_text: str  # end 距今多久（「2 年 3 个月前」/「至今（进行中）」）


@dataclass
class GapResult:
    after_label: str  # 较早的一段（gap 在它结束之后）
    before_label: str  # 较晚的一段（gap 在它开始之前）
    months: int
    # 空窗类型（2026-09-21）：「当前空窗」（下一段至今在进行 = 上一份结束到现在还没入职）
    # 与「历史空窗」（下一段也已结束 = 早已翻篇）的敏感度不同，标出来供 agent 判断权
    # 参考刻度（大致量级，非硬阈值）：当前空窗 ~200 天、历史空窗 ~300 天值得聊。
    is_current: bool = False


@dataclass
class OverlapResult:
    a_label: str
    b_label: str
    months: int  # 重叠时长


@dataclass
class TimelineReport:
    periods: list[PeriodResult] = field(default_factory=list)
    gaps: list[GapResult] = field(default_factory=list)
    overlaps: list[OverlapResult] = field(default_factory=list)
    total_months: int = 0  # 总工龄（并集月数，重叠不重复计）


def _to_month_index(d: date) -> int:
    """日期 → 自公元 0 年的月份序号（含月，忽略日）。月级精度的比较/求差都走它。"""
    return d.year * 12 + (d.month - 1)


def _parse_bound(raw: str, *, is_end: bool) -> date | None:
    """解析一个时间端点成日期。无法解析 / 「至今」返回 None（由调用方按 today 兜底）。

    - "YYYY"     → start 取年初 01-01，end 取年末 12-31（年份含糊，按整年计）
    - "YYYY-MM"  → start 取当月 1 日，end 取当月末日
    - "YYYY-MM-DD" → 原样
    """
    s = raw.strip()
    if s.lower() in _PRESENT_ALIASES:
        return None
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", s)
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else (12 if is_end else 1)
    if m.group(3):
        day = int(m.group(3))
    else:
        # end 缺日：取该月最后一天（30/31 依月份，2 月取 28 不判闰年——月级精度足够）
        day = 31 if is_end and month in (1, 3, 5, 7, 8, 10, 12) else (30 if is_end and month != 2 else (28 if is_end else 1))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _fmt_duration(months: int) -> str:
    """月数 → 「X 年 Y 个月」可读串。"""
    if months <= 0:
        return "不足 1 个月"
    years, rem = divmod(months, 12)
    if years and rem:
        return f"{years} 年 {rem} 个月"
    if years:
        return f"{years} 年"
    return f"{rem} 个月"


def _is_present(end: str | None) -> bool:
    return end is None or end.strip().lower() in _PRESENT_ALIASES


def calc_timeline(periods: list[TimelinePeriod], *, today: str) -> TimelineReport:
    """算一组时间段：时长、距今、相邻 gap、重叠、总工龄（并集）。

    today：「今天」的日期串（YYYY-MM-DD）——「至今」/距今都按它算。由工具注入当天真实日期，
    调用方（agent）不用关心现时刻。
    """
    today_d = date.fromisoformat(today)
    today_m = _to_month_index(today_d)

    # 逐段解析成月序号区间 [start_m, end_m]，无法解析的段跳过（不连坐整批）
    parsed: list[tuple[TimelinePeriod, int, int]] = []
    for p in periods:
        start_d = _parse_bound(p.start, is_end=False)
        if start_d is None:
            continue
        end_d = today_d if _is_present(p.end) else _parse_bound(p.end or "", is_end=True)
        if end_d is None:
            continue
        start_m, end_m = _to_month_index(start_d), _to_month_index(end_d)
        if end_m < start_m:  # 端点颠倒的坏数据，跳过
            continue
        parsed.append((p, start_m, end_m))

    results: list[PeriodResult] = []
    for p, start_m, end_m in parsed:
        # 时长 = end - start（含头不含尾）：1月到次年1月 = 12 个月 = 1 年，符合在职时长惯例。
        months = end_m - start_m
        current = _is_present(p.end)
        ago = "至今（进行中）" if current else f"{_fmt_duration(today_m - end_m)}前"
        results.append(
            PeriodResult(
                label=p.label,
                start=p.start,
                end=(p.end or "").strip() or "至今",
                months=months,
                duration_text=_fmt_duration(months),
                is_current=current,
                ago_text=ago,
            )
        )

    # 按开始时间排序，逐对相邻段算 gap / 重叠
    ordered = sorted(parsed, key=lambda t: t[1])
    gaps: list[GapResult] = []
    overlaps: list[OverlapResult] = []
    for i in range(len(ordered) - 1):
        (pa, sa, ea), (pb, sb, eb) = ordered[i], ordered[i + 1]
        # 间隔 sb - ea：>1 才有完整空月（==1 = 首尾相接，如 6 月结束 7 月开始，正常衔接不算空窗）。
        # gap 月数 = 两段之间的完整空月数 = sb - ea - 1。**不设阈值**——算不算「值得聊的空窗」
        # 是 agent 的判断（299 天与 300 天无本质区别），工具只负责如实算出并标类型（当前/历史）。
        if sb - ea > 1:
            gaps.append(
                GapResult(
                    after_label=pa.label,
                    before_label=pb.label,
                    months=sb - ea - 1,
                    is_current=_is_present(pb.end),
                )
            )
        elif sb < ea:
            # 重叠：b 开始早于 a 结束 → 重叠月到 min(ea, eb)（含头不含尾）
            overlaps.append(OverlapResult(a_label=pa.label, b_label=pb.label, months=min(ea, eb) - sb))

    # 当前空窗：最新一段已结束、且结束到「今天」之间没有进行中的下一段 → 上一份结束到现在还没入职。
    # 这是尾部状态，不是段间 gap，单独判。空月 = 结束月到今天的完整月数 - 1（与段间同口径）。
    if ordered:
        latest = max(parsed, key=lambda t: t[2])  # 结束最晚的一段
        lp, _, le = latest
        if not _is_present(lp.end) and today_m - le > 1:
            gaps.append(
                GapResult(
                    after_label=lp.label,
                    before_label="至今",
                    months=today_m - le - 1,
                    is_current=True,
                )
            )

    # 总工龄 = 各段月区间的并集大小（重叠不重复计）。区间 [start, end) 含头不含尾。
    covered: set[int] = set()
    for _, start_m, end_m in parsed:
        covered.update(range(start_m, end_m))

    return TimelineReport(periods=results, gaps=gaps, overlaps=overlaps, total_months=len(covered))


def render_timeline_report(report: TimelineReport) -> str:
    """把 TimelineReport 渲染成给 agent 看的可读文本（工具返回值）。"""
    if not report.periods:
        return "没有可计算的时间段（检查 start/end 格式：YYYY 或 YYYY-MM，「至今」表进行中）。"

    lines: list[str] = []
    for p in report.periods:
        span = f"{p.start} ~ {p.end}"
        label = f"「{p.label}」" if p.label else ""
        lines.append(f"- {label}{span}：时长 {p.duration_text}，距今 {p.ago_text}")

    if report.gaps:
        for g in report.gaps:
            a = f"「{g.after_label}」" if g.after_label else "上一段"
            # 当前空窗 = 下一段至今在进行（上一份结束到现在还没入职），敏感度高，标出来。
            if g.is_current:
                lines.append(f"空窗（当前）：{a} 结束到现在空了 {_fmt_duration(g.months)}，这段还没入职下一份。")
            else:
                b = f"「{g.before_label}」" if g.before_label else "下一段"
                lines.append(f"空窗（历史）：{a} 结束到 {b} 开始之间空了 {_fmt_duration(g.months)}。")
    if report.overlaps:
        for o in report.overlaps:
            a = f"「{o.a_label}」" if o.a_label else "一段"
            b = f"「{o.b_label}」" if o.b_label else "另一段"
            lines.append(f"重叠：{a} 与 {b} 时间重叠了 {_fmt_duration(o.months)}——若都是全职经历则矛盾，需跟用户核对纠正。")

    lines.append(f"总工龄（并集，重叠不重复计）：{_fmt_duration(report.total_months)}。")
    return "\n".join(lines)
