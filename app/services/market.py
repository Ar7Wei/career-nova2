"""market service：现场查猎聘市场（ADR 0007 / 0018）。

query_market = 简历 agent 的工具——现场调猎聘查市场客观统计，只吐原始统计、不做判断。
判断（你 vs 市场缺什么）由 agent 拿统计比对已注入 facts 自己讲出。样本仅猎聘，诚实标注。

ADR 0018：另附每组**召回诊断**（原始计数）——平台对多词是「自适应 AND」，交集不足时
静默放宽成 OR，而响应不暴露命中总数。诊断把「这组词实际召回什么」以计数交回 agent。

**工具只吐原始数据，不替 agent 做策略**（ADR 0007 铁律）：想知道「同一岗位在哪座城更好」，
agent 自己**一城一调**、拿两份快照比——不提供「传多城帮你横向对比」的聚合结构。

分层：Service → connector（与 refresh_jobs 同构）。不碰 DB（现场查即弃，不落库不缓存）。
猎聘风控/失败 → 抛 LLMUnavailableError 出声（agent 工具路径 catch 后转文本）。
"""

import re
from collections import Counter

from app.connectors.liepin import LiepinBlockedError, LiepinError, fetch_liepin_page
from app.core.errors import ConflictError, LLMUnavailableError
from app.core.logging import logger
from app.schemas.direction import MAX_KEYWORD_GROUPS
from app.schemas.market import DistributionSlice, GroupDiagnostic, MarketSnapshot, ProbeEstimate

# 召回诊断里每组附带的样本标题条数（够 agent 看噪音性质即可，不刷屏）。
_DIAGNOSTIC_SAMPLE = 6

# 薪资段分桶（按月薪区间中点落桶，半开区间）。GBK 安全：只用中文 + K + 数字，无特殊箭头。
_BUCKETS: list[tuple[float, str]] = [
    (15.0, "15K以下"),
    (25.0, "15-25K"),
    (40.0, "25-40K"),
    (60.0, "40-60K"),
]

_SOURCE_NOTE = "样本仅来自猎聘（BOSS/前程无忧/智联未纳入），不代表全市场。"



def bucket_salary(low: float, high: float) -> str:
    """薪资段分桶：按月薪区间中点落桶（单位 K）。"""
    mid = (low + high) / 2.0
    for upper, label in _BUCKETS:
        if mid < upper:
            return label
    return "60K以上"


def _parse_salary_to_k(text: str) -> tuple[float, float] | None:
    """把 salary_text 原始串解析成 (低, 高) K 单位；解析不出（面议/空）返回 None。

    支持「30-45K·14薪」「1.5-2万」「8000-12000」等形态。字段不强归一（apply.md §4），
    只能确定性 best-effort 解析，解析失败的归「面议」桶。
    """
    t = (text or "").strip()
    if not t or "面议" in t:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-~到]\s*(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    low, high = float(m.group(1)), float(m.group(2))
    if "万" in t:
        unit = 10.0  # 万 → 10K
    elif "千" in t or "k" in t.lower():
        unit = 1.0  # 千 / K → K
    else:
        unit = 1.0 / 1000.0  # 裸数字按元 → K
    return low * unit, high * unit


def _distribution(counter: Counter) -> list[DistributionSlice]:
    """Counter → 降序分布切片列表（空值已在外层剔除）。"""
    return [DistributionSlice(label=k, count=v) for k, v in counter.most_common()]


def aggregate_market(jobs: list[dict]) -> MarketSnapshot:
    """把一批岗位 dict（连接器归一化后）聚合成市场快照（纯函数，可单测）。

    只吐客观统计：岗位量 / 城市 / 学历 / 年限 / 薪资段。空字段不计入分布。
    """
    cities: Counter = Counter()
    degrees: Counter = Counter()
    experiences: Counter = Counter()
    salaries: Counter = Counter()
    for j in jobs:
        if j.get("city"):
            cities[str(j["city"])] += 1
        if j.get("degree"):
            degrees[str(j["degree"])] += 1
        if j.get("experience"):
            experiences[str(j["experience"])] += 1
        parsed = _parse_salary_to_k(str(j.get("salary_text", "")))
        if parsed is None:
            salaries["面议"] += 1
        else:
            salaries[bucket_salary(*parsed)] += 1
    return MarketSnapshot(
        total=len(jobs),
        by_city=_distribution(cities),
        by_degree=_distribution(degrees),
        by_experience=_distribution(experiences),
        salary_buckets=_distribution(salaries),
        source_note=_SOURCE_NOTE,
    )



def _title_hits(jobs: list[dict], word: str) -> int:
    """标题含该词（大小写不敏感子串）的条数。纯函数。"""
    w = word.strip().lower()
    if not w:
        return 0
    return sum(1 for j in jobs if w in str(j.get("title", "")).lower())


def group_diagnostic(group: list[str], jobs: list[dict]) -> GroupDiagnostic:
    """一组的召回诊断（原始计数，ADR 0018）。

    只吐计数 + 样本标题，不下「AND 没生效」这类结论——那是 agent 读完数该自己说的话
    （守 ADR 0007 铁律：工具只吐统计、判断在 agent 那张嘴）。
    """
    words = [w for w in group if str(w).strip()]
    per_word = {w: _title_hits(jobs, w) for w in words}
    all_hit = sum(
        1
        for j in jobs
        if all(w.strip().lower() in str(j.get("title", "")).lower() for w in words)
    )
    titles = [str(j.get("title", "")) for j in jobs if j.get("title")]
    return GroupDiagnostic(
        group=words,
        total=len(jobs),
        per_word=per_word,
        all_hit=all_hit,
        sample_titles=titles[:_DIAGNOSTIC_SAMPLE],
    )


async def query_market(keywords: list[list[str]], cities: list[str] | None = None) -> MarketSnapshot | None:
    """现场查市场：按给定查询组**逐组配城市**调猎聘 → 跨组 external_id 去重 → 聚合 + 每组诊断。

    查询组由调用方（agent 工具）从对话里的候选方向直接传入——**定方向前摸底**也查得了，
    不必等方向落库。**城市挂到组上**（2026-09-13 ADR 0019）：`cities[i]` 是第 i 组要查的城市，
    与 keywords 平行（组数与城市数不齐时按最短对齐 = 缺的组不传城市）；空城市按猎聘默认（全国
    页）走。清洗空组；清洗后仍无查询组 → None（提示先给方向）；超 MAX_KEYWORD_GROUPS
    组 → ConflictError（ADR 0009，与 commit_direction 同上限）。猎聘风控/失败
    → LLMUnavailableError。现场查即弃，不落库、不缓存（ADR 0007）。

    **比城市 = agent 自己一城一调**：想知道「同一岗位在哪座城更好」，agent 分别
    `query_market([["前端"]], ["北京"])` / `(["上海"])` 拿两份快照比——本函数**不**提供
    「一次传多城、帮你横向对比」的聚合（那是替 agent 做策略，违 ADR 0007）。
    """
    raw_cities = cities or []
    cleaned: list[list[str]] = []
    cleaned_cities: list[str] = []
    for i, group in enumerate(keywords):
        words = [str(w).strip() for w in group if str(w).strip()]
        if words:
            cleaned.append(words)
            cleaned_cities.append(str(raw_cities[i]).strip() if i < len(raw_cities) else "")
    if not cleaned:
        return None
    # 查询组数上限（ADR 0009）：与 commit_direction 同套 MAX_KEYWORD_GROUPS——摸底查询也是
    # 逐组现场查猎聘，同样的膨胀风险。超限出声（ConflictError），在发起任何猎聘请求前拦下。
    if len(cleaned) > MAX_KEYWORD_GROUPS:
        raise ConflictError(
            f"市场查询最多 {MAX_KEYWORD_GROUPS} 组关键词，你给了 {len(cleaned)} 组——请精简到 {MAX_KEYWORD_GROUPS} 组以内再查。"
        )

    by_id: dict[str, dict] = {}
    diagnostics: list[GroupDiagnostic] = []
    estimates: list[ProbeEstimate] = []
    for i, group in enumerate(cleaned):
        query = " ".join(group)
        group_city = cleaned_cities[i]
        try:
            raw, est = await fetch_liepin_page(query=query, city=group_city)
        except (LiepinBlockedError, LiepinError) as e:
            logger.warning("market_query_failed", query=query, city=group_city, error=str(e))
            raise LLMUnavailableError(detail=f"猎聘市场查询失败：{e}") from e
        diagnostics.append(group_diagnostic(group, raw))  # 诊断按**未去重**的本组召回算
        estimates.append(ProbeEstimate(group=group, city=group_city, est_total=est))
        for job in raw:
            eid = job.get("external_id") or job.get("source_url") or job.get("title") or ""
            by_id[eid] = job

    snap = aggregate_market(list(by_id.values()))
    snap.role = ""  # 摸底查询无岗位标签；role 由 agent 转述时补
    snap.cities = cleaned_cities  # 逐组城市（渲染用；城市分布由聚合结果给）
    snap.diagnostics = diagnostics
    snap.est_totals = estimates
    logger.info("market_queried", cities=cleaned_cities, total=snap.total, groups=len(cleaned))
    return snap



def _render_slices(slices: list[DistributionSlice], limit: int = 8) -> str:
    """分布切片 → 紧凑文本（前 limit 项）。空返回「无」。"""
    if not slices:
        return "无"
    top = slices[:limit]
    parts = [f"{s.label} {s.count}" for s in top]
    return "、".join(parts)


def _render_diagnostics(diags: list[GroupDiagnostic]) -> str:
    """每组召回诊断 → 紧凑文本（原始计数 + 样本标题，不下结论）。"""
    if not diags:
        return ""
    lines = ["每组召回诊断（标题命中计数，仅供参考）："]
    for d in diags:
        hits = "、".join(f"{w} {n}" for w, n in d.per_word.items())
        lines.append(
            f"- 组「{' '.join(d.group)}」：召回 {d.total} 条；标题命中 {hits}；"
            f"全部词同时命中 {d.all_hit} 条。"
        )
        if d.sample_titles:
            lines.append(f"  样本标题：{'、'.join(d.sample_titles)}")
    lines.append(
        "说明：平台对多词是「有交集按 AND、交集不足静默放宽成 OR」，且响应不给命中总数。"
        "上面的「全命中」若远低于最大单词命中，说明这组词实际是按单词搜的——"
        "它们被平台当成了并列词，而不是收窄词。"
    )
    return "\n".join(lines)


def _render_estimates(estimates: list[ProbeEstimate]) -> str:
    """逐探针岗位规模估值 → 紧凑文本（附封顶语义）。无估值时不渲染。"""
    rows = [e for e in estimates if e.est_total is not None]
    if not rows:
        return ""
    parts = [
        f"{' '.join(e.group)}@{e.city or '全国'} 约 {e.est_total}" for e in rows
    ]
    return (
        "岗位规模（猎聘估值）：" + "；".join(parts) + "。\n"
        "  说明：这是猎聘给的**封顶估值**（约 800 封顶）——明显低于峰值可信（说明这小市场岗少，"
        "如昆明约 224）；大城市多压在封顶线上、**互相不可比**。只用来判断「哪些城明显偏小」。"
    )


def render_market_snapshot(snap: MarketSnapshot) -> str:
    """把市场快照渲染成给 agent 读的文本（GBK 安全，无特殊箭头）。

    agent 据此比对已注入的 facts，自己讲出「你 vs 市场缺什么」——本函数只摆事实不下结论。
    """
    lines = [
        f"目标岗位：{snap.role or '未指定'}，查询城市：{'、'.join(c for c in snap.cities if c) or '未指定'}。",
        f"岗位量（去重后）：{snap.total} 条。",
        f"城市分布：{_render_slices(snap.by_city)}。",
        f"学历分布：{_render_slices(snap.by_degree)}。",
        f"年限要求：{_render_slices(snap.by_experience)}。",
        f"薪资段：{_render_slices(snap.salary_buckets)}。",
    ]
    est = _render_estimates(snap.est_totals)
    if est:
        lines.append(est)
    diag = _render_diagnostics(snap.diagnostics)
    if diag:
        lines.append(diag)
    lines.append(f"样本说明：{snap.source_note}")
    return "\n".join(lines)
