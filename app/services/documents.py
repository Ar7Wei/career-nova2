"""简历文档 service：文档版本化 + 快照回滚的编排。

纯数据读写（无 LLM）——Service 直接走 Repository（不绕 Graph，分层铁律）。
职责：
- 存一版文档（upload/generated）→ 建新版本前给被替换的当前版本打快照（v1 无前任不打）。
- 列版本、取当前、回滚（文档切版本 + 可选连事实还原）。
回滚语义：软作废（不删历史——目标稿之后标 superseded、目标稿恢复当前，数据全保留）；
快照还原 = 目标版本那代的事实库快照覆盖当前 active 事实。

快照时机（2026-08-06 定稿）：**建新版本前**给被替换的当前版本打快照——
vN 快照 = "vN 还是当前文档时的事实库"（vN 任期末）。v1 在 v2 创建时才打，
天然包含上传后台抽取结果；回滚自身也要先给被替换的当前版本打快照（否则
回滚后原当前版本的事实状态丢失，无法再"回滚回 v3"）。
"""

from pathlib import Path
import asyncio
from contextlib import asynccontextmanager

from app.core.errors import ConflictError, NotFoundError
from app.core.logging import logger
from app.repositories import documents as docs_repo
from app.repositories import originals as originals_repo
from app.repositories import sessions as sessions_repo
from app.repositories import snapshots as snaps_repo
from app.repositories.facts import create_facts, delete_all_facts, list_facts, update_fact
from app.repositories.crawl_state import clear_all_crawl_state
from app.repositories.analysis import clear_all_reports
from app.repositories.jobs import clear_all_followup_events, clear_all_interviews, clear_all_jobs, epoch_write_guard
from app.repositories.optimization import clear_all_change_records, clear_all_preferences
from app.repositories.settings import bump_data_epoch
from app.schemas.documents import ResumeDocument, ResumeUploadResponse
from app.schemas.facts import FactCreate, FactSource, FactUpdate
from app.schemas.resume import Typography, resume_from_json
from app.schemas.settings import AppSettingsPatch
from app.schemas.snapshots import SnapshotRecord
from app.services import extract_state
from app.services import settings as settings_service
from app.services.opening import schedule_opening
from app.services.sessions import open_session, record_current_event
from app.utils.render import render_resume

_FACT_SOURCES = ("resume_upload", "chat", "manual")


# 文档任务互斥锁（2026-08-14：把 optimization._apply_lock 收口成全局「写文档流」互斥）。
# 同一时刻只允许一个写 resume_documents / 版本变更结清的任务在跑——apply / 改写 /
# 生成确认 / 修复排版 / 回滚 / 重置，这些动作都写文档流或清建议，并发跑会互相踩踏
# （改写撞回滚、两个写 vN+1 撞号、apply 清建议时新建议溜进 confirmed 改了没改）。
# 本地单用户，进程内一把 asyncio.Lock：非阻塞 trylock，拿不到 = 忙 → 用户入口
# 抛 ConflictError(409)，agent 工具（generate_resume）返回字符串。释放 = 任务结束自动
# （正常/异常/取消都走 with 退出），无手动解锁、无死锁（LLM 超时 300s 也抛错释放）。
_document_lock = asyncio.Lock()


def document_is_busy() -> bool:
    """是否有文档任务在执行（建议状态写入口据此 409；agent 工具据此返回字符串）。"""
    return _document_lock.locked()


@asynccontextmanager
async def document_task():
    """文档任务互斥守卫（非阻塞）：拿不到锁立刻抛 ConflictError（不排队）。

    用法：`async with document_task(): ...` 包住「写文档流」那段。锁伴随任务生命周期
    自动释放，调用方无需关心解锁。
    """
    if _document_lock.locked():
        raise ConflictError("正在处理文档任务，请稍候再试")
    await _document_lock.acquire()
    try:
        yield
    finally:
        _document_lock.release()


def _fact_source(value: str) -> FactSource:
    """把快照 JSON 里的来源安全收口到 FactSource；未知值回退默认（不应发生）。"""
    return value if value in _FACT_SOURCES else "resume_upload"  # type: ignore[return-value]


async def save_document(markdown: str, source: str = "generated", html: str = "", summary: str = "", resume_json: str = "", typography: Typography | None = None) -> ResumeDocument:
    """存一版文档（LLM 组合产出），并给新稿打一份「版本开始时」工作台快照。

    2026-09-24 语义改判：快照 = **该版开始时的工作台**，打点从「建下一版前给旧版打」
    挪到**版本创建时刻**——本函数顺带打一份。`generate_confirm` 在结清改动记录后会
    **重打一次覆盖它**（快照要含刚刚那批确认/结清，见该函数）——`create_snapshot` 按
    document_id 幂等，重打即覆盖，故两条路都不会留下错的那份。
    生成/修改版无原件：original_name/ext 传空（原件只在上传版上有）。
    markdown：上传 v1 内容层（生成版空）。resume_json：生成版结构化真身（上传 v1 空）。
    html：生成版渲染快照（固定模板从 resume_json 渲染）。summary：一句话版本简述（S8）。
    typography：排版自由度配置（2026-09-02 挂版本）：调用方传当前版配置（新生成稿继承上一版）。
    """
    doc = await docs_repo.create_document(markdown=markdown, source=source, html=html, summary=summary, resume_json=resume_json, typography=typography)
    assert doc.id is not None
    await snaps_repo.create_snapshot(document_id=doc.id)
    return doc


async def save_upload(markdown: str, resume_name: str, original_bytes: bytes | None = None, original_name: str | None = None) -> ResumeUploadResponse:
    """上传转出：存文档 v1（upload 来源），原件落盘，**给 v1 打一份工作台快照**。

    2026-09-24：快照语义改为「该版开始时的工作台」，打点跟着挪到**创建时刻**——v1 在这
    打完（此时工作台是空的：还没抽取、也没聊过，正合「v1 刚建立」）。回滚到 v1 被关掉
    （见 `rollback`），这份快照是留给「后续版本按版开始打」这条链的起点。
    原件 = 上传的原始文件（PDF/HTML 等），落盘到 originals/ 供重启后原生预览（文件名消毒防路径穿越）。
    不传 original_bytes/original_name（老测试/无原件场景）→ 不落盘、元数据留空。
    summary：上传 v1 固定「最初版本」（无 LLM 时机，规则名，S8）。
    """
    # 消毒原文件名 → 决定落盘名 + 扩展名（驱动前端预览分档）
    sanitized = ""
    ext = ""
    if original_bytes is not None and original_name is not None:
        sanitized = originals_repo.sanitize_filename(original_name)
        ext = Path(sanitized).suffix.lstrip(".").lower()
    doc = await docs_repo.create_document(
        markdown=markdown,
        source="upload",
        original_name=sanitized,
        original_ext=ext,
        summary="最初版本",
    )
    if sanitized:
        assert original_bytes is not None  # 有 sanitized 说明传了原件（上方 guard）
        originals_repo.save_original(doc.version, original_bytes, sanitized)
    assert doc.id is not None
    await snaps_repo.create_snapshot(document_id=doc.id)  # v1 开始时的工作台（空）
    return ResumeUploadResponse(
        document_id=doc.id,
        version=doc.version,
        markdown=doc.markdown,
        resume_name=resume_name,
        original_name=sanitized,
        original_ext=ext,
    )


async def get_versions() -> list[ResumeDocument]:
    """列当前简历的全部稿（v1→vN 升序，不含软作废稿）。"""
    return await docs_repo.list_versions()


async def get_all_versions() -> list[ResumeDocument]:
    """列全部稿（含软作废稿，回看时间线聚合用）。"""
    return await docs_repo.list_all_versions()


async def get_current() -> ResumeDocument | None:
    """取当前（最新）简历文档。"""
    return await docs_repo.latest_document()


async def update_current_typography(typography: Typography) -> ResumeDocument:
    """排版自由度防抖落库（2026-09-02）：改当前版排版配置 → 用该版 resume_json 重渲染 html + 配置一起落库。

    只存配置数字不够——预览/导出吃的是落库 html，必须同步重渲染（render_resume 纯函数，
    同一 resume_json + 新配置吐带新 :root 排版变量的 html）。只对生成简历开放（有 resume_json）；
    上传原件 v1 无 resume_json → 拒绝（前端已置灰控件，这里是双保险）。不生成新版本
    （排版是「这版的松紧」，改它不改内容，原地更新而非堆版本）。返回更新后的文档。
    """
    current = await docs_repo.latest_document()
    if current is None:
        raise ConflictError("还没有简历文档")
    if not current.resume_json.strip():
        raise ConflictError("当前简历是上传原件，不支持排版调整（仅生成的简历可调排版）")
    # 重渲染：同一 resume_json + 新排版配置 → 新 html（内容不变，只改排版）
    new_html = render_resume(resume_from_json(current.resume_json), typography=typography)
    updated = await docs_repo.update_document_typography_and_html(current.id, typography, new_html)
    assert updated is not None  # current 刚读过，必存在
    logger.info("document_typography_updated", version=updated.version, typography=typography.model_dump())
    return updated


async def get_current_original() -> tuple[bytes, str, str] | None:
    """取当前文档的原件文件内容（无原件时返回 None）。

    返回 (bytes, 原文件名, 原文件扩展名)。文件缺失（历史遗留/手动删）时返回 None
    （前端落 Markdown）。扩展名供 Router 决定 Content-Type（原生预览的 MIME）。
    """
    current = await docs_repo.latest_document()
    if current is None or not current.original_name:
        return None
    try:
        data = originals_repo.read_original_file(current.version, current.original_name)
    except FileNotFoundError:
        logger.warning("original_missing", version=current.version, name=current.original_name)
        return None
    return data, current.original_name, current.original_ext


async def get_document_original(document_id: int | None = None) -> tuple[bytes, str, str] | None:
    """取**指定文档**的原件文件内容（回看模式用：展示该稿当时的文档原件）。

    2026-08-12 身份锚定：按 **id** 取（不是 version）——version 是显示标签（软作废后
    复用），id 才是唯一身份；回看某稿传 document_id 精确取，不撞同号作废稿。
    无该文档/该文档无原件（生成版）→ None（前端落 Markdown 预览）。
    省略 document_id = 取当前版本原件（默认路径，与 get_current_original 等价）。
    """
    if document_id is None:
        return await get_current_original()
    if document_id <= 0:
        return None
    doc = await docs_repo.get_document_by_id(document_id)
    if doc is None or not doc.original_name:
        return None
    try:
        data = originals_repo.read_original_file(doc.version, doc.original_name)
    except FileNotFoundError:
        logger.warning("document_original_missing", document_id=document_id, version=doc.version, name=doc.original_name)
        return None
    return data, doc.original_name, doc.original_ext


async def reset_resume(clear_facts: bool) -> dict[str, int]:
    """重置简历：清空文档流 + 快照（回到空态，可重传新 v1）。

    clear_facts=True 时连事实库一起清空（换新简历、不要旧事实时用）。
    待执行建议**无条件清空**——版本都没了，建议锚在旧版上脱锚无意义（§12.3 决策四）。
    返回清理的行数统计。文档任务互斥：清文档流是破坏性写，撞锁 409。
    """
    async with document_task():
        docs = await docs_repo.delete_all_documents()
        snaps = await snaps_repo.delete_all_snapshots()
        facts = await delete_all_facts() if clear_facts else 0
        records = await clear_all_change_records()
        # 2026-08-29：换简历/重传（reset_resume）**不删 session**——重传是「首次上传失败」的
        # 弥补动作，仍在「首次上传」范畴内，之前的聊天要接着聊（推翻 S1-6「重置连会话一起清」）。
        # 换简历后 current_session 仍是旧的（id 最大），新简历的「已上传/确认」事件接着写进它，
        # 新老对话连续、血统不串——用户换简历 ≠ 冷启动，聊天历史保留可回看。
        sessions = 0
        # 原件文件一并清空（reset 后无文档流，残留的原件无意义）
        originals_deleted = originals_repo.delete_all_originals()
        # 抽取状态回 idle：重置 = 回到空态，抽取状态也必须归零——否则确认(confirmed)后
        # 重置再重传，会撞上传路由的 409 封闭通道守卫（"简历已就绪"），抽不出来。
        extract_state.reset_state()
        logger.info(
            "resume_reset",
            documents=docs,
            snapshots=snaps,
            facts=facts,
            records=records,
            sessions=sessions,
            originals=originals_deleted,
        )
        return {"documents": docs, "snapshots": snaps, "facts": facts, "records": records, "sessions": sessions}


async def reset_all() -> dict[str, int]:
    """核爆：清空一切求职数据回到出厂态（「重新开始」，不推荐的后悔药）。

    与 reset_resume（清文档流、可留 facts）不同——这是**全量清空**：文档流 + 事实 +
    会话 + 建议 + 偏好 + 投递侧（jobs + job_followup_events）全删，且 apply_mode 强制置 off
    （方向 facts 没了，爬虫再开只能空转抓空气）。**用户配置（LLM key/模型/语言等
    settings 表 app 行）原样保留**——工具配置是用户的，求职数据才是要清的。

    顺序很关键（方案②协作式取消）：
    1. 先 bump data_epoch（把「世界被重置」信号立起来）+ apply_mode=off + 删 facts——
       在途爬虫（Electron 读 DOM / 猎聘 HTTP）下次循环检查点发现 epoch 变了，
       丢弃在途批次、不回写、自我了断。
    2. 再删 jobs + job_followup_events + 其余所有表（先立信号后删数据，顺序反了会被回写）。
    3. 内存态（提案/冲突/抽取）归零。

    文档任务互斥：清文档流是破坏性写，撞锁 409。返回清理行数统计。
    """
    async with document_task():
        # 1. 立信号 + 关停爬虫 + 删 facts（先于删岗位，给在途爬虫「世界没了」的信号）。
        # bump 包进 epoch_write_guard：与 jobs 的「比对+写」共用一把锁——bump 一旦持锁，
        # 任何在途 ingest/refresh 排到它之后，看到新 epoch 即丢弃；反之 ingest 先持锁写
        # 完提交，bump 后到，清空会连那批已提交岗位一起删。两向都无孤儿。
        async with epoch_write_guard():
            epoch = await bump_data_epoch()
        await settings_service.update_settings(AppSettingsPatch(apply_mode=False))
        facts = await delete_all_facts()

        # 2. 删全部业务数据（先反馈后岗位，免留孤儿行）
        docs = await docs_repo.delete_all_documents()
        snaps = await snaps_repo.delete_all_snapshots()
        records = await clear_all_change_records()
        preferences = await clear_all_preferences()
        followup_events = await clear_all_followup_events()
        interviews = await clear_all_interviews()
        analysis_reports = await clear_all_reports()
        jobs = await clear_all_jobs()
        crawl_state = await clear_all_crawl_state()
        sessions = await sessions_repo.delete_all_sessions()
        originals_deleted = originals_repo.delete_all_originals()

        # 3. 内存态归零
        extract_state.reset_state()

        logger.info(
            "data_reset_all",
            epoch=epoch,
            documents=docs,
            snapshots=snaps,
            facts=facts,
            records=records,
            preferences=preferences,
            followup_events=followup_events,
            interviews=interviews,
            analysis_reports=analysis_reports,
            jobs=jobs,
            crawl_state=crawl_state,
            sessions=sessions,
            originals=originals_deleted,
        )
        return {
            "documents": docs,
            "snapshots": snaps,
            "facts": facts,
            "records": records,
            "preferences": preferences,
            "followup_events": followup_events,
            "interviews": interviews,
            "jobs": jobs,
            "crawl_state": crawl_state,
            "sessions": sessions,
        }


async def create_version_snapshot(document_id: int) -> None:
    """给某版打一份工作台快照（该版**开始时**的工作台：资料集 + 改动记录）。

    调用点 = **版本创建时刻**：`save_upload`（v1）、`generate_confirm`（落库后、结清前，
    那张快照要含本版之后的确认/结清状态，故不能在 save_document 里打）。
    回滚的目标版快照早已在它创建时打好，回滚时不重打（重打会存成"回滚后的样子"）。
    """
    await snaps_repo.create_snapshot(document_id=document_id)


async def _restore_workspace(snapshot: SnapshotRecord) -> bool:
    """把工作台恢复成快照里的样子（资料集 + 改动记录，**直回不叠加**）。

    语义（2026-09-24）：回滚到 vN = 恢复 vN 开始时的工作台——不是「撤销 vN 之后的变化」，
    而是直接换成快照那一份（facts 全标 superseded 后按快照重建；records 整表清掉后按快照重建）。
    旧快照（`records` 为 None，升级前打的）**降级**为只还原事实、不动改动记录——不能拿
    None 去清空当前记录。返回是否真的做了恢复。
    """
    await _restore_facts(snapshot)
    if snapshot.records is None:
        logger.info("workspace_restore_legacy_snapshot", document_id=snapshot.document_id)
        return True
    await snaps_repo.restore_records(snapshot.records)
    return True


async def _render_discarded_work(snapshot: SnapshotRecord | None) -> str:
    """渲染「这次回滚真正会放弃的工作」（恢复**之前**算，恢复后就查不到了）。

    **只报会消失的**：当前活跃、但目标版快照里没有的改动记录——它们是在目标版之后新攒的，
    回滚后不再出现在面板。目标版快照里**有**的那些不算放弃（它们会被原样恢复回来，
    哪怕此刻状态不同）。快照缺失 / 旧快照（records 为 None）→ 无从对比，返回空串。
    """
    from app.services.optimization import pending_records

    if snapshot is None or snapshot.records is None:
        return ""
    snap_ids = {c.id for c in snapshot.records}
    lost = [r for r in await pending_records() if r.id not in snap_ids]
    if not lost:
        return ""
    lines: list[str] = []
    for r in lost:
        detail = "；".join(f"{c.target or '（未定位）'}（{c.status}）" for c in r.changes) or "（无具体改动点）"
        lines.append(f"- {r.reason}：{detail}")
    return "\n".join(lines)


async def rollback(document_id: int) -> tuple[ResumeDocument, bool]:
    """回滚到目标稿（2026-08-10 软作废回滚；2026-08-12 A3 按 document_id 身份锚定）。

    - 文档：目标稿之后（id 更大）所有稿标 superseded（数据保留、UI 不显示），目标稿变当前。
      按 **id** 取目标 + 圈定范围——version 可复用（同号一作废一当前两条），按 version 会撞错稿。
    - **工作台：整份恢复目标版的开始快照**（2026-09-24 改判）——资料集与改动记录一起回到
      「该版刚建立」那一刻，**直回不叠加**（不是把 vN 之后的变化「撤销」成某中间态，
      而是直接换成快照里的那份）。回滚的语义从"丢弃 vN 之后的改动"变成"回到 vN 刚开始"。
    - **回滚到 v1 被拒**：v1 的快照是空的，恢复等于清空工作台——那是「重置」不是「回滚」，
      让用户去走重置入口（避免一个按钮悄悄抹掉资料集）。
    - 版本变更 = 开新 session（§11.1）。
    - **开场引导挪后台（2026-09-24）**：回滚此前同步 `await persist_opening(...)`，被一次
      LLM 往返（说清「从哪退到哪 + 放弃了哪些变化」）拖住整个响应——用户点确认后要等它
      回来才看到回退，观感是「点了没反应」。现改为：**持锁期间**把新 session 的 id 钉死，
      **锁外** fire-and-forget 生成（对齐确认入库 resume.py 的做法），响应提前返回。
      引导晚到由前端短轮询捞回；回滚**本身**（文档/工作台/session/事件）照旧全程原子，
      没有任何一段可被暂停。
    - 返回 (目标稿文档, 是否做了工作台恢复)。
    - 文档任务互斥（2026-08-14）：回滚写文档流，撞 apply/改写/生成 409。
    """
    # 后台开场引导的「调度时刻」快照：持锁期间填，锁外发起（见函数末）。钉住 session_id
    # 而非锁外再取 current_session()——锁外期间别的动作可能又开了新 session，取到会落错轮。
    opening_session_id: int | None = None
    covered_id = 0
    from_version = 0
    to_version = 0
    discarded = ""
    async with document_task():
        # 回滚目标 = 当前文档时拒绝（回滚到当下无意义）
        current = await docs_repo.latest_document()
        if current is not None and current.id == document_id:
            raise ConflictError(f"已是当前版本 v{current.version}，无需回滚")

        target = await docs_repo.get_document_by_id(document_id)
        if target is not None and target.version == 1:
            raise ConflictError("回滚到最早一版等于清空重建——请用「回到初始版本」重置，而不是回滚")

        # 目标版的开始快照：既用于恢复，也用于算「真正会放弃哪些」（恢复后就查不到了）。
        snapshot = await snaps_repo.get_snapshot(document_id)
        # ⚠️ 放弃清单必须在**恢复之前**算：恢复会按快照重建改动记录，恢复后这批就没了。
        discarded = await _render_discarded_work(snapshot)

        doc = await _rollback_document(document_id)
        # 回滚成功必有目标文档 → 被覆盖稿（回滚前的当前稿）也必存在（否则目标稿就是当前稿，上面已拒绝）
        assert current is not None
        # 工作台恢复：用目标版的开始快照，资料集 + 改动记录整份换回。
        # ⚠️ 旧快照（records_json 为 NULL，升级前打的）在 _restore_workspace 里降级为
        # 「只还原事实、不动改动记录」——不能拿 None 去清空当前记录。
        workspace_restored = await _restore_workspace(snapshot) if snapshot is not None else False
        # 版本变更 = 开新 session（§11.1），绑定目标稿 id
        sess = await open_session(document_id=doc.id)
        opening_session_id = sess.id  # 钉死在本轮 session，锁外发引导用（不落错轮）
        # 事件落库到**新 session**：回滚是干净轮回，事件是新一轮的第一条（§11.3）。
        # 从哪退到哪要写清（用户诉求：回滚后要能看出动过什么）。
        await record_current_event(
            f"已从第 {current.version} 稿回滚到第 {doc.version} 稿（第 {doc.version} 稿之后的各稿已作废，下一稿从第 {doc.version + 1} 稿继续）",
            kind="rolled_back",
            ref_document_id=current.id,
        )
        # 分阶段引导（§11.8）：仅**收集**生成所需参数——真正的生成/落库在**锁外**发起
        # （见函数末 schedule_opening）。from_version/to_version 供 prompt 说清「从哪退到哪」；
        # discarded = 刚被放弃的工作（恢复前就捞好，恢复后查不到）；covered_document_id
        # 保留被覆盖稿的溯源线索。
        covered_id = current.id
        from_version = current.version
        to_version = doc.version
        logger.info("resume_rolled_back", to_document_id=document_id, current_version=doc.version, workspace_restored=workspace_restored)
    # 锁已释放：回滚本身（文档/工作台/session/事件）全部落定，开场引导才在后台起跑。
    # session_id=None（真无 session，理论上不会）→ 不调度，只记日志。
    if opening_session_id is None:
        logger.warning("rollback_opening_no_session", document_id=document_id)
    else:
        schedule_opening(
            "rolled_back",
            covered_document_id=covered_id,
            session_id=opening_session_id,
            from_version=from_version,
            to_version=to_version,
            discarded=discarded,
        )
    return doc, workspace_restored


async def _rollback_document(document_id: int) -> ResumeDocument:
    """软作废回滚：目标稿之后全标 superseded，目标稿变当前（按 document_id）。

    数据不删（session/快照/原件/建议全保留）；原件文件无需复制（回滚不产生新文档，
    目标稿原件路径不变）。目标稿不存在 → NotFoundError。
    """
    doc = await docs_repo.soft_rollback(document_id)
    if doc is None:
        raise NotFoundError(f"文档 #{document_id} 不存在")
    return doc


async def _restore_facts(snapshot: SnapshotRecord) -> None:
    """用快照覆盖当前事实库：当前 active 全标 superseded，快照事实重建为 active。

    2026-08-07 重构：嵌套模型——快照事实 = title + points 一条内，直接整批重建，
    无需父先子后/id 映射/层级外键（旧的 group_id id_map 机制已删）。
    来源原样保留（_fact_source 收口），溯源不断。
    """
    # 1. 当前 active 全标 superseded（留历史，不删）
    for fact in await list_facts(status="active"):
        await update_fact(fact.id, FactUpdate(status="superseded"))
    # 2. 快照事实重建为 active：title + points 一行一条（嵌套在一条内，无需层级重建）
    await create_facts(
        [
            FactCreate(
                category=f.category,
                title=f.title,
                points=f.points,
                occurred_at=f.occurred_at,
                source=_fact_source(f.source),
                on_resume=f.on_resume,
            )
            for f in snapshot.facts
        ]
    )
