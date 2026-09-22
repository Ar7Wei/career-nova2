"""宽松结构化输出解析器：json_mode 降级路径专用。

背景：DeepSeek 等 OpenAI 兼容端点不支持 ``response_format: json_schema``（严格模式），
自动降级到 ``json_object``（只保证合法 JSON，不保证 schema）。此时 langchain 的
``PydanticOutputParser`` 严格校验会因字段名不一致直接抛错（实测 DeepSeek 输出
``type`` 而非 ``category`` → 500）。

本模块提供宽松解析：
1. 从 LLM 输出提取 JSON（容忍 markdown 代码块包裹、前后缀文字）。
2. 字段归一化：把常见异名映射回 schema 字段（如 ``type`` → ``category``）。
3. pydantic 校验；失败不抛异常，返回 schema 默认值 + 一条可读错误信息。

用法：``base.bind(response_format={"type": "json_object"}) | lenient_structured_parser(MyModel)``，
返回 pydantic 实例。只被 llm service 降级路径引用。
"""

import json
import re
import typing
from typing import Any, Type, TypeVar

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from app.core.logging import logger

T = TypeVar("T", bound=BaseModel)

# 字段异名归一化：schema 字段名 → 可能出现的别名（模型自由发挥时的常见写法）
# 例：ExtractedFact.category 可能被输出成 "type"；ParseCoverage.note 可能被输出成 "description"。
# 2026-08-07 重构：ExtractedFact 去 content/group_id，改 title + points。
_FIELD_ALIASES: dict[str, list[str]] = {
    "category": ["type", "category_name", "classification"],
    "title": ["text", "value", "content", "description", "detail", "entry", "name"],
    "points": ["sub_points", "children", "items", "bullets", "details", "subitems"],
    # 2026-09-21 补：occurred_at 时间线（ExtractedFact 新增字段），模型自由发挥的常见写法。
    "occurred_at": ["date", "dates", "period", "duration", "time_range", "time", "date_range"],
    "detected_sections": ["detected", "detected_count", "sections_detected"],
    "extracted_sections": ["extracted", "extracted_count", "sections_extracted"],
    "note": ["notes", "comment", "summary", "coverage_note"],
    "facts": ["items", "results", "fact_list", "data", "extracted_facts"],
    "coverage": ["coverage_info", "stats", "cover"],
}

# 已知事实分类名（与 FactCategory Literal 对齐）。json_object 漂移时 DeepSeek 可能把分类名
# 当顶层 key（{"basic": [...], "work": [...]}），据此识别并拍平。
_CATEGORY_KEYS = {"basic", "education", "work", "projects", "skill", "other"}


def _extract_json(text: str) -> Any:
    """从 LLM 输出文本里提取 JSON：容忍 markdown 代码块包裹、首尾杂字。

    依次尝试：① 整段是 JSON ② 代码块内的 JSON ③ 第一个平衡的 {...} 块。
    失败返回 None。
    """
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    break
    return None


def _normalize_item(schema: Type, item: dict[str, Any]) -> dict[str, Any]:
    """把单个 dict 的 key 按 schema 字段归一化：原字段优先，缺时从别名补齐。

    只保留 schema 存在的字段（extra=ignore 由调用方 schema 处理）。
    """
    result: dict[str, Any] = {}
    field_names = getattr(schema, "model_fields", {})
    for field_name in field_names:
        if field_name in item:
            result[field_name] = item[field_name]
            continue
        aliases = _FIELD_ALIASES.get(field_name, [])
        found = next((item[k] for k in aliases if k in item), None)
        if found is not None:
            result[field_name] = found
    return result


def _resolve_field_schema(schema: Type, field_name: str) -> Type | None:
    """从字段注解里解析出内层 pydantic schema。

    - list[SubModel] → SubModel
    - SubModel（直接） → SubModel
    - 标量（str/int 等） → None（无需归一化）
    """
    ann = getattr(schema, "model_fields", {}).get(field_name)
    if ann is None:
        return None
    ann_type = ann.annotation
    # 剥掉 Optional[X] 包装
    if typing.get_origin(ann_type) is typing.Union:
        non_none = [a for a in typing.get_args(ann_type) if a is not type(None)]  # noqa: E721
        ann_type = non_none[0] if non_none else ann_type
    origin = typing.get_origin(ann_type)
    if origin is list:
        args = typing.get_args(ann_type)
        ann_type = args[0] if args else None
    if isinstance(ann_type, type) and issubclass(ann_type, BaseModel):
        return ann_type
    return None


def _parse_facts(payload: Any, schema: Type[T]) -> list[Any]:
    """尽力解析 facts 清单：单条损坏不影响其他条（coverage 损坏也不连坐 facts）。

    策略：从 raw 里取 facts 数组 → 逐条归一化 + 校验；坏的条丢弃并记日志。
    相比整份 model_validate，避免"coverage 字段类型错 → 整份 facts 清空"。

    结构漂移兜底在 _lenient_parse 入口做（分类名当顶层 key 的形态会先拍平成
    {"facts": [...]}，见 _flatten_category_dict），这里只负责把 facts 数组逐条解析。
    """
    facts_schema = _resolve_field_schema(schema, "facts")
    raw_facts = payload if isinstance(payload, list) else None
    if isinstance(payload, dict):
        raw_facts = payload.get("facts")
    if not isinstance(raw_facts, list) or facts_schema is None:
        return []
    parsed: list[Any] = []
    for item in raw_facts:
        if not isinstance(item, dict):
            continue
        try:
            parsed.append(facts_schema.model_validate(_normalize_item(facts_schema, item)))
        except Exception as e:  # noqa: BLE001 - 单条损坏丢弃，不阻断整份
            logger.warning("lenient_parse_dropped_fact", error=str(e)[:200])
    return parsed


def _flatten_category_dict(payload: dict) -> list[dict]:
    """把 `{"basic": [...], "work": [...]}` 拍平成 `[{category: "basic", ...}, ...]`。

    json_object 漂移（2026-08-29）：DeepSeek 把分类当顶层 key、每类一个数组。逐类取数组、
    给每条补 category 字段（若条目内没写）；非 list 的值（如 coverage 之类混进来的）跳过。
    """
    flat: list[dict] = []
    for key, value in payload.items():
        if key not in _CATEGORY_KEYS or not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            if "category" not in entry:
                entry["category"] = key
            flat.append(entry)
    return flat


def _coerce_int(value: Any) -> int | None:
    """把 int 字段的异形值收拢成 int：已是 int/数字串直接转；中文描述串抠出首个数字。

    例：``"识别到约 5 个板块，工作经历 2 段"`` → 5。提取失败返回 None。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        m = re.search(r"\d+", value)
        if m:
            try:
                return int(m.group(0))
            except ValueError:
                return None
        return None
    return None


def _parse_coverage(payload: Any, schema: Type[T]) -> Any:
    """尽力解析覆盖率自述：失败仅保留空覆盖率，不连坐 facts。

    json_mode 下 DeepSeek 实测把 detected_sections 输出成中文描述串而非整数——
    coverage 只是辅助自述字段，它的失败不该清空整份抽取结果。
    int 字段做 _coerce_int 收拢（从"识别到约 5 段"里抠出 5），尽力恢复覆盖率数字。
    """
    coverage_schema = _resolve_field_schema(schema, "coverage")
    raw_coverage = payload.get("coverage") if isinstance(payload, dict) else None
    if coverage_schema is None or not isinstance(raw_coverage, dict):
        return coverage_schema() if coverage_schema else {}
    normalized = _normalize_item(coverage_schema, raw_coverage)
    # int 字段容错：字符串里也尝试提取数字（实测 DeepSeek 输出中文描述串）
    for field_name, field in coverage_schema.model_fields.items():
        if field.annotation is int and isinstance(normalized.get(field_name), str):
            coerced = _coerce_int(normalized.get(field_name))
            if coerced is not None:
                normalized[field_name] = coerced
    try:
        return coverage_schema.model_validate(normalized)
    except Exception as e:  # noqa: BLE001 - 覆盖率坏了不影响 facts
        logger.warning("lenient_parse_coverage_fallback", error=str(e)[:200])
        return coverage_schema()


def _lenient_parse(text: str, schema: Type[T]) -> T:
    """宽松解析：提取 JSON → 归一化 → pydantic 校验，失败返回默认实例。

    两条路径（按 schema 形状分流）：
    - schema 含 facts 字段（ExtractedFacts）→ 走 facts/coverage 专用逻辑：facts 与 coverage
      分开解析、互不连坐（coverage 类型错不清空 facts，见 _parse_facts/_parse_coverage）。
    - 其他 schema（DirectionRefineOutput 等）→ 通用路径：_normalize_item 归一化字段名后
      直接 model_validate，标量/list 字段正常落。修复 2026-08-20 bug：旧实现硬编码只回传
      facts+coverage，导致非-facts schema 的 role/keywords/city 全丢、产出静默全空。
    """
    # DEBUG 记录 LLM 原始输出（截断 1500 字符），排查「200 但 facts 空」这类输出不稳定问题。
    logger.debug("lenient_parse_input", text=text[:1500])
    raw = _extract_json(text)
    if raw is None:
        logger.warning("lenient_parse_no_json_found")
        return schema()
    payload: dict[str, Any]
    if isinstance(raw, list):
        payload = {"facts": raw}
    elif isinstance(raw, dict):
        # 漂移兜底（2026-08-29）：DeepSeek 把分类名当顶层 key（{"basic": [...], "work": [...]}）
        # 且没有 facts 字段时，先拍平成 {"facts": [...]} 再走归一化——否则 _normalize_item 会把
        # 分类 key 当非 schema 字段剥掉，导致 facts 静默清空。
        # 2026-09-02 bug 修复：这个兜底**只对含 facts 字段的 schema（ExtractedFacts）生效**。
        # Resume 等 schema 的顶层字段（work/education/projects/skills）与事实分类名撞车，
        # 若不按 schema 形状分流，整份简历会被误判成「分类漂移」拍平成 {"facts":[...]}，
        # 而 Resume 无 facts 字段（extra=ignore）→ 静默丢弃成空 → 冷启动生成/改写全判 EmptyOutputError。
        if (
            "facts" in getattr(schema, "model_fields", {})
            and "facts" not in raw
            and any(k in raw for k in _CATEGORY_KEYS)
        ):
            payload = {"facts": _flatten_category_dict(raw)}
        else:
            payload = _normalize_item(schema, raw)
    else:
        payload = {}

    # 通用路径：schema 无 facts 字段（方向提炼等），直接整体校验归一化后的字段。
    if "facts" not in getattr(schema, "model_fields", {}):
        try:
            return schema.model_validate(payload)
        except Exception as e:  # noqa: BLE001 - 校验失败回退默认实例，不抛 500
            logger.warning("lenient_parse_generic_fallback", error=str(e)[:200])
            return schema()

    # facts 专用路径：facts 与 coverage 分开解析、互不连坐。
    # 旧实现整份 model_validate——coverage 类型错（如 int 字段收到中文串）会连带 facts 一起回退空，
    # 用户看到的识别框就是空清单（bug：识别框看不见提取信息）。
    return schema.model_validate(
        {
            "facts": _parse_facts(payload, schema),
            "coverage": _parse_coverage(payload, schema),
        }
    )


def _input_text(input: Any) -> str:
    """把 langchain runnable 输入转成文本。"""
    if isinstance(input, BaseMessage):
        content = input.content
        return content if isinstance(content, str) else str(content)
    if isinstance(input, dict) and "content" in input:
        return str(input["content"])
    return str(input)


def lenient_structured_parser(schema: Type[T]) -> RunnableLambda:
    """构造宽松结构化解析 Runnable：`model | 本函数(MyModel)` 链的右端。

    返回 langchain RunnableLambda，兼容 `|` 组合与 ainvoke。
    """
    return RunnableLambda(
        lambda input: _lenient_parse(_input_text(input), schema),
    )
