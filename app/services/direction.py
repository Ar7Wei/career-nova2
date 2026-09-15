"""投递方向 service（阶段二 v2）。

方向 = 派生事实（存 user_facts，不建独立表，apply.md §11.2）：
- 单值槽位 fact：category=basic、title="目标岗位：{role}"，**一个查询组一条 point**：组词与
  城市同条存（如 "Vue 前端 | 杭州"），组词与城市一一对应、平行等长。role 仍住 title。
- 目标城市**没有独立槽位**——城市挂在查询组上（2026-09-13，ADR 0019），是这个 fact 的一部分。

city 挂到查询组上（2026-09-13 grill 定稿，ADR 0019）——城市是**每个查询组的属性**，不是方向级
单值：`["Java","后端"]` 配成都、`["产品"]` 配杭州各搜各的。抓取的天然单元本就是「一组词 + 一个
城市」，故探针数 / 三态 / 游标维度全不变（不顶 ADR 0009 封顶）。**逐组必填、无「全国」**
（前程无忧留空 = 回落 IP 城市，语义是坏的）；`cities` 与 `keywords` **平行等长**，落定前双校验
（长度 + 逐项 25 城白名单）。

role 与 keywords 是两份独立数据（2026-08-21 定稿）：
- **role（目标岗位）**：人话标签，给简历生成（target_role）+ 方向标签展示用，**不参与搜索**。
  独立字段、独立编辑、独立存储（fact title），可被多套 keywords 复用。
- **keywords（查询组）**：二维 `list[list[str]]`，**组内多词、组间 OR**，只管搜索。
  组内多词的真实语义是**自适应 AND**（交错时 AND、交集不足时平台静默放宽成 OR，ADR 0018）；
  写法（一组 = 概念 + 大范围共现的收窄词、同义变体只留最强的一个）见 direction 技能。
  与 role 内容可重复，但互不派生、不混用。

改方向 = 人拍板（2026-08-25 口头化）：refine（提炼，只读不落库）+ commit（拍板写回）。
岗位处置与方向落定**拆分**——commit 只写方向并返回待处置的未处理岗位数
（pending_disposal_count），前端就地弹「留/删」确认后调 dispose_unprocessed_jobs。
分层：Router → Service → Repository；本层编排 facts/jobs repository + LLM，抛 AppError。
"""

from langchain_core.messages import HumanMessage
from app.core.errors import ConflictError
from app.core.logging import logger
from app.prompts import load_refine_direction_prompt
from app.repositories.crawl_state import reset_crawl_state
from app.repositories.facts import list_facts, update_fact
from app.repositories.jobs import count_unprocessed_jobs, delete_unprocessed_jobs
from app.schemas.direction import (
    MAX_KEYWORD_GROUPS,
    Direction,
    DirectionCommitResponse,
    DirectionDisposeResponse,
    DirectionRefineOutput,
    DirectionRefineResponse,
)
from app.schemas.facts import FactCreate, FactUpdate
from app.services.facts import confirm_facts
from app.services.llm import llm_service
from app.utils.facts import flatten_facts
from app.services.sessions import record_current_event

_ROLE_PREFIX = "目标岗位："
# 方向 fact 的 point 里，组词与城市的分隔符（组词可含空格，用 `|` 分隔更清晰）。
_GROUP_CITY_SEP = " | "

# 可选城市库（单一真相源，2026-09-01 定稿）：对齐前程无忧「热门城市」分组（25 个）。
# 城市**逐组强制必填**——前程无忧省略 jobArea = 回落用户 IP 城市（非全国），留空语义是坏的；
# 猎聘亦不再允许「留空=全国」。两家 connector 各持一份「城市名→平台码」映射，都从此清单
# 取键；清单外的城市（含 agent 从对话填来的）一律挡回，不静默回退。
SUPPORTED_CITIES: tuple[str, ...] = (
    "北京", "上海", "广州", "深圳", "武汉", "西安", "杭州", "南京", "成都",
    "重庆", "东莞", "大连", "沈阳", "苏州", "昆明", "长沙", "合肥", "宁波",
    "郑州", "天津", "青岛", "济南", "哈尔滨", "长春", "福州",
)


def _validate_one_city(city: str) -> str:
    """校验并归一单个城市（白名单 + 单城市，2026-09-01 定稿）。

    空/「全国/不限」/白名单外/多城市串一律出声（ConflictError）。返回归一后的城市名。
    """
    city = city.strip()
    if not city:
        raise ConflictError(f"目标城市必填——请从 {list(SUPPORTED_CITIES)} 里选一个。")
    if city in ("全国", "不限", "不限城市"):
        raise ConflictError(f"目标城市必填——请从 {list(SUPPORTED_CITIES)} 里选一个具体城市。")
    if city in SUPPORTED_CITIES:
        return city
    # 多城市分隔符检测（顿号/逗号/空白/斜杠）：「成都、杭州」「成都 杭州」都算多城市。
    # 逐组配城市时不需要这个——用户想给不同组不同城市，就分别填到对应组上。
    for sep in ("、", "，", ",", " ", "/"):
        if sep in city:
            raise ConflictError(f"单个城市只能是一个地名，请从 {list(SUPPORTED_CITIES)} 里选一个。")
    raise ConflictError(f"暂不支持城市「{city}」——请从 {list(SUPPORTED_CITIES)} 里选一个。")


def _validate_cities(keywords: list[list[str]], cities: list[str]) -> list[str]:
    """校验并归一逐组城市（**双校验**：长度相等 + 逐项白名单，2026-09-13 ADR 0019）。

    `cities` 与 `keywords` 是平行数组——错位时下标仍在、**报错都报不出来**，故长度不等一律
    出声，不做补齐/截断（静默补齐会让某组配上别人的城市）。返回归一后的城市数组（逐项过白名单）。
    """
    if len(cities) != len(keywords):
        raise ConflictError(
            f"城市数量（{len(cities)}）与查询组数量（{len(keywords)}）不一致——"
            "每个查询组都要配一个城市。"
        )
    return [_validate_one_city(c) for c in cities]



def get_supported_cities() -> list[str]:
    """可选城市库（有序 25 城），前端下拉 + agent 工具描述引用。"""
    return list(SUPPORTED_CITIES)

# 待处置岗位单例（agent 工具路径，照抄原 proposal.py 单例模式）：
# _run_commit_direction 落定方向后若有未处理岗位 → set_pending_disposal(count)，
# chat.py 读走（透传前端就地弹窗）后 clear。按钮路径（endpoint）不碰它——commit
# 响应体直接带 pending_disposal_count，前端按返回值弹，不经单例。
_pending_disposal: int | None = None


def get_pending_disposal() -> int | None:
    """当前待处置的未处理岗位数（agent commit 方向后）；None = 无待处置。"""
    return _pending_disposal


def set_pending_disposal(count: int) -> None:
    """写入待处置岗位数（_run_commit_direction 落定方向且有未处理岗位时）。"""
    global _pending_disposal
    _pending_disposal = count


def clear_pending_disposal() -> None:
    """清空待处置岗位数（chat.py 读走后 / 无岗位时）。"""
    global _pending_disposal
    _pending_disposal = None


# 方向变更信号单例（agent 工具路径，与 _pending_disposal 同构）：
# _run_commit_direction 落定方向后 set_direction_changed()，chat.py 读走透传前端
# （direction_changed=True → 前端 refresh directionStore，跨页同步方向标签）。按钮路径
# （endpoint）不碰它——前端 commit() 就地 set({ direction }) 已同步。
_direction_changed = False


def get_direction_changed() -> bool:
    """本轮 agent 是否落定了方向（chat.py 读走后清空）。"""
    return _direction_changed


def set_direction_changed() -> None:
    """写入方向变更信号（_run_commit_direction 落定方向后）。"""
    global _direction_changed
    _direction_changed = True


def clear_direction_changed() -> None:
    """清空方向变更信号（chat.py 读走后）。"""
    global _direction_changed
    _direction_changed = False


async def _build_facts_text() -> str:
    """Active facts 展平成文本（注入提炼 prompt）。复用 utils 的展平口径。"""
    return flatten_facts(await list_facts(status="active"))


def _split_group(group: str) -> list[str]:
    """落盘的组（空格连接的词）→ 组内词数组。空/空白返回空组。"""
    return [w for w in group.split() if w]


def _pack_point(words: list[str], city: str) -> str:
    """(组词, 城市) → 一条 point（组内空格 join + `|` 接城市）。城市可为空（refine 提案可缺）。"""
    return " ".join(words) + _GROUP_CITY_SEP + city


def _split_point(point: str) -> tuple[list[str], str] | None:
    """一条 point → (组词数组, 城市)。空/无词返回 None（跳过）。城市缺 `|` 时按空处理。"""
    raw_words, _, city = point.partition(_GROUP_CITY_SEP)
    words = _split_group(raw_words)
    if not words:
        return None
    return words, city.strip()


async def get_direction() -> Direction:
    """读当前方向：从「目标岗位」active fact 聚合出 role + 逐组 keywords/cities。

    抓取器（Electron）经 GET /api/v1/direction 调用，替换硬编码 query/城市。
    无方向 fact → 返回空 Direction（role/keywords/cities 全空）。

    role 从 fact title（前缀之后）读；每组从 points 里一条 point 拆回（组词 + 城市）。**旧格式
    兼容**：points 里没有 `|`（升级前只存了组词）时城市留空——由 agent 聊天里补问，不静默回退。
    """
    role = ""
    keywords: list[list[str]] = []
    cities: list[str] = []
    for f in await list_facts(status="active"):
        if f.title.startswith(_ROLE_PREFIX):
            role = f.title[len(_ROLE_PREFIX) :].strip()
            for p in f.points:
                pair = _split_point(p)
                if pair is None:
                    continue
                words, city = pair
                keywords.append(words)
                cities.append(city)
    return Direction(role=role, keywords=keywords, cities=cities)


def _describe_direction(d: Direction) -> str:
    """方向 → 一行人话（喂 refine 的 current_direction；空则空串）。如「全栈工程师 · 杭州/成都」。"""
    if not d.role and not d.keywords:
        return ""
    cities = "、".join(c for c in d.cities if c)
    return f"{d.role or '未指定岗位'} · {cities or '城市未定'}"



async def refine_direction() -> DirectionRefineResponse:
    """从 active facts 提炼方向（LLM 结构化输出），返回 current/proposed（只读不落库）。

    - current：当前已存方向（无则 None）。
    - proposed：LLM 提炼出的方向；无 facts 可提炼则 None（提示先去聊目标）。
    一次性 + 事后编辑：LLM 只翻译，不对话，不写库——拍板走 commit。
    2026-08-25：删 is_new_direction / unprocessed_count（岗位处置统一由 commit 后的
    pending_disposal_count 表达，refine 不再判定连续性）。
    2026-09-13（ADR 0019）：proposed.cities 与 keywords 平行等长，**允许空串**（LLM 没把握的
    组留空，由对话里补问）——清洗时把 cities 对齐到「清洗后仍非空的组」，缺项补空。
    """
    facts = await list_facts(status="active")
    current = await get_direction()
    if not facts:
        return DirectionRefineResponse(
            current=current if current.role or current.keywords else None,
            proposed=None,
        )
    prompt = load_refine_direction_prompt(
        facts=await _build_facts_text(),
        current_direction=_describe_direction(current),
    )
    result = await llm_service.call(
        [HumanMessage(content=prompt)],
        response_format=DirectionRefineOutput,
    )
    logger.info("direction_refined", role=result.role, keywords=result.keywords)
    # 清洗：去空词/空组；cities 对齐到**清洗后仍非空**的组（LLM 给的组数与城市数可能不一致，
    # 缺项补空串 = 「这组还没定城市」，不编造、不报错——commit 时才逐组必填）。
    kw: list[list[str]] = []
    cities: list[str] = []
    for i, g in enumerate(result.keywords):
        words = [w.strip() for w in g if w.strip()]
        if not words:
            continue
        kw.append(words)
        cities.append(result.cities[i].strip() if i < len(result.cities) else "")
    proposed = Direction(role=result.role.strip(), keywords=kw, cities=cities)
    return DirectionRefineResponse(
        current=current if current.role or current.keywords else None,
        proposed=proposed,
    )


async def _supersede_by_prefix(prefix: str) -> None:
    """把某个前缀的单值槽位现有 active 行标 superseded（留 history）。"""
    for f in await list_facts(status="active"):
        if f.title.startswith(prefix):
            await update_fact(f.id, FactUpdate(status="superseded"))


async def commit_direction(
    role: str,
    keywords: list[list[str]],
    cities: list[str],
    *,
    record_event: bool = True,
) -> DirectionCommitResponse:
    """拍板改方向：写回 facts（role + 逐组 keywords/cities），返回待处置的未处理岗位数。

    - role：目标岗位人话标签（独立字段，给简历生成/方向展示用，**不参与搜索**）；可空。
    - keywords：二维查询组（非空；组内多词、组间 OR；组内真实语义是自适应 AND，ADR 0018），
      只喂搜索。
    - cities：逐组城市（**与 keywords 平行等长、逐组必填**——`_validate_cities` 挡回长度不等/
      空/白名单外）。组词与城市同条落 fact 的 points（`"组词 | 城市"`，ADR 0019）。
    - record_event：写库成功后补一条系统事件气泡（按钮路径 true；agent 工具路径 false——
      agent 自己会返回"已落定方向"的助手回复，再补气泡重复）。
    2026-08-25：删 dispose_unprocessed——commit 只写方向不碰岗位；未处理岗位处置
    拆到 dispose_unprocessed_jobs（前端就地弹「留/删」后决定调不调）。
    """
    # 清洗二维查询组：组内去空白词、去空组；全空 → 409（冲突，不写）。
    # **清洗后必须同步清洗 cities**——否则组被去掉、城市数组没跟上，平行数组错位。
    cleaned: list[list[str]] = []
    cleaned_cities: list[str] = []
    for i, group in enumerate(keywords):
        words = [w.strip() for w in group if w.strip()]
        if words:
            cleaned.append(words)
            cleaned_cities.append(cities[i].strip() if i < len(cities) else "")
    if not cleaned:
        raise ConflictError("方向搜索词不能为空——请至少填一个搜索词。")
    # 查询组数上限（ADR 0009）：超 MAX_KEYWORD_GROUPS 组出声报错，不静默截断——
    # 静默砍组会让方向失真（用户/agent 以为落定的组被悄悄丢掉），不如直接报错让 ta 选。
    if len(cleaned) > MAX_KEYWORD_GROUPS:
        raise ConflictError(
            f"方向搜索词最多 {MAX_KEYWORD_GROUPS} 组，你给了 {len(cleaned)} 组——请精简到 {MAX_KEYWORD_GROUPS} 组以内再落定。"
        )
    role = role.strip()
    # 逐组城市双校验（ADR 0019）：长度相等 + 逐项 25 城白名单 + 禁「全国」。
    cleaned_cities = _validate_cities(cleaned, cleaned_cities)
    # 单值槽位：旧 superseded，新 active（title=role 标签、points=每组一条 `"组词 | 城市"`）。
    # role 与 keywords 独立存储，互不派生——role 空则 title 只留前缀（无岗位标签）。
    # 方向槽位不上简历（ADR 0011）：on_resume=False，生成 prompt / 机器门都不带它。
    await _supersede_by_prefix(_ROLE_PREFIX)
    await confirm_facts(
        [
            FactCreate(
                category="basic",
                title=f"{_ROLE_PREFIX}{role}",
                points=[_pack_point(words, c) for words, c in zip(cleaned, cleaned_cities, strict=True)],
                on_resume=False,
            )
        ],
        source="manual",
        detect_conflict=False,
    )

    # 落定方向后仍未处理的岗位数（前端据此就地弹「留/删」确认；本层只读不删）。
    pending = await count_unprocessed_jobs()
    if pending:
        logger.info("direction_commit_pending_disposal", count=pending)

    # 改方向 → 游标归零（decision 9）：方向全局单值，两家 connector 吃的同一份方向，
    # 任一输入变了进度就没意义——清两家游标 + 组内三态。
    await reset_crawl_state("liepin")
    await reset_crawl_state("job51")

    # 系统事件气泡（按钮路径）：把"改方向"这个动作记进聊天历史（event 不喂 LLM，纯 UI 记录）
    if record_event:
        role_desc = role or "未指定岗位"
        city_desc = "、".join(dict.fromkeys(cleaned_cities))
        await record_current_event(f"已把求职方向改为「{role_desc}」· {city_desc}", kind="direction_changed")

    return DirectionCommitResponse(role=role, keywords=cleaned, cities=cleaned_cities, pending_disposal_count=pending)


async def dispose_unprocessed_jobs() -> DirectionDisposeResponse:
    """删除未处理岗位（用户在前端处置弹窗选了「清」）。已处理/已投无条件保留。"""
    deleted = await delete_unprocessed_jobs()
    logger.info("direction_dispose_deleted_unprocessed", deleted=deleted)
    return DirectionDisposeResponse(deleted_jobs=deleted)


async def unprocessed_job_count() -> int:
    """当前未处理岗位数（服务层薄封装，供测试/未来调用点使用）。"""
    return await count_unprocessed_jobs()
