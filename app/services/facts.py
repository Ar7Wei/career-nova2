"""facts service：用户信息事实库的读写编排。

纯数据读写（无 LLM），允许 Service 直接走 Repository（不绕 Graph）。
统一冲突检测：**入库前**（确认 / 对话挖掘 / 手填）做——同分类 + 高相似判定"同一实体"：
- 同一实体 + 标题不同（真伪冲突，如"雅思 7.5"vs"4.5"）→ 新的 active、旧的 superseded 留 history。
- 标题相同/近似（幂等）→ 不重复写，跳过。
- 不同实体 → 直接写。
比较单元是 title（总条目）；points 是标题下的要点，继承标题的判定。
重语义矛盾（时间交集等）留给对话澄清（design/resume.md §4）。
对话挖掘（record_chat_facts）对真伪冲突**不自动裁决**——返回给 agent 反问用户（§11.4）。
"""

from difflib import SequenceMatcher

from pydantic import BaseModel, Field

from app.core.logging import logger
from app.repositories.facts import create_facts, delete_fact, list_facts, update_fact
from app.schemas.facts import Fact, FactCategory, FactConflict, FactCreate, FactStatus, FactUpdate

# 相似度阈值：>= CONFLICT_RATIO 视为"同一实体"（做真伪冲突判定）
CONFLICT_RATIO = 0.8
# >= IDEMPOTENT_RATIO 视为"标题相同/近似"（幂等，跳过）
IDEMPOTENT_RATIO = 0.95


class FactRecordResult(BaseModel):
    """对话挖掘（record_facts 工具）的执行结果，供 agent 感知「记/跳/冲突」。"""

    recorded: list[str] = Field(default_factory=list, description="实际写入的事实（title）")
    skipped: list[str] = Field(default_factory=list, description="幂等跳过（已存在）的 title")
    conflicts: list[FactConflict] = Field(default_factory=list, description="真伪冲突：未写入，需 agent 澄清")


class FactConfirmResult(BaseModel):
    """确认入库（confirm_facts）的执行结果：写入条数 + 未写入的真伪冲突对。

    甲方案（2026-08-13）：fuzzy 带（0.8~0.95）**永不自动裁决**——真伪冲突不静默
    supersede 旧事实，而是返回给调用方转交用户裁决（2026-09-09 起改为**对话内反问**：
    agent 在聊天里问用户，拍板后调 `supersede_fact` 落定；冲突卡片已删，见 ADR 0017）。
    """

    saved: int = Field(default=0, description="实际写入条数")
    conflicts: list[FactConflict] = Field(
        default_factory=list,
        description="真伪冲突：未写入，待用户裁决",
    )


def _normalize(text: str) -> str:
    """归一化：去空白、去标点，用于相似度比较。"""
    return "".join(ch for ch in text if not ch.isspace()).lower()


def _similar(a: str, b: str) -> float:
    """标题相似度（0~1）：归一化后编辑相似度。"""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _find_best_match(f: FactCreate, existing: list[Fact]) -> tuple[Fact | None, float]:
    """同分类下找与 f 标题最相似的 active 事实（返回 (匹配, 相似度)）。"""
    best_ratio, best_match = 0.0, None
    for e in existing:
        if e.category != f.category:
            continue
        ratio = _similar(f.title, e.title)
        if ratio > best_ratio:
            best_ratio, best_match = ratio, e
    return best_match, best_ratio


async def _resolve_conflicts(facts: list[FactCreate], *, auto_adjudicate: bool) -> tuple[list[FactCreate], list[FactConflict]]:
    """入库前冲突检测：同分类 + 高相似 → 同一实体，幂等跳过；真伪冲突按策略处理。

    返回 (真正要写入的新事实列表, 未裁决的真伪冲突对)。
    - auto_adjudicate=True（手填改自己条目等明确意图）：真伪冲突自动——旧 superseded、新写入。
    - auto_adjudicate=False（甲方案，上传确认/对话挖掘）：fuzzy 带**永不自动裁决**——
      冲突条不写库，收进 conflicts 返回给调用方转交用户裁决。
    """
    existing = await list_facts(status="active")
    to_write: list[FactCreate] = []
    conflicts: list[FactConflict] = []
    for f in facts:
        verdict, match = _classify_fact(f, existing)
        if verdict == "conflict":
            assert match is not None  # conflict 分支必有匹配（_classify_fact 语义保证）
            if auto_adjudicate:
                # 明确意图路径：旧 active 标 superseded（留 history），新事实保留写入
                await update_fact(match.id, FactUpdate(status="superseded"))
                to_write.append(f)
            else:
                # 甲方案：不自动裁决，不写库，返回完整冲突对给用户裁决（2026-09-09 起走聊天内反问）
                conflicts.append(
                    FactConflict(new_fact=f, new_title=f.title, existing_id=match.id, existing_title=match.title)
                )
        elif verdict == "write":
            to_write.append(f)
        # verdict == "skip"：幂等，不写
    return to_write, conflicts


async def record_chat_facts(facts: list[FactCreate]) -> FactRecordResult:
    """对话挖掘入库（§11.4 record_facts 工具）：统一冲突检测，source=chat。

    - 幂等重复 → skipped（agent 不被打扰）
    - 不同实体 → recorded（写入）
    - 真伪冲突 → conflicts（**不自动裁决**，返回给 agent 反问用户，再按用户答复写入）
    """
    if not facts:
        return FactRecordResult()
    existing = await list_facts(status="active")
    to_write: list[FactCreate] = []
    recorded: list[str] = []
    skipped: list[str] = []
    conflicts: list[FactConflict] = []
    for f in facts:
        verdict, match = _classify_fact(f, existing)
        if verdict == "conflict":
            # 真伪冲突：**不自动裁决**，返回给 agent 反问用户，再按用户答复写入
            assert match is not None  # conflict 分支必有匹配（_classify_fact 语义保证）
            conflicts.append(FactConflict(new_fact=f, new_title=f.title, existing_id=match.id, existing_title=match.title))
        elif verdict == "skip":
            skipped.append(f.title)
        else:
            to_write.append(f)
            recorded.append(f.title)
    if to_write:
        stamped = [f.model_copy(update={"source": "chat"}) for f in to_write]
        await create_facts(stamped)
    return FactRecordResult(recorded=recorded, skipped=skipped, conflicts=conflicts)


def _classify_fact(f: FactCreate, existing: list[Fact]) -> tuple[str, Fact | None]:
    """把一条事实对 active 集分类：write（不同实体，直接写）/ skip（幂等）/ conflict（真伪冲突）。

    返回 (verdict, best_match)——write 分支 match 为 None；conflict/skip 分支 match 必非空。
    三个分支共用同一套 `_find_best_match` + CONFLICT_RATIO/IDEMPOTENT_RATIO 级联，
    调用方各自映射到自己的动作（confirm 自动裁决 vs 对话反问用户）。
    """
    best_match, best_ratio = _find_best_match(f, existing)
    if best_match is None or best_ratio < CONFLICT_RATIO:
        return "write", None  # 不同实体：直接写
    if best_ratio >= IDEMPOTENT_RATIO:
        return "skip", best_match
    return "conflict", best_match


async def confirm_facts(
    facts: list[FactCreate],
    *,
    detect_conflict: bool = True,
    source: str = "resume_upload",
    auto_adjudicate: bool = False,
) -> FactConfirmResult:
    """确认入库：统一冲突检测（默认开）→ 批量写入（active、指定 source）。

    返回 FactConfirmResult（saved 写入条数 + conflicts 未裁决的真伪冲突对）。
    - detect_conflict=False：直接写入（手填改自己条目等场景，连检测都不跑）。
    - auto_adjudicate：真伪冲突是否自动裁决。甲方案（2026-08-13）默认 **False**——
      fuzzy 带永不自动裁决，冲突条不写库、返回给调用方转交用户裁决。
      仅明确意图路径（如目标岗位单值槽位）传 True 自动 supersede。
    source：确认卡片=resume_upload、对话挖掘=chat、手填=manual。
    """
    if not facts:
        return FactConfirmResult()
    if not detect_conflict:
        to_write = facts
        conflicts: list[FactConflict] = []
    else:
        to_write, conflicts = await _resolve_conflicts(facts, auto_adjudicate=auto_adjudicate)
    if not to_write:
        return FactConfirmResult(saved=0, conflicts=conflicts)
    stamped = [f.model_copy(update={"source": source}) for f in to_write]
    created = await create_facts(stamped)
    return FactConfirmResult(saved=len(created), conflicts=conflicts)


async def get_facts(
    category: FactCategory | None = None,
    status: FactStatus | None = "active",
) -> list[Fact]:
    """读事实列表（信息库页按分类/状态过滤）。"""
    return await list_facts(category=category, status=status)


async def supersede_fact(existing_id: int, category: FactCategory, title: str, points: list[str] | None = None, occurred_at: str | None = None) -> Fact | None:
    """对话反问落定（2026-09-09）：用新事实顶掉旧事实，旧 superseded、新写入 active。

    这是「record_facts 返回 conflicts → agent 反问 → 用户拍板」落定那半边（替代
    旧 ConflictCard 内存队列的 resolve_conflict use_new 分支）。用户说「以新的为准」时，
    agent 调本函数：旧事实 superseded（留 history 溯源），新事实写入 active（source=chat）。
    旧事实不存在 → 返回 None（幂等，agent 转述「这条已不在，无需改」）。
    """
    existing = await update_fact(existing_id, FactUpdate(status="superseded"))
    if existing is None:
        return None
    created = await create_facts(
        [FactCreate(category=category, title=title, points=points or [], occurred_at=occurred_at, source="chat")]
    )
    logger.info("fact_superseded_by_chat", existing_id=existing_id, new=title)
    return created[0] if created else None


async def modify_fact(fact_id: int, patch: FactUpdate) -> Fact | None:
    """部分更新一条事实；不存在返回 None。"""
    return await update_fact(fact_id, patch)


async def remove_fact(fact_id: int) -> bool:
    """删除一条事实；不存在返回 False。"""
    return await delete_fact(fact_id)


# ---- 缺口计算（resume.md §11.7，采集姿态的硬信号）----

# 关键槽位：槽位名 → 中文标签（渲染缺口报告用）。
# 纯有无二态（2026-08-20 定稿选 a）：只判「空/非空」，深浅语义全留 agent。
_SLOT_LABELS: dict[str, str] = {
    "direction": "求职方向",
    "work": "工作经历",
    "skill": "技能",
    "education": "教育经历",
    "basic": "基本信息",
}

# 方向槽位前缀（与 direction service 的 _ROLE_PREFIX 同构）：「目标岗位：X」fact = 已定方向。
_DIRECTION_PREFIX = "目标岗位："


def compute_info_gaps(facts: list[Fact]) -> dict[str, bool]:
    """判关键槽位「有没有」（True=有 / False=缺）。

    - direction：有「目标岗位：X」前缀的 active fact（方向的唯一天真源，非按 category）。
    - 其余槽位：按 category 有无（work/skill/education/basic）。
    「目标岗位」那条本身是 basic 类，但不算「基本信息」槽位（基本信息指姓名/联系方式等）。
    """
    categories = {f.category for f in facts}
    has_direction = any(f.title.startswith(_DIRECTION_PREFIX) for f in facts)
    # basic 槽位：排除「目标岗位/目标城市」这类方向槽位行，只看真正的基本信息
    has_basic = any(
        f.category == "basic" and not f.title.startswith(("目标岗位：", "目标城市：")) for f in facts
    )
    return {
        "direction": has_direction,
        "work": "work" in categories,
        "skill": "skill" in categories,
        "education": "education" in categories,
        "basic": has_basic,
    }


def render_info_gaps(gaps: dict[str, bool]) -> str:
    """把缺口 dict 渲染成注入 prompt 的文本：列出仍缺的槽位（全有则返回空串）。"""
    missing = [label for key, label in _SLOT_LABELS.items() if not gaps.get(key, False)]
    if not missing:
        return ""
    return "、".join(missing)
