"""optimization service：1.2 优化建议流的编排。

2026-08-10 落库升级（建议组从聊天暂存 → 落库对象，带状态机）：
- suggest：suggest_improvements 工具产出 → 落库 pending（面板左栏）。
- accept：pending → confirmed（去重：已 confirmed/rejected/discussing 不再接受）。
- reject：pending → rejected + 记偏好"拒掉这一类"。
- 聊一聊：pending → discussing（面板右栏「正在聊」）；裁决（decide_suggestion）
  从 discussing 定论：accept→confirmed / reject→rejected / refine→内容更新回 pending。
- apply（"开始改"，2026-09-09 ADR 0015）：**不再在本 service**——折进统一简历图
  （services/rewrite.generate_preview(apply_confirmed=True) 读 confirmed 渲染成文本进图），
  人门确认后由 generate_confirm 调 clear_settled_for_new_version 结清。本 service 只留
  建议状态机（suggest/accept/reject/discuss/retract/refine/split）+ 结清 + 偏好。
"""

from datetime import UTC, datetime

from app.core.errors import ConflictError
from app.core.logging import logger
from app.repositories.documents import latest_document
from app.repositories.optimization import (
    add_custom_preference,
    add_preference,
    add_suggestion,
    get_suggestion,
    list_preferences,
    list_suggestions,
    set_split_from,
    soft_settle_by_status,
    update_suggestion_content,
    update_suggestion_status,
)
from app.schemas.optimization import (
    PendingSuggestion,
    PendingSuggestionsResponse,
    Suggestion,
    SuggestionDecision,
)
from app.services.documents import document_is_busy
from app.services.sessions import current_session


def _parse_iso(s: str) -> datetime:
    """ISO 字符串 → 时区感知 datetime（naive 当 UTC）。"""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _as_utc(v: datetime | str | None) -> datetime | None:
    """归一 changed_since 入参（datetime / ISO 串 / None）为 UTC-aware datetime。"""
    if v is None:
        return None
    if isinstance(v, str):
        return _parse_iso(v) if v else None
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)


def _ensure_not_applying() -> None:
    """文档任务（apply/改写/生成/修复/回滚/重置）执行中，禁止改动建议状态。

    文档任务会「版本变更统一结清」清建议——期间新确认的建议会被清了又没进快照
    （改了没改、清了没清）。本守卫由用户 HTTP 入口调用（撞锁抛 409）；
    agent 工具（decide/propose）不走这里，返回字符串让 agent 转述（见各自实现）。
    """
    if document_is_busy():
        raise ConflictError("正在处理文档任务，请稍候再操作建议")


def render_panel(
    panel: PendingSuggestionsResponse,
    *,
    changed_since: datetime | str | None = None,
) -> str:
    """把面板四栏渲染成注入文本，附变化标记 + 两道门控（积压刹车 / 开始改时机）。

    抽到本 service（2026-09-09）：建议面板是 optimization 的领域，渲染函数本就该住
    这里——deep agent 的 build_deep_system_prompt 复用它，不反向依赖别处。

    - 四栏：待定 / 正在聊 / 已确认 / 已拒绝（2026-08-25 补已拒绝，§12.6 D6 闸一）。
    - **变化标记（§12.6 D10 档1，2026-09-14 落地）**：`changed_since` = 本轮 agent 开跑前
      的时间点快照；`updated_at > changed_since` 的行标「← 你刚改的」。避免 agent 把自己
      刚做的动作（或用户在面板上的点击）误当"一开始就在这儿"。旧实现用两份面板对象逐行
      比状态，但 `build_deep_system_prompt` 每轮现读面板、从来没传过 initial_panel——
      死代码。改为传时间戳：一行一个快照，调用方只需在开跑前记一次 `datetime.now(UTC)`。
    - 门控（§12.6 D6 闸二 / D9）：待定积压 >5 提示停提新 + 催办；待定清零且 confirmed
      非空提示「可以开改」。
    """
    changed_since_dt = _as_utc(changed_since)

    def was_changed(s: PendingSuggestion) -> bool:
        if changed_since_dt is None or not s.updated_at:
            return False
        try:
            at = _parse_iso(s.updated_at)
        except ValueError:
            return False
        return at > changed_since_dt

    def row(s: PendingSuggestion, *, show_suggested_only: bool = False) -> str:
        body = s.suggested if show_suggested_only else f"{s.original} → {s.suggested}"
        mark = f"  ← 你刚改成了「{s.status}」" if was_changed(s) else ""
        return f"  #{s.id} [{s.type}] {s.target}: {body}{mark}"

    panel_lines: list[str] = []
    if panel.pending:
        panel_lines.append("【待定】")
        panel_lines.extend(row(s) for s in panel.pending)
    if panel.discussing:
        panel_lines.append("【正在聊】")
        panel_lines.extend(row(s) for s in panel.discussing)
    if panel.confirmed:
        panel_lines.append("【已确认】")
        panel_lines.extend(row(s, show_suggested_only=True) for s in panel.confirmed)
    if panel.rejected:
        panel_lines.append("【已拒绝】")
        panel_lines.extend(row(s) for s in panel.rejected)
    if not panel_lines:
        panel_lines.append("（暂无建议）")

    # 门控（§12.6 D6 闸二）：待定积压超阈值 → 停提新、改为催办。
    if len(panel.pending) > 5:
        panel_lines.append(
            f"\n【待定积压 {len(panel.pending)} 条】暂停提新建议——先逐条念给用户、"
            "用 update_suggestion 帮 ta 把待定清空，再继续优化。"
        )
    # 门控（§12.6 D9）：待定清零且 confirmed 非空 → 开始改时机到。同一门控状态只提议一次。
    if not panel.pending and panel.confirmed:
        panel_lines.append(
            f"\n【开始改时机】已确认 {len(panel.confirmed)} 条、待定已清零——可提议用户开一轮改"
            "（apply_suggestions）；若已确认只有 1~2 条，建议先攒攒。"
        )
    return "\n".join(panel_lines)


async def record_suggestion(
    s: Suggestion, *, status: str = "pending", origin: str = "agent", document_id: int | None = None
) -> PendingSuggestion:
    """落库一条建议：聊天 agent 提的 → pending（面板待定）；投递页分析产的处方 → proposed。

    status/origin（2026-09-14，apply.md §11.7.3）：处方走 proposed + origin=job_analysis
    （不进面板四栏，点「改进」才转 pending）；agent 工具走默认 pending + origin=agent。
    document_id：处方基于「当前简历」——显式传入可避免与 latest_document 竞态，不传则读最新稿。
    """
    sess = await current_session()
    session_id = sess.id if sess is not None else 0
    doc_id = document_id
    if doc_id is None:
        doc = await latest_document()
        doc_id = doc.id if doc is not None else 0
    return await add_suggestion(session_id, doc_id, s, status=status, origin=origin)


async def promote_suggestion(suggestion_id: int) -> PendingSuggestion | None:
    """收录一条处方进优化点面板：「改进」按钮 → proposed → pending（apply.md §11.7.7）。

    仅 proposed 可收录（已被 agent/用户操作过的不再重复收录）。到这一步面板就多了一行——
    而面板**本就每轮注入**聊天 agent（`_assembly.py`），故这既是"收录"也是"交给 agent"。
    """
    row = await get_suggestion(suggestion_id)
    if row is None or row.status != "proposed":
        return None
    _ensure_not_applying()
    await update_suggestion_status(suggestion_id, "pending")
    return PendingSuggestion(
        id=suggestion_id,
        type=row.type,  # type: ignore[arg-type]
        target=row.target,
        original=row.original,
        suggested=row.suggested,
        reason=row.reason,
        severity=row.severity,  # type: ignore[arg-type]
        status="pending",
        origin=row.origin,  # type: ignore[arg-type]
    )


async def proposed_suggestions(document_id: int | None = None) -> list[PendingSuggestion]:
    """读「未处理」分析产的处方（status=proposed，投递页分析面板用）。

    document_id 给了就按稿过滤（当前简历）；不给 = 全部 proposed（版本变了就脱锚，
    结清由版本变更那套逻辑兜底——与其它状态同一条规矩）。
    """
    return await list_suggestions(document_id=document_id, status="proposed")


async def accept_suggestion(suggestion_id: int) -> PendingSuggestion | None:
    """确认一条已落库建议：pending/discussing → confirmed。

    2026-08-10 去重：已 confirmed/rejected 的不可再接受（前端三栏语义保证，
    后端兜底）——旧实现同一条可无限次收录（bug）。
    """
    row = await get_suggestion(suggestion_id)
    if row is None:
        return None
    if row.status in ("confirmed", "rejected"):
        return None  # 已定论，不再接受（去重兜底）
    _ensure_not_applying()
    await update_suggestion_status(suggestion_id, "confirmed")
    return PendingSuggestion(
        id=suggestion_id,
        type=row.type,  # type: ignore[arg-type]
        target=row.target,
        original=row.original,
        suggested=row.suggested,
        reason=row.reason,
        severity=row.severity,  # type: ignore[arg-type]
        status="confirmed",
        origin=row.origin,  # type: ignore[arg-type]
    )


async def start_discuss(suggestion_id: int) -> None:
    """聊一聊：pending → discussing（面板右栏「正在聊」区）。"""
    row = await get_suggestion(suggestion_id)
    if row is None or row.status not in ("pending", "discussing"):
        return
    _ensure_not_applying()
    await update_suggestion_status(suggestion_id, "discussing")


async def update_suggestion(
    suggestion_id: int,
    decision: SuggestionDecision,
    refined: Suggestion | None = None,
    split_to: list[Suggestion] | None = None,
) -> str:
    """更新一条建议（agent `update_suggestion` 工具执行，2026-08-25 起开放全状态操作）。

    decision（§12.6 优化点操作权，2026-08-29 分级自主度）：
    - accept → confirmed（右栏「已确认」）——**高风险**，agent 只在用户给了**确定口吻**才调。
    - reject → rejected（进灰栏，版本内可撤回；**不即时记偏好**——偏好延迟到
      版本变更清空 rejected 时按最终结果记，2026-08-12）——**高风险**，确定口吻才调。
    - refine → 更新内容，回 pending（细化/修改，仍待用户最终确认）
    - split → 分裂成多条新建议（原条回 pending 保留，新条 pending + split_from）
    - discuss → discussing（「聊一聊」）——**低风险**，agent 随便动（聊哪条挪哪条）。
    - retract → pending（「撤回」）——**低风险**，agent 随便动。

    分级自主度（2026-08-29）：待定/正在聊两栏（未定论）之间 agent 随便挪；只有往
    confirmed/rejected 这两个**定论态**走（要真改进简历 / 记偏好）才要用户确定口吻。
    这是按"动作风险"分级，不是按"用户有没有下命令"分级。

    状态守卫（2026-08-25 推翻 S4-1/S4-2）：只要建议**还活跃**（resolved_in_document_id
    为 NULL）就能操作。终态（applied/archived）与已结清的建议不可操作。

    返回给 agent 的可读结果（agent 据此组织聊天话术）。
    """
    row = await get_suggestion(suggestion_id)
    if row is None:
        return f"建议 #{suggestion_id} 不存在。"
    # 2026-08-25 放宽守卫：只拦已结清（终态 applied/archived，锚在旧稿上）——
    # pending/confirmed/rejected/discussing 四态都活跃，agent 都可操作（§12.6）。
    if row.status in ("applied", "archived"):
        return f"建议 #{suggestion_id} 已结清（{row.status}），不在当前活跃面板，无法操作。"
    # 文档任务执行中（agent 工具路径）：返回可读反馈，不抛异常——让 agent 转述给用户
    if document_is_busy():
        return "正在处理文档任务，这条建议稍后再操作。"

    if decision == "accept":
        await update_suggestion_status(suggestion_id, "confirmed")
        return f"建议 #{suggestion_id} 已确认（将应用于下次改写）。"
    if decision == "reject":
        await update_suggestion_status(suggestion_id, "rejected")
        return f"建议 #{suggestion_id} 已拒绝（进灰栏，本版本内可撤回；偏好将在版本变更时记录）。"
    if decision == "refine":
        if refined is None:
            return f"细化建议 #{suggestion_id} 需要传 refined（新的 suggested/reason/target）。"
        await update_suggestion_content(suggestion_id, refined)
        return f"建议 #{suggestion_id} 已细化更新，回到待定（面板左栏）。"
    if decision == "split":
        sess = await current_session()
        session_id = sess.id if sess is not None else 0
        if not split_to:
            return f"分裂建议 #{suggestion_id} 需要传 split_to（拆分出的新建议）。"
        # 原条回 pending（保留），新条 pending + split_from
        await update_suggestion_status(suggestion_id, "pending")
        new_ids: list[int] = []
        for s in split_to:
            p = await add_suggestion(session_id, row.document_id, s, origin=row.origin)
            assert p.id is not None
            await set_split_from(p.id, suggestion_id)
            new_ids.append(p.id)
        return f"建议 #{suggestion_id} 已分裂为 {len(new_ids)} 条（#{', '.join(map(str, new_ids))}），原条回到待定。"
    if decision == "discuss":
        await update_suggestion_status(suggestion_id, "discussing")
        return f"建议 #{suggestion_id} 已移入「正在聊」（面板右栏），我们边聊边定。"
    if decision == "retract":
        await update_suggestion_status(suggestion_id, "pending")
        return f"建议 #{suggestion_id} 已撤回，回到待定（面板左栏）。"
    # 防御：decision 是闭集 Literal，工具边界已拦非法值——到这儿说明集被改成不同步了。
    raise ValueError(f"未覆盖的 suggestion decision：{decision}")


async def reject_suggestion(suggestion_id: int, reason: str) -> None:
    """拒绝一条已落库建议：pending/discussing → rejected（进灰栏，版本内可撤回）。

    2026-08-12：**不即时记偏好**——偏好延迟到版本变更清空 rejected 时
    按最终结果记（拒绝可撤回，撤回后不算"这一类"）。
    2026-09-14：**拒因落库**（此前只 logger.info 丢了）——"能力不到"这类值不再蒸发，
    它是方向降级的证据来源（apply.md §11.7.8）。
    """
    row = await get_suggestion(suggestion_id)
    if row is None or row.status == "rejected":
        return
    _ensure_not_applying()
    await update_suggestion_status(suggestion_id, "rejected", reject_reason=reason)
    logger.info("optimization_rejected", suggestion_id=suggestion_id, type=row.type, reason=reason)


async def retract_suggestion(suggestion_id: int) -> None:
    """撤回一条已定论建议（confirmed/discussing/rejected → pending）。

    2026-08-12：右三栏每条「撤回」按钮——已确认（还没开始改，改前可撤回）、
    正在聊、已拒绝（版本内拉回重新决定）都能回到待定。
    已 pending / 不存在 → no-op（幂等）。
    """
    row = await get_suggestion(suggestion_id)
    if row is None or row.status == "pending":
        return
    _ensure_not_applying()
    await update_suggestion_status(suggestion_id, "pending")
    logger.info("optimization_retracted", suggestion_id=suggestion_id, from_status=row.status)


async def pending_suggestions() -> PendingSuggestionsResponse:
    """读建议面板四栏（待定/已确认/正在聊/已拒绝，2026-08-12 加「已拒绝」）。"""
    all_rows = await list_suggestions(include_rejected=True)
    pending = [s for s in all_rows if s.status == "pending"]
    confirmed = [s for s in all_rows if s.status == "confirmed"]
    discussing = [s for s in all_rows if s.status == "discussing"]
    rejected = [s for s in all_rows if s.status == "rejected"]
    return PendingSuggestionsResponse(pending=pending, confirmed=confirmed, discussing=discussing, rejected=rejected)


async def count_suggestions() -> int:
    """读当前未定论建议数（面板 pending 待定 + discussing 正在聊 = 未定论）。

    2026-08-12 口径不变：rejected/confirmed 是已定论，不算"待处理"。
    """
    panel = await pending_suggestions()
    return len(panel.pending) + len(panel.discussing)


async def clear_settled_for_new_version(new_document_id: int, *, discard: bool = False) -> dict[str, int]:
    """版本变更统一结清（2026-08-12 策略反转，resume.md §12.3 决策四修订）。

    - **保留 discussing**：聊一聊的内容留到下一版本继续聊（是否适合新版由用户判断）。
    - **结清 pending / confirmed / rejected / proposed**：定论项锚在旧稿上，版本变更即脱锚；
      proposed（投递页分析产的处方）同锚在当前稿、同样会脱锚——一并 archived（若仍留 proposed）。
    - 清空 rejected 时，**延迟记录最终被拒类别的偏好**（拒绝不即时记——
      撤回后不算"这一类"；这里按版本变更时仍留在灰栏的最终结果记"拒掉这一类"）。

    2026-08-20 大雷修复：结清从**物理删除**改为**软标记 + 全留存**——
    confirmed→applied、pending/rejected/proposed→archived，记 `resolved_in_document_id=new_document_id`
    （溯源：哪版产生/哪版结清）。行不删，供「这版改了哪些点」回查（§11.8）。

    new_document_id：本次版本变更的目标稿 id（生成/改写=新稿 id；回滚=被覆盖稿 current.id）。
    discard=True（回滚）：confirmed 也→archived——回滚丢弃这轮改动、没应用进目标稿，
    不能谎称 applied。生成/改写走默认 False。

    返回结清行数统计 {pending, confirmed, rejected}（供日志/事件文案）。
    """
    cleared = await soft_settle_by_status(
        {"pending", "confirmed", "rejected", "proposed"}, new_document_id, discard=discard
    )
    # cleared 是结清前快照（原状态 pending/confirmed/rejected），直接按原状态计数
    counts = {"pending": 0, "confirmed": 0, "rejected": 0}
    rejected_by_type: dict[str, str] = {}
    for s in cleared:
        counts[s.status] = counts.get(s.status, 0) + 1
        if s.status == "rejected":
            rejected_by_type.setdefault(s.type, s.suggested)
    # 延迟记偏好：结清的 rejected 按 type 聚合（同类只记一条）
    for scope, suggested in rejected_by_type.items():
        await add_preference(kind="reject", scope=scope, content=f"拒绝建议：{suggested}")
    if rejected_by_type:
        logger.info("preferences_recorded_on_version_change", types=sorted(rejected_by_type))
    return counts


async def record_custom_preference(scope: str, content: str) -> str:
    """记一条跨版本 custom 偏好（方案 A：agent 把改简历决定持久化，每版注入生成/改写 prompt）。

    同 scope 新顶旧（矛盾时后决定覆盖先决定）。返回给人/agent 看的确认文案。
    """
    scope = scope.strip()
    content = content.strip()
    await add_custom_preference(scope, content)
    logger.info("custom_preference_recorded", scope=scope)
    return f"已记下这条长期规则（{scope}）：{content}——以后每一版都会遵守。"


async def get_preferences() -> str:
    """把用户判定偏好格式化化成注入文本（拒绝项 + 自定义标准）。"""
    prefs = await list_preferences()
    if not prefs:
        return ""
    lines = [f"- [{p.scope}] {p.content}" for p in prefs]
    return "\n".join(lines)
