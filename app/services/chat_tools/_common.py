"""工具层共用助手：LLM 传参的归一化（非法值收口）+ 结果序列化。

工具签名是 agent 契约：LLM 可能传出集外值或错误类型，这里在**工具边界**把它们
收口成层内合法值（非法 type/severity 跳过该条，非法 category 回退 other），
不把脏值透进 service。措辞是 LLM 契约的一部分，改动需谨慎。
"""

import json
from typing import Any, Literal, cast, get_args

from app.schemas.optimization import ChangeDecision
from app.services.direction import SUPPORTED_CITIES

# 可选城市（给方向工具描述用，单一真相源 SUPPORTED_CITIES，2026-09-01）
CITY_LIST = "、".join(SUPPORTED_CITIES)

# 优化点操作决定闭集（§12.6）：工具入参靠它拦非法值。从 ChangeDecision 派生，
# 单一真相源——手写的平行集合会随 schema 增删而静默漂移。
VALID_DECISIONS = frozenset(get_args(ChangeDecision))

# LangChain 工具调用 → 我们的事实分类
_CATEGORY_MAP: dict[str, Literal["basic", "education", "work", "projects", "skill", "other"]] = {
    "basic": "basic",
    "education": "education",
    "work": "work",
    "projects": "projects",
    "skill": "skill",
    "other": "other",
}


def to_category(value: str) -> Literal["basic", "education", "work", "projects", "skill", "other"]:
    """工具传的 category，非法值回退 other。"""
    return _CATEGORY_MAP.get(value, "other")


def clean_points(raw: list[str] | None) -> list[str]:
    """工具传的 points：逐条转字符串 + 去空白，丢弃空条。"""
    return [str(p).strip() for p in (raw or []) if str(p).strip()]


def to_suggestion_type(
    value: str,
) -> Literal["quantify", "word_choice", "structure", "fill_gap", "job_relevance", "highlight"] | None:
    """工具传的 type 转字面量；非法值返回 None（跳过该条）。"""
    if value in ("quantify", "word_choice", "structure", "fill_gap", "job_relevance", "highlight"):
        return cast(Literal["quantify", "word_choice", "structure", "fill_gap", "job_relevance", "highlight"], value)
    return None


def to_severity(value: str) -> Literal["high", "medium", "low"] | None:
    """工具传的 severity 转字面量；非法值返回 None（跳过该条）。"""
    if value in ("high", "medium", "low"):
        return cast(Literal["high", "medium", "low"], value)
    return None


def serialize_result(result: Any) -> str:
    """把工具执行结果序列化成字符串（返回给 LLM 的可读文本）。"""
    return json.dumps(result, ensure_ascii=False)


def render_keyword_groups(keywords: list[list[str]]) -> str:
    """把二维查询组渲染成二维字面形态（如 `[["前端工程师","Vue"],["web前端"]]`）。

    区别于旧的扁平串（「前端工程师 Vue、web前端」）：扁平串丢组结构，agent 重建 commit 的
    keywords 时只能靠口号脑补，容易简化成每组一个词。二维字面保真结构（组内多词、组间 OR
    一目了然），供 agent 在转述与 commit 之间保留参照。
    """
    if not keywords:
        return "（未展开）"
    return json.dumps(keywords, ensure_ascii=False)


def render_group_cities(keywords: list[list[str]], cities: list[str]) -> str:
    """逐组渲染「组 → 城市」（供 agent 转述 refine 提案；ADR 0019）。

    如 `["Vue 前端"→杭州, "前端开发"→杭州]`。城市为空 → `（未定）`（refine 提案允许缺，
    提醒 agent 就这组去问用户）。组数与城市数不齐时按最短对齐（防御，正常恒等长）。
    """
    if not keywords:
        return "（未展开）"
    parts = []
    for i, group in enumerate(keywords):
        city = cities[i].strip() if i < len(cities) and cities[i].strip() else "（未定）"
        parts.append(f'{" ".join(group)}→{city}')
    return "[" + ", ".join(parts) + "]"
