"""事实覆盖校验 + 展平纯函数（分层铁律：纯函数，不碰 DB、不知道 LLM 存在）。

「内容永不丢」机器门（ADR 0012）：把格式化后的 facts_text 与结构化 resume JSON 比对，
返回「整条事实连影子都没进成品」的缺失清单（title 截断）。生成路径
（generate_json_from_facts）与改写机器门（validate_content_node）共用同一口径——
只拦硬丢失，软质量（措辞/量化）是 LLM 的活，不归机器门。

解析契约：facts_text 的 title 行以 `[` 开头、要点行以 `-` 开头（与
`services/resume_edit.py` 的 `build_facts_text` 严格对齐）。
"""

import re

from app.schemas.facts import Fact
from app.schemas.resume import Resume, resume_to_json

# 「个人概述」类标题词 → 映射到的字段。这类 fact 的值会进 basics.summary，但标题词本身
# （「个人概述」）不会出现在成品里——故对这类只校验「值进了对应字段」，不校验标题文字。
_SUMMARY_TITLES = ("个人概述", "个人简介", "自我介绍", "自我评价")


def flatten_facts(facts: list[Fact], *, bullet_title: bool = False, on_resume_only: bool = False) -> str:
    """事实列表 → 注入/校验用文本（按条目行 + 缩进要点行的两层展平）。

    各注入点（对话 agent / 方向提炼 / 开场引导 / 生成输入）共用此格式，避免四份拷贝漂移：
    - bullet_title：条目行是否带 `- ` 前缀（对话/开场用带前缀，方向/生成用不带）。
    - on_resume_only：只取 on_resume=True 的事实（生成输入 + 机器门口径，ADR 0011）。
    """
    lines: list[str] = []
    for f in facts:
        if on_resume_only and not f.on_resume:
            continue
        lines.append((f"- [{f.category}] {f.title}") if bullet_title else f"[{f.category}] {f.title}")
        for p in f.points:
            lines.append(f"  - {p}")
    return "\n".join(lines)


def _title_tokens(title: str) -> list[str]:
    """从一条 fact title 里提取用于覆盖检查的关键词（≥2 字的连续片段，去重）。"""
    tokens: list[str] = []
    for seg in re.split(r"[\s·|:：、,，/()（）\-]+", title):
        seg = seg.strip()
        if len(seg) >= 2:
            tokens.append(seg)
    return tokens


def find_missing_facts(facts_text: str, resume: Resume) -> list[str]:
    """返回「facts_text 里有、resume 成品里连影子都没有」的事实 title 清单（截断 60 字）。

    检测手段：每条 title 行的非空关键词都必须在 resume JSON 全文出现；概述类例外
    （标题词不进成品，只查 basics.summary 非空）。空清单 = 覆盖完整。
    """
    full_text = resume_to_json(resume)
    missing: list[str] = []
    for line in facts_text.splitlines():
        line = line.strip()
        if not line or line.startswith("-"):
            continue  # 跳过要点行（- 前缀）；title 行以 [category] 开头
        if not line.startswith("["):
            continue
        title = line.split("]", 1)[1].strip() if "]" in line else line
        if any(t in title for t in _SUMMARY_TITLES):
            if not resume.basics.summary.strip():
                missing.append(title[:60])
            continue  # 概述类只看 basics.summary 有没有填
        tokens = _title_tokens(title)
        if not tokens:
            continue
        if not any(tok in full_text for tok in tokens):
            missing.append(title[:60])
    return missing
