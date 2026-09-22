"""对话 agent 的工具声明（ADR 0016，从 chat_tools.py 拆分，2026-09-11 B3）。

这里的工具是**真函数**，函数体直接调本层（service）的领域逻辑（冲突检测 / 面板操作 /
方向落定 / 市场查询），"判断在 agent、处理在代码"不变。工具只做薄胶水：参数清洗
（借 _common 归一化）→ 调 service → 结果序列化。

放 service 层（而非 agents 层）的理由：工具函数体写库/冲突检测是 service 的活，放 agents 层
会违反「单向向下、不调上层」铁律（agents 只能 import 基础设施，不 import 业务 service）。
graph 组装时由装配层注入工具（见 _assembly.py），graph 不 import 业务 service。

docstring 是 LLM 工具契约（schema + 使用时机），从 legacy 原样搬来，措辞不可随意改动。
"""

from typing import Any, cast

from datetime import UTC, datetime

from langchain_core.tools import tool

from app.core.errors import ConflictError, EmptyOutputError, LLMUnavailableError
from app.core.logging import logger
from app.repositories.optimization import get_suggestion
from app.schemas.facts import FactCreate
from app.schemas.optimization import Suggestion, SuggestionDecision
from app.schemas.direction import Direction
from app.services.chat_tools._common import (
    CITY_LIST,
    VALID_DECISIONS,
    clean_points,
    render_group_cities,
    render_keyword_groups,
    serialize_result,
    to_category,
    to_severity,
    to_suggestion_type,
)
from app.services.direction import commit_direction, refine_direction, set_direction_changed, set_pending_disposal
from app.services.facts import record_chat_facts, supersede_fact
from app.services.market import query_market, render_market_snapshot
from app.services.optimization import record_suggestion, update_suggestion
from app.services.rewrite import generate_preview
from app.services.timeline import TimelinePeriod, calc_timeline, render_timeline_report


@tool("record_facts")
async def record_facts_tool(
    category: str,
    title: str,
    points: list[str] | None = None,
    occurred_at: str | None = None,
    on_resume: bool = True,
) -> str:
    """记录一条用户信息事实到信息库。

    category：basic 基本信息 / education 教育经历 / work 工作经历 / projects 项目经历 / skill 个人技能（含证书）/ other 其他补充
    title：一句话事实（如 "2021–2024 南京大学软件工程本科"，或 "某公司 前端工程师 2018-2021"）
    points（可选）：这段经历下的子要点数组（如 ["主导核心模块", "性能优化40%"]）；单句事实留空。
    occurred_at（可选）：事实发生的时间线（如 "2018-2021"）。
    on_resume（可选）：这条事实该不该写进简历成品。默认 True（工作/教育/项目/技能/基本信息等简历内容都上）。
      敏感/不该上的信息传 False：薪资、年龄、婚姻、健康、gap 弱点（如"学历不够""没有大厂经历"）、
      想淡化的经历、求职方向（目标岗位/城市）等。标 False 的事实不会进简历、也不会被"内容永不丢"校验。
    工具返回 记/跳/冲突：recorded 已写入、skipped 已存在未重复写、conflicts 与你已记过的事实冲突（需反问用户）。
    """
    title = title.strip()
    if not title:
        return "title 为空，未记录任何事实。"
    if isinstance(on_resume, str):
        on_resume = on_resume.strip().lower() not in ("false", "0", "no", "off")
    fact = FactCreate(
        category=to_category(category),
        title=title,
        points=clean_points(points),
        occurred_at=occurred_at,
        on_resume=bool(on_resume),
    )
    result = await record_chat_facts([fact])
    return serialize_result(result.model_dump())


@tool("supersede_fact")
async def supersede_fact_tool(
    existing_id: int,
    category: str,
    title: str,
    points: list[str] | None = None,
    occurred_at: str | None = None,
) -> str:
    """用一条新事实顶掉已记录的旧事实（对话反问落定，2026-09-09）。

    当 `record_facts` 返回 `conflicts`（新事实与你已记过的旧事实冲突）时，你要在对话里
    当场反问用户「以哪个为准」；用户拍板「以新的为准」后，调本工具落定。

    - existing_id：旧事实的 id（来自 record_facts 返回 conflicts 里的 existing_id）。
    - category / title / points / occurred_at：新事实内容（同 record_facts）。
    工具返回：已用新事实顶掉旧事实；或旧事实不存在（已不在，无需改）。
    若用户说「保留旧的」，不调本工具——旧事实保持原样即可。
    """
    title = title.strip()
    if not title:
        return "title 为空，未落定。"
    new_fact = await supersede_fact(
        existing_id=existing_id,
        category=to_category(category),
        title=title,
        points=clean_points(points),
        occurred_at=occurred_at,
    )
    if new_fact is None:
        return f"旧事实 #{existing_id} 不存在——可能已被处理，无需再改。"
    return f"已用新事实「{title}」顶掉旧事实 #{existing_id}。"


@tool("suggest_improvements")
async def suggest_improvements_tool(
    suggestions: list[dict[str, Any]],
) -> str:
    """把优化点写进「待定」栏（优化点面板 = 你的工作记事本，这是你落新点的手）。

    有简历在手时，优化是默认姿态——改完简历回来 / 首份简历上传后，**先抓一轮**改进点
    直接落待定，作为这一轮对话的工作底稿；聊天中聊透的新点也随手落。逐条给出简历改进建议。

    每条 dict：type / target / original / suggested / reason / severity。
    - type：quantify 量化 / word_choice 用词 / structure 结构 / fill_gap 补缺 / job_relevance 岗位相关 / highlight 卖点
    - target：定位锚点——写在**当前简历 JSON 里能对上**的位置（如 `work[0].highlights[1]`、`basics.summary`），
      给人看的导航标签，不是机器解析的路径
    - original：原文；suggested：建议改法；reason：为什么（引用判定标准）
    - severity：high / medium / low

    **规则全文见 optimize 技能**（`read_file` 读）：六类各自的含义、力度克制、面板四栏交互、
    敲定收口的规矩都在那里。这里只交代函数的入参形状，不重复规则细节——规则只写一处。

    产出会**落库**到建议面板（左栏待定），用户去面板逐条处理。落完你在聊天里简述抓到的点，
    围绕它们展开这一轮。
    """
    raw = suggestions or []
    saved_ids: list[int] = []
    for item in raw:
        type_val = to_suggestion_type(str(item.get("type", "structure")))
        sev_val = to_severity(str(item.get("severity", "medium")))
        if type_val is None or sev_val is None:
            logger.warning("suggestion_parse_skipped", raw=item)
            continue
        s = Suggestion(
            type=type_val,
            target=str(item.get("target", "")),
            original=str(item.get("original", "")),
            suggested=str(item.get("suggested", "")),
            reason=str(item.get("reason", "")),
            severity=sev_val,
        )
        p = await record_suggestion(s)
        if p.id is not None:
            saved_ids.append(p.id)
    return f"已落库 {len(saved_ids)} 条建议（#{', '.join(map(str, saved_ids))}），进建议面板左栏待定，等待用户处理。"


@tool("update_suggestion")
async def update_suggestion_tool(
    suggestion_id: int,
    decision: str,
    refined_target: str = "",
    refined_original: str = "",
    refined_suggested: str = "",
    refined_reason: str = "",
    refined_type: str = "",
    refined_severity: str = "",
    split_to: list[dict[str, Any]] | None = None,
) -> str:
    """更新一条优化点建议的状态/内容（分级自主度，2026-08-29）。

    - suggestion_id：建议编号（来自注入的「当前简历的待执行优化建议」清单）。
    - decision：
      - `accept` 接受（→ 已确认）：**高风险**——只有用户给了**确定口吻**才动
        （「这条收了吧 / 就按这个改」）。
      - `reject` 拒绝（→ 已拒绝灰栏）：**高风险**（版本变更会记"拒掉这一类"偏好）——
        只有用户给了**确定口吻**才动（「这条算了 / 别改了」）。
      - `discuss` 聊一聊（→ 正在聊）：**低风险，随便动**——聊哪条就挪哪条，让面板跟得上。
      - `retract` 撤回（→ 待定）：**低风险，随便动**。
      - `refine` 细化（→ 待定）：讨论后修改了改法，传 refined_* 覆盖内容。
      - `split` 分裂：一条拆成多条，传 split_to。

    分级自主度（2026-08-29）：未定论的两栏（待定/正在聊）之间你随便挪；只有往
    「已确认」这个定论态（下次要真改进简历）走才要确定口吻；拒绝会记偏好，也要确定口吻。
    - refine：refined_type/refined_severity 留空 = 沿用原分类/严重度（细化通常只改措辞）。
    - split：传 split_to（每条 dict 同 suggest_improvements 格式），原条回待定、新条进面板。
    """
    decision = decision.strip().lower()
    # 工具边界收敛：decision 必须落在闭集内，非法值直接回可读反馈（不落进 service 的未知分支）。
    if decision not in VALID_DECISIONS:
        return f"未知操作：{decision}（accept/reject/refine/split/discuss/retract）。"
    refined = None
    if decision == "refine":
        existing = await get_suggestion(suggestion_id)
        if existing is None:
            return f"建议 #{suggestion_id} 不存在。"
        refined_type = to_suggestion_type(refined_type) or cast(Any, existing.type)
        refined_severity = to_severity(refined_severity) or cast(Any, existing.severity)
        refined = Suggestion(
            type=refined_type,
            target=refined_target or existing.target,
            original=refined_original or existing.original,
            suggested=refined_suggested or existing.suggested,
            reason=refined_reason or existing.reason,
            severity=refined_severity,
        )
    split_list: list[Suggestion] | None = None
    if decision == "split" and split_to:
        split_list = []
        for item in split_to:
            type_val = to_suggestion_type(str(item.get("type", "structure")))
            sev_val = to_severity(str(item.get("severity", "medium")))
            if type_val is None or sev_val is None:
                continue
            split_list.append(
                Suggestion(
                    type=type_val,
                    target=str(item.get("target", "")),
                    original=str(item.get("original", "")),
                    suggested=str(item.get("suggested", "")),
                    reason=str(item.get("reason", "")),
                    severity=sev_val,
                )
            )
    return await update_suggestion(suggestion_id, cast(SuggestionDecision, decision), refined=refined, split_to=split_list)


@tool("apply_suggestions")
async def apply_suggestions_tool() -> str:
    """执行「开始改」：把已确认的建议批量应用进简历 → 出一版待确认的简历草稿。

    仅在**用户明确同意开始改**时调用（如「开始改吧 / 应用这些建议 / 生成新版本」）。
    这是昂贵且不可逆的动作（跑整份改写 + 排版重渲染，且确认后开新 session、面板结清），
    所以**动手前必须先问用户、得了准再调**。

    前置门控（由你负责把关）：
    - 还有「待定」建议 → **先别改**，逐条念给用户、用 update_suggestion 清掉待定，清空后再问。
    - 只有「正在聊」未结论 → 口头交代「这几条这轮先不带上（会留到下一版）」再改。
    - 已确认数量 1~2 条 → 建议再攒攒（改一轮成本不低）；用户坚持就改。
    工具会产出一版**暂存预览**（应用了已确认建议），用户在左侧预览确认后才保存为新稿；
    改完你在聊天里用一句话说明这版动了哪些点。
    """
    try:
        await generate_preview(user_request="应用已确认的优化建议", apply_confirmed=True)
    except ConflictError as e:
        return f"改不了：{e.message}"
    except EmptyOutputError as e:
        return f"没改成功：{e.message}"
    return (
        "已出一版应用了已确认建议的简历草稿（暂存预览态，未保存）。"
        "请用户看左侧预览：确认就保存为新版本，不满意可「带意见重改」或继续说要求。"
    )


@tool("generate_resume")
async def generate_resume_tool(
    request: str,
    target_role: str = "",
) -> str:
    """出简历草稿（统一入口，1.3，ADR 0012 合并：生成与改写同一工具）。

    当用户要「生成/重做一份简历」、或要「基于当前简历改内容/改版式」时都调本工具——
    系统自己会判：还没有简历就从事实库生成第一版，已有简历就在现有这份上按 request 改。
    你不用区分「生成 vs 改写」，把用户的原话请求如实传给 request 即可。
    产出**暂存预览态**（不写库），用户在人门确认才保存为新版本；不满意可带意见重改。

    - request：用户的目标/修改请求原话（自然语言，如实转述，不要精简）。如"帮我生成一份
      简历"、"把腾讯那段量化一下"、"重排一版式"。
    - target_role（可选）：用户明确说过的目标岗位（如"我想投后端"）；**没明确说过就留空**，
      留空 = 通用版。不要替用户编造岗位——用户没提过就别传，靠聊出来的目标岗位 fact 兜底。
    工具返回：草稿已就绪，提示用户去左侧预览确认；或说明缺事实/没有简历无法出稿。
    """
    request = request.strip() or "生成一版简历"
    target_role_clean: str | None = target_role.strip() or None
    try:
        await generate_preview(user_request=request, target_role=target_role_clean)
    except ConflictError as e:
        return f"出不了稿：{e.message}"
    except EmptyOutputError as e:
        return f"出稿失败：{e.message}"
    return "已出一版简历草稿（暂存预览态，未保存）。请用户看左侧预览：确认就保存为新版本，不满意可「带意见重改」或继续说要求。"


def _format_current(d: Direction) -> str:
    """当前方向 → 一行人话（refine 工具转述「当前方向」。如「全栈工程师 · 杭州/成都」）。"""
    cities = "/".join(c for c in d.cities if c)
    return f"{d.role or '未指定岗位'} · {cities or '城市未定'}"


@tool(
    "refine_direction",
    description=(
        "从用户信息事实提炼求职方向（投递·方向，apply.md §11.2）。\n\n"
        "当用户要确定/调整找工作的方向时调用（首次定方向、或聊出新的目标岗位后重新提炼）。"
        "从 facts 里提炼出 role（目标岗位标签，写简历/展示用）、keywords（二维查询组，组内多词、"
        "组间 OR）、cities（**逐组城市**，与 keywords 平行等长，从以下 25 城选：" + CITY_LIST + "）。"
        "**城市挂在每个查询组上**（2026-09-13，ADR 0019）——`[\"Java\",\"后端\"]` 配成都、"
        "`[\"产品\"]` 配杭州各搜各的，不是方向级单值。**没证据的组城市留空字符串**（如 "
        "cities=[\"杭州\",\"\"]），别编造；留空就当面问用户。\n"
        "**keywords 写法见 direction 技能（实测平台检索语义，ADR 0018）**——别凭直觉堆同义词。\n"
        "**只产提案不落库**——返回文本给你，你据此口头转述给用户、问 ta 认不认，"
        "用户拍板后再用 commit_direction_tool 落定。方向是决策，人拍板，不自动改写。"
    ),
)
async def refine_direction_tool() -> str:
    """从用户信息事实提炼求职方向（投递·方向，apply.md §11.2）。"""
    try:
        r = await refine_direction()
    except LLMUnavailableError as e:
        return f"方向提炼失败：{e.message}"
    if r.proposed is None:
        return "还没有足够的信息提炼方向——先聊聊你的目标岗位、技能和期望城市吧。"
    kw = render_keyword_groups(r.proposed.keywords)
    city_desc = render_group_cities(r.proposed.keywords, r.proposed.cities)
    role = r.proposed.role
    current_desc = _format_current(r.current) if r.current else "（无）"
    lines = [
        f"已提炼出方向提案：岗位「{role or '未指定'}」、逐组城市 {city_desc}、查询组「{kw}」。",
        f"当前方向：{current_desc}。",
        "请把这个提案讲给用户听（**逐组讲清城市**，哪组没定就问 ta），问 ta 认不认；"
        "用户拍板后再用 commit_direction 落定。",
    ]
    return "\n".join(lines)


@tool(
    "commit_direction",
    description=(
        "用户在聊天里明确确认了方向改动后，落定方向（人拍板后才调用）。\n\n"
        "当用户已明确同意某个方向（如\"就这个\"、\"改成后端吧\"）时调用，把方向写回 facts。"
        "（区分：用户还在犹豫/讨论时用 refine_direction_tool 出提案，用户拍板了才用本工具落定。）\n\n"
        "- role：目标岗位标签（如\"全栈工程师\"）——写简历/方向展示用，**不参与搜索**；可空。\n"
        "- keywords：二维查询组（组内多词、组间 OR），只管搜索。**写法见 direction 技能**（一组 = 一个概念 + 与它大范围共现的收窄词；同义变体只留最强的一个，别堆一组；2~3 组起步）。\n"
        "  硬上限 6 组（超了会报错，先精简再落定）。\n"
        "- cities：**逐组城市**（**与 keywords 平行等长、每组必填**，可选：" + CITY_LIST + "）。"
        "`[\"Java\",\"后端\"]` 配成都、`[\"产品\"]` 配杭州各搜各的（2026-09-13 ADR 0019）。"
        "**长度必须与 keywords 一致**，空城市 / 长度不等都会报错——用户想要多城市就逐组配，"
        "没定的组先问清楚再落定。\n\n"
        "**岗位处置与你无关**：落定方向后，现有未处理岗位留还是删由系统在界面上向用户确认，"
        "你不用问、也不用管——本工具只负责把方向写回。"
    ),
)
async def commit_direction_tool(
    role: str,
    keywords: list[list[str]],
    cities: list[str],
) -> str:
    """落定方向（人拍板后调用）。"""
    role = role.strip()
    raw_keywords = keywords or []
    cleaned_keywords = [
        [str(w).strip() for w in (g if isinstance(g, list) else [g]) if str(w).strip()]
        for g in raw_keywords
    ]
    cleaned_keywords = [g for g in cleaned_keywords if g]
    if not cleaned_keywords:
        return "没收到方向搜索词——请告诉我要落定的目标岗位。"
    cleaned_cities = [str(c).strip() for c in (cities or [])]
    try:
        r = await commit_direction(role=role, keywords=cleaned_keywords, cities=cleaned_cities, record_event=False)
    except ConflictError as e:
        return f"方向落定失败：{e.message}"
    # 落定后若有未处理岗位 → 存单例，chat.py 读走透传前端就地弹「留/删」（岗位处置与 agent 无关）
    if r.pending_disposal_count > 0:
        set_pending_disposal(r.pending_disposal_count)
    # 方向已落定 → 存信号，chat.py 读走透传前端 direction_changed（跨页同步方向标签）
    set_direction_changed()
    kw = render_keyword_groups(r.keywords)
    city_desc = render_group_cities(r.keywords, r.cities)
    return f"已落定求职方向：岗位「{r.role or '未指定'}」、逐组城市 {city_desc}、查询组「{kw}」。"


@tool(
    "query_market",
    description=(
        "现场查招聘市场（猎聘），拿客观统计，**只用来摸大致市场方向**（ADR 0007）。\n\n"
        "定方向前摸底、或想看「这个方向机会多不多、门槛在哪、哪座城明显偏小」时调用。"
        "工具按给定查询组逐组查猎聘，返回**原始统计**：岗位量 / 城市分布 / 学历 / 年限要求 / 薪资段。\n\n"
        "**能力边界（别越界用）**：\n"
        "- 它是把一组词拼成一句话丢进猎聘搜索框，**只看得到猎聘一个平台、只搜得到岗位标题**，"
        "**看不到 JD 正文内容**。\n"
        "- 它的搜索方式**跟后台抓取那套关键词检索是两条路**——所以它对检索词**没有发言权**：\n"
        "  **不能用它检验检索词效果，也不能拿它当制定检索词的依据**（检索词怎么写见 direction 技能规则）。\n"
        "- 用户要的是「查公司情报 / 看岗位具体内容 / 联网搜索」这类它做不到的，**如实说做不到**，别硬凑。\n\n"
        "- keywords：二维查询组（组内多词、组间 OR），从对话里用户说过的候选方向直接传——"
        "**不必等方向落库**。如 [[\"Vue\",\"前端\"],[\"React\",\"前端\"],[\"前端开发\"]]。"
        "**最多 6 组**（超了会报错，先精简到 6 组以内再查）。\n"
        "- cities：**逐组城市**（**与 keywords 平行等长**，可选：" + CITY_LIST + "），如 "
        "[\"杭州\",\"杭州\",\"成都\"]。**城市挂到组上**（2026-09-13 ADR 0019）——每组查自己那个城市。\n"
        "  **比城市写法（重要）**：想知道「同一个岗位在哪座城更好」要**一城一调**——"
        "`query_market([[\"前端\"]],[\"北京\"])`、再 `query_market([[\"前端\"]],[\"上海\"])`，"
        "拿两份快照自己比。**一次调用是一份合并统计**，多城塞一次会把城混在一起、看不出差别。\n"
        "- **返回里带「每组召回诊断」**（每个词的标题命中数 / 全部词同时命中数 / 样本标题）——"
        "它是**摸方向时的佐证**，不是判断检索词好坏的依据：召回近乎 0 → 这方向/这城可能没肉；"
        "样本标题全是无关岗 → 方向本身可能偏了。**别拿它去验词、改词。**\n"
        "- 只返回客观数字，**不做判断**——「用户 vs 市场缺什么」由你拿这些数字比对已注入的"
        "facts（经历/技能/学历）后自己讲出，带理由、带差距、带风险。\n"
        "- 没给 keywords 会返回「先给个方向」的提示；查询失败返回拿不到市场数据的说明，"
        "你如实告诉用户，别硬编数字。"
    ),
)
async def query_market_tool(
    keywords: list[list[str]],
    cities: list[str] | None = None,
) -> str:
    """现场查招聘市场（猎聘）。"""
    raw_keywords = keywords or []
    cleaned_keywords = [
        [str(w).strip() for w in (g if isinstance(g, list) else [g]) if str(w).strip()]
        for g in raw_keywords
    ]
    cleaned_keywords = [g for g in cleaned_keywords if g]
    cleaned_cities = [str(c).strip() for c in (cities or [])]
    if not cleaned_keywords:
        return "没收到查询方向——请把候选岗位/技能组（如 [['AI应用开发','LangChain'],['大模型应用开发']]）传进来，或先聊出个方向。"
    try:
        snap = await query_market(keywords=cleaned_keywords, cities=cleaned_cities)
    except LLMUnavailableError as e:
        return f"市场查询失败：{e.message}"
    if snap is None:
        return "查询组里没有有效关键词——请给个具体的岗位/技能组。"
    return render_market_snapshot(snap)


# 9 个业务工具的清单（组装 deep agent 用；单一真相源，工具名只在 @tool 装饰器里写一次）。
@tool("calc_timeline")
async def calc_timeline_tool(
    periods: list[dict[str, Any]],
) -> str:
    """算一组工作/项目/教育经历的时长、距今、空窗与重叠，用来判断时长权重、察觉时间线矛盾。

    当你需要「量化」时间线时调它，别自己心算（跨年、含「至今」、多段求并集都容易算错）。
    典型场景：对比两份工作的时长权重、看上一段与上上段之间有无空窗 gap、发现两份经历时间重叠
    （全职经历重叠 = 数据矛盾，要及时跟用户核对纠正）、算「至今」到底有多久。

    periods：时间段数组，每项 {start, end, label}：
      - start / end：「YYYY」或「YYYY-MM」（如 "2018" / "2021-06"）；end 传 "至今"/"现在" 表进行中。
      - label：这段叫什么（公司/项目名），便于在结果里指认，可留空。
    返回：每段的时长（X 年 Y 个月）、距今多久、相邻空窗（gap 月数）、重叠对、总工龄（并集，重叠不重复计）。
    """
    parsed = [
        TimelinePeriod(
            start=str(p.get("start", "")),
            end=(None if p.get("end") is None else str(p.get("end"))),
            label=str(p.get("label", "")),
        )
        for p in periods
        if isinstance(p, dict)
    ]
    today = datetime.now(UTC).date().isoformat()
    return render_timeline_report(calc_timeline(parsed, today=today))


TOOLS = [
    record_facts_tool,
    supersede_fact_tool,
    calc_timeline_tool,
    suggest_improvements_tool,
    update_suggestion_tool,
    apply_suggestions_tool,
    generate_resume_tool,
    refine_direction_tool,
    commit_direction_tool,
    query_market_tool,
]
