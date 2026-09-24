"""optimization service：优化点（1.2）的编排。

**2026-09-23 一张表**：优化点 = 改动记录（`change_records`），`reason`（为什么）+ `changes`
（改什么，复合子项，各自带 status）。取代旧的扁平 `optimization_pending`（建议逐条一行）
与 `preferences(kind=custom)`（自定义标准）——「优化点和改进记录本就是一回事」。

- 落库：聊天 agent（`record_change` / `suggest_improvements`）→ pending（面板待定）；
  投递页分析产的处方 → 同样落这里（origin=job_analysis），点「改进」收录（`promote_suggestion`）。
- 操作粒度 = **单条子项**：`set_change_item_status(record_id, change_id, status)` 逐条定论；
  记录级 `set_record_status` 只在子项全空或整条存废时用。
- 跨版本决策：`kind=decision` 的记录每版注入生成/改写 prompt（`build_decisions_text`）；
  `kind=change` 是本轮整改，版本变更时结清（`clear_settled_for_new_version`）。
- apply（「开始改」，ADR 0015）：不在本 service——折进统一简历图
  （`services/rewrite.generate_preview(apply_confirmed=True)` 读**已确认子项**渲染成文本进图），
  人门确认后由 `generate_confirm` 调 `clear_settled_for_new_version` 结清。
"""

from datetime import UTC, datetime

from app.core.errors import ConflictError
from app.core.logging import logger
from app.repositories.documents import latest_document
from app.repositories.optimization import (
    add_change_record,
    append_change_items,
    clear_all_change_records as _clear_all_change_records,
    get_change_record,
    get_suggestion,
    list_change_records,
    query_change_records,
    soft_settle_by_status,
    soft_settle_change_records,
    update_change_item_status,
    update_record_status,
)
from app.schemas.optimization import ChangeRecord
from app.services.documents import document_is_busy
from app.services.sessions import current_session

# 面板/agent 的决策动词 → 子项（或记录）状态。accept/reject 进定论态，discuss/retract 在未定论态之间。
_DECISION_TO_STATUS = {
    "accept": "confirmed",
    "reject": "rejected",
    "discuss": "discussing",
    "retract": "pending",
}


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
    """文档任务（apply/改写/生成/修复/回滚/重置）执行中，禁止改动优化点状态。

    文档任务会「版本变更统一结清」清改动记录——期间新确认的会清了又没进快照
    （改了没改、清了没清）。本守卫由用户 HTTP 入口调用（撞锁抛 409）；
    agent 工具（set_change_status / record_change）不走这里，返回字符串让 agent 转述。
    """
    if document_is_busy():
        raise ConflictError("正在处理文档任务，请稍候再操作优化点")


async def render_records_panel(*, changed_since: datetime | str | None = None) -> str:
    """把改动记录渲染成注入文本（供聊天 agent 每轮看工作底稿）。

    每条：`#{id}（kind）reason` + 缩进的子改动点 `- [status] target: original → suggested`。
    变化标记（← 你刚改的）：updated_at > changed_since（本轮开跑前记的时间点快照）。

    **决策不随版本结清而消失（2026-09-23）**：`kind=decision` 是跨版本约束，结清标它
    `archived` 只是记账（「这条在哪版兑现过」），**约束本身依然成立**——所以这里连同
    已结清的决策一起注入（标「已兑现·持续生效」）。只取活跃记录会让 agent 把结清误读成
    「这个决定作废了」，进而放任后续版本回退它（2026-09-23 v6 技能块回归的放大器）。
    本轮整改（kind=change）结清即随版本出清，不在此列。
    """
    changed_since_dt = _as_utc(changed_since)
    records = await list_change_records(active_only=True)
    standing = [r for r in await list_change_records(kind="decision") if r.id not in {x.id for x in records}]
    if not records and not standing:
        return "（暂无改动记录）"
    lines: list[str] = []
    for r in records + standing:
        mark = ""
        if changed_since_dt is not None and r.updated_at:
            try:
                if _parse_iso(r.updated_at) > changed_since_dt:
                    mark = "  ← 你刚改的"
            except ValueError:
                pass
        kind_tag = "决策" if r.kind == "decision" else "整改"
        status_tag = "·已兑现·持续生效" if r.kind == "decision" and r.status != "pending" else ""
        lines.append(f"#{r.id} 【{kind_tag}·{r.status}{status_tag}】{r.reason}{mark}")
        for c in r.changes:
            lines.append(f"  - [{c.status}] {c.target}: {c.original} → {c.suggested}")
    return "\n".join(lines)


async def query_decisions(keyword: str = "") -> str:
    """查历史改动记录（原因 + 改动点），格式化成 agent 可读文本。

    keyword 非空 → LIKE 过滤 reason/changes；空 → 返回全部活跃记录。
    供 agent 决策前查历史（避免与已定决策相悖）。
    """
    records = await query_change_records(keyword) if keyword else await list_change_records()
    if not records:
        return "（没有相关历史改动记录）"
    lines: list[str] = []
    for r in records:
        head = f"#{r.id}（{r.kind}）{r.reason}"
        lines.append(head)
        for c in r.changes:
            lines.append(f"  - [{c.status}] {c.target}: {c.original} → {c.suggested}")
    return "\n".join(lines)


async def build_decisions_text() -> str:
    """把「持续生效的决策记录」（kind=decision）渲染成注入文本，喂生成/改写 prompt 的 {preferences}。

    只取活跃（未结清）的 decision——它们是跨版本约束（如"不要项目经历栏，并进工作经历"）。
    """
    records = await list_change_records(kind="decision", active_only=True)
    if not records:
        return ""
    return "\n".join(f"- {r.reason}" for r in records)


async def record_decision(reason: str, changes: list[dict] | None = None) -> str:
    """记一条跨版本决策记录（kind=decision），每版注入生成/改写 prompt。返回确认文案。

    reason 必填（为什么）；changes 可空（原因先行、子项后补）或带具体改动点。
    """
    reason = reason.strip()
    if not reason:
        return "reason 为空，未记录任何决策。"
    rec = await record_change(reason, changes or [], kind="decision")
    logger.info("decision_recorded", record_id=rec.id)
    return f"已记下这条长期决策（#{rec.id}）：{reason}——以后每一版都会遵守。"


async def record_change(
    reason: str,
    changes: list[dict] | None = None,
    *,
    kind: str = "change",
    origin: str = "agent",
    status: str = "pending",
    document_id: int | None = None,
) -> ChangeRecord:
    """落库一条改动记录（原因 + 子项）：聊天 agent 提的 → pending；投递页处方 → job_analysis。

    document_id 不给则读当前最新稿（避免与 latest_document 竞态可显式传）。
    """
    sess = await current_session()
    session_id = sess.id if sess is not None else 0
    doc_id = document_id
    if doc_id is None:
        doc = await latest_document()
        doc_id = doc.id if doc is not None else 0
    return await add_change_record(
        reason=reason, changes=changes, session_id=session_id, document_id=doc_id,
        status=status, kind=kind, origin=origin,
    )


async def _append_or_create_by_reason(reason: str, changes: list[dict]) -> ChangeRecord:
    """同原因不新开行：已有一条**活跃的 change 记录**用同一 reason 就 append 子项，否则新建。

    兑现工具契约里「同原因的新改动 append 进同一条、不重复抄原因」。只在 kind=change
    的活跃记录里找——decision 是跨版本约束，不与本轮整改混。

    原因先行时 changes 可为空：这一支会新建一条 reason 挂着的空记录，后续再 append。
    """
    reason = reason.strip()
    for r in await list_change_records(kind="change", active_only=True):
        if r.reason.strip() == reason and r.id is not None:
            updated = await append_change_items(r.id, changes) if changes else r
            return updated or r
    return await record_change(reason, changes, kind="change")


async def set_change_item_status(record_id: int, change_id: int, status: str) -> str:
    """子项级状态迁移（操作粒度 = 单条子项）。返回可读结果。

    文档任务执行中的处理分两条路（沿用 2026-08-25 的分工）：
    - **用户入口**（面板 URL）撞锁要抛 409——由路由层先调 `_ensure_not_applying()`。
    - **agent 工具**路径不抛，返回可读反馈让 agent 转述（工具层是这么调的）。
    本函数走后者（不抛）；抛的那条由路由层的 guard 负责（见 `app/api/v1/optimization.py`）。
    """
    ok = await update_change_item_status(record_id, change_id, status)
    if not ok:
        return f"改动记录 #{record_id} 的子项 #{change_id} 不存在。"
    return f"改动点 #{record_id}.{change_id} 已标为 {status}。"


async def set_record_status(record_id: int, status: str) -> str:
    """记录级状态迁移（子项全空时用、或整条存废）。返回可读结果。"""
    rec = await get_change_record(record_id)
    if rec is None:
        return f"改动记录 #{record_id} 不存在。"
    await update_record_status(record_id, status)
    return f"改动记录 #{record_id} 已标为 {status}。"


async def pending_records(*, origin: str | None = None) -> list[ChangeRecord]:
    """读当前活跃（未结清）的改动记录，供面板/渲染。origin 可选过滤（agent/job_analysis）。"""
    return await list_change_records(origin=origin, active_only=True)


async def count_open_records() -> int:
    """未定论改动记录数（有 pending/discussing 子项的记录）——供 chat 响应。

    子项级口径：一条记录里只要还有待定/在聊的子项就算「未定论」（与面板待定栏一致）。
    无子项的记录不算（那是还没落地的决策，不是"待处理条目"）。
    """
    records = await list_change_records(active_only=True)
    return sum(
        1
        for r in records
        if any(c.status in ("pending", "discussing") for c in r.changes)
    )


async def clear_all_change_records() -> int:
    """核爆：清空全部改动记录。"""
    return await _clear_all_change_records()


async def promote_suggestion(suggestion_id: int) -> ChangeRecord | None:
    """投递页处方「改进」：把一条 proposed 处方收录成一条改动记录，原行结清。

    2026-09-23 起处方并入 change_records——面板只读新表，故这里落新记录、把旧行软标
    archived（记 resolved_in_document_id 留溯源），而不是旧实现的 proposed → pending。

    仅 proposed 可收录（已被 agent/用户操作过的不再重复收录）。到这一步面板就多了一行——
    而面板**本就每轮注入**聊天 agent（`_assembly.py`），故这既是"收录"也是"交给 agent"。
    """
    row = await get_suggestion(suggestion_id)
    if row is None or row.status != "proposed":
        return None
    _ensure_not_applying()
    # 收录进新表：旧行一条 = 新记录的一个子项（reason 沿用处方的理由，缺失给通用标题）。
    rec = await record_change(
        row.reason.strip() or "投递页分析处方",
        [
            {
                "target": row.target,
                "original": row.original,
                "suggested": row.suggested,
                "status": "confirmed",  # 「改进」= 用户已认可这条处方 → 直接进已确认栏，可「开始改」
                "type": row.type,
                "severity": row.severity,
            }
        ],
        origin="job_analysis",
        document_id=row.document_id,
    )
    # 旧行软结清（不删，留溯源）。discard=False：它被收录进新记录，不算被丢弃。
    await soft_settle_by_status({"proposed"}, row.document_id or rec.document_id, ids={suggestion_id})
    logger.info("prescription_promoted", suggestion_id=suggestion_id, record_id=rec.id)
    return rec


async def clear_settled_for_new_version(
    new_document_id: int,
    *,
    applied_changes: set[tuple[int, int]] | None = None,
) -> dict[str, int]:
    """版本变更统一结清（2026-08-12 策略反转，resume.md §12.3 决策四修订）。

    ⚠️ **只服务于「生成确认 / 开始改」**（2026-09-24）：**回滚不走这里**——回滚 = 整份恢复
    目标版开始时的工作台快照（资料集 + 改动记录一起换回），没有"结清"这一步。

    - **保留 discussing**：聊一聊的内容留到下一版本继续聊（是否适合新版由用户判断）。
    - **结清 pending / confirmed / rejected**：定论项锚在旧稿上，版本变更即脱锚。
    - 清空时**延迟记录最终被拒类别的偏好**（拒绝不即时记——撤回后不算"这一类"；
      rejected 子项按 kind=decision 记一条「拒掉这一类」决策，跨版本注入）。

    2026-08-20 大雷修复：结清从**物理删除**改为**软标记 + 全留存**——
    confirmed→applied、pending/rejected→archived，记 `resolved_in_document_id=new_document_id`
    （溯源：哪版产生/哪版结清）。行不删，供「这版改了哪些点」回查（§11.8）。
    2026-09-23：旧的 `optimization_pending` 表已停用，结清只动 change_records。

    new_document_id：本次版本变更的目标稿 id（生成/改写=新稿 id）。
    applied_changes（件 1，2026-09-24）：**本版真进了图**的已确认改动点 `{(record_id, change_id)}`。
    只有它在集合里才标 `applied`；不在 = 被结清但**未应用** → `archived`。别把「有 confirmed」
    当「应用了」——`generate_resume` 这条路不带已确认改动，旧实现照样标 applied，新版开场引导
    读它就会谎报「这版做了这些调整」。None = 不关心带了什么（全部 confirmed → applied）。

    返回结清行数统计 {pending, confirmed, rejected}（供日志/事件文案）。
    """
    records = await pending_records()
    rejected: list[tuple[str, str]] = []  # (改哪类, 改法) —— 记偏好用
    counts = {"pending": 0, "confirmed": 0, "rejected": 0}
    for r in records:
        for c in r.changes:
            if c.status in counts:
                counts[c.status] += 1
                if c.status == "rejected":
                    rejected.append((c.type, c.suggested))
    # 软结清（子项级状态迁移 + 记录记 resolved_in_document_id）
    # applied_changes 必须透传：没进图的 confirmed 不许标 applied（件 1，2026-09-24）。
    await soft_settle_change_records({"pending", "confirmed", "rejected"}, new_document_id, included=applied_changes)
    # 延迟记偏好：结清的 rejected 按改哪类聚合（同类只记一条），作为跨版本决策注入。
    by_type: dict[str, str] = {}
    for type_, suggested in rejected:
        by_type.setdefault(type_, suggested)
    for type_, _suggested in by_type.items():
        await record_change(f"拒掉这一类：{type_}", kind="decision")
        logger.info("preference_recorded_on_version_change", scope=type_)
    return counts

