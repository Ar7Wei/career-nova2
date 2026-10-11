"""岗位匹配度打分（ADR 0023，2026-10-11）——纯函数、不调 LLM、不碰 DB。

「匹配度排序」= 排「**最契合 / 最省事**」，**不是**排「最容易中」——没结果数据，
成功率预测是禁区（ADR 0023）。三条确定性信号相加：
1. **门槛**：用户年限/学历 vs 岗位 experience/degree（差 ≤1 年算「差一点」）；
2. **契合**：岗位标题/公司命中用户的目标岗位词或方向检索词。
门槛「不限/空白/解析不出」的岗按**中性分**计（不惩罚也不奖励）。

本模块是**纯叶子**（只依赖 schemas）：门槛原语（年限/学历解析）与分析统计
`analysis_stats.compute_gate` 共用一套，排序与报告口径不分家。
"""

import re

from app.schemas.analysis_graph import UserProfile
from app.schemas.direction import Direction
from app.schemas.jobs import Job

# 经验门槛「差一点」的容差（年）——差 ≤1 年算险过、>1 年算明显不够。
RISKY_WITHIN_YEARS = 1.0

# 学历档位：数值越大越高，按数值比大小。
DEGREE_RANK: dict[str, int] = {"博士": 4, "硕士": 3, "本科": 2, "大专": 1, "高中": 0, "中专": 0}
# 档位 → 规范标签（现算画像时把归一后的最高档写回人话）。
DEGREE_LABEL: dict[int, str] = {4: "博士", 3: "硕士", 2: "本科", 1: "大专", 0: "高中"}
# 学历词归一：非标准写法 → 标准词。「学士」是 JSON-Resume 词表写法，本仓模型实际吐「本科」，两种都认。
DEGREE_ALIASES: dict[str, str] = {
    "统招本科": "本科",
    "全日制本科": "本科",
    "学士": "本科",
    "统招硕士": "硕士",
    "全日制硕士": "硕士",
    "研究生": "硕士",
    "专科": "大专",
    "高职": "大专",
    "高中及以下": "高中",
}

# 经验门槛里的「不限」写法（无门槛，不计入分母）。
EXP_UNLIMITED = ("不限", "无经验", "应届")

# 门槛档 → 匹配度得分分量。clear 达标 / risky 差一点 / blocked 明显不够 / unknown 不明确（中性）。
_EXP_PTS: dict[str, int] = {"clear": 45, "unknown": 32, "risky": 26, "blocked": 5}
_DEG_PTS: dict[str, int] = {"clear": 30, "unknown": 22, "blocked": 8}
# 契合分量：命中 1 个词 / 命中 ≥2 个词。
_FIT_SOME, _FIT_STRONG = 15, 25


def degree_rank(raw: str) -> int | None:
    """学历词 → 档位（归一后）。认不得 → None（门槛不明确）。"""
    norm = DEGREE_ALIASES.get((raw or "").strip(), (raw or "").strip())
    return DEGREE_RANK.get(norm)


def exp_min_years(text: str) -> float | None:
    """岗位经验门槛 → 最低年限（年）。'不限' 写法 / 空 / 解析不出 → None（门槛不明确）。

    - 区间 '3-5年' / '3-6年' → 下界（3）
    - 及以上 '3年以上' / '8年以上' → 该值
    - 及以下 '3年以下' / '1年以下' → 0（人人达标）
    """
    t = (text or "").strip()
    if not t or any(u in t for u in EXP_UNLIMITED):
        return None
    m = re.search(r"(\d+)\s*[-~到]\s*(\d+)", t)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+)", t)
    if not m:
        return None
    n = float(m.group(1))
    if "以下" in t:
        return 0.0
    return n


def gate_of_job(job: Job, profile: UserProfile) -> tuple[str, str]:
    """单岗门槛判定 → (经验档, 学历档)，各取 `clear/risky/blocked/unknown`。

    门槛不明确（不限/空白/解析不出）→ `unknown`（不计入分母、按中性分算）。
    """
    exp = "unknown"
    if profile.years is not None:
        m = exp_min_years(job.experience)
        if m is not None:
            gap = m - profile.years
            exp = "clear" if gap <= 0 else ("risky" if gap <= RISKY_WITHIN_YEARS else "blocked")
    deg = "unknown"
    if profile.degree_rank > 0:
        r = degree_rank(job.degree)
        if r is not None:
            deg = "clear" if profile.degree_rank >= r else "blocked"
    return exp, deg


def match_terms(direction: Direction | None, profile: UserProfile | None) -> tuple[str, ...]:
    """契合词表 = 用户目标岗位 + 方向 role + 各检索组词（归一、去重、去极短）。"""
    raw: list[str] = []
    if profile is not None and profile.target:
        raw.append(profile.target)
    if direction is not None:
        if direction.role:
            raw.append(direction.role)
        for group in direction.keywords:
            raw.extend(group)
    out: list[str] = []
    for t in raw:
        norm = (t or "").strip().lower()
        if len(norm) >= 2 and norm not in out:
            out.append(norm)
    return tuple(out)


def match_score(job: Job, profile: UserProfile | None, terms: tuple[str, ...]) -> tuple[int, int, str]:
    """单岗匹配度 → (总分 0~100, 命中契合词数, 一句依据)。

    profile 为 None（无简历画像）→ 只按契合分量打（门槛部分中性）。
    """
    exp, deg = gate_of_job(job, profile) if profile is not None else ("unknown", "unknown")
    text = f"{job.title} {job.company}".lower()
    matched = [t for t in terms if t in text]
    fit = _FIT_STRONG if len(matched) >= 2 else (_FIT_SOME if matched else 0)
    score = _EXP_PTS[exp] + _DEG_PTS[deg] + fit
    return score, len(matched), _reason(exp, deg, matched)


_EXP_LABEL = {"clear": "经验达标", "risky": "经验差一点", "blocked": "经验不够", "unknown": ""}
_DEG_LABEL = {"clear": "学历达标", "blocked": "学历不够", "unknown": ""}


def _reason(exp: str, deg: str, matched: list[str]) -> str:
    """一句依据（供 UI 悬浮/展示）：门槛结论 + 命中的契合词。"""
    bits = [x for x in (_EXP_LABEL.get(exp, ""), _DEG_LABEL.get(deg, "")) if x]
    if matched:
        bits.append("命中 " + "、".join(matched[:3]))
    return " · ".join(bits)
