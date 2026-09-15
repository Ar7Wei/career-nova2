"""简历识别 service：上传 → 转 Markdown → 存文档 v1（快），后台异步抽事实待确认。

重构后（文档为成品，事实为唯一真相源）：
- 上传关键路径**没有 LLM**：MarkItDown 转 Markdown → 存 resume_documents v1 → 立即返回。
- 抽取退到**后台任务**：对文档 Markdown 单次跑识别图，结果进**抽取状态**
  （候选，不入库）。前端轮询 extract-status 弹「抽取信息卡片」，**确认才入库**
  （source=resume_upload，带统一冲突检测）。不设"直接入库"——抽错靠卡片确认拦一道，
  确认后仍靠对话自然对账。
- 文档是成品/范本，事实库是唯一事实源。抽取把文档"索引进"事实库，不是关键路径。
"""

import asyncio

from collections.abc import Callable
from typing import Any

from app.core.errors import ConflictError, NotFoundError
from app.core.logging import logger
from app.graphs import resume_parse
from app.schemas.documents import ResumeUploadResponse
from app.schemas.facts import (
    ExtractConfirmRequest,
    ExtractConfirmResponse,
    ExtractRejectRequest,
    ExtractRetryResponse,
    ExtractState,
    FactCreate,
)
from app.repositories.documents import latest_document
from app.repositories.facts import list_facts
from app.services import extract_state
from app.services.documents import reset_resume, save_upload
from app.services.facts import FactConfirmResult, confirm_facts
from app.services.opening import schedule_opening
from app.services.sessions import record_current_event
from app.utils.extract import extract_markdown

# 后台任务调度器：Router 注入 FastAPI 的 BackgroundTasks.add_task（service 不 import
# fastapi，调度机制由调用方给、策略留在这里）。签名 (func, *args, **kwargs) -> None。
BackgroundScheduler = Callable[..., Any]

# S3-2（2026-08-13）：上传互斥锁——双击/重试的真并发不能让两份上传都过守卫叠进文档流。
# 单用户本地应用，进程内一把 asyncio.Lock 即可（非阻塞 trylock：拿不到立刻 409，不排队）。
_upload_lock = asyncio.Lock()


async def upload_resume(filename: str, data: bytes, original_bytes: bytes | None = None, original_name: str | None = None) -> ResumeUploadResponse:
    """上传简历：转 Markdown → 存文档 v1（含快照）→ 返回。**不抽取**（快路径）。

    可能抛出：UnsupportedFileTypeError（415）/ EmptyExtractionError（422）。
    original_bytes/original_name：上传的原件文件（PDF/HTML 等），落盘供重启后原生预览。
    事件落库：上传完成写「已上传简历」到当前 session（无 session 时开种子）。

    S3-2 并发护栏：同一份简历文档流**一次只能有一份上传在处理**——并发进来时，
    后到者立刻 ConflictError（409），不与先到者争抢（否则两份都过封闭通道守卫叠成两行）。
    """
    if _upload_lock.locked():
        # 瞬时互斥护栏（双击/重试撞锁）：不是「用户的失败」，不落库红条（record_event=False）。
        raise ConflictError("正在处理上一份上传，稍后再试（请勿重复点击上传）", record_event=False)
    async with _upload_lock:
        markdown = extract_markdown(filename, data)
        logger.info("resume_uploaded", filename=filename, markdown_chars=len(markdown))
        response = await save_upload(
            markdown=markdown,
            resume_name=filename,
            original_bytes=original_bytes or data,
            original_name=original_name or filename,
        )
        # 事件落库（§11.3）：上传是版本变更（0→1），事件归进当前 session（无则开种子）。
        # 不带 kind——分阶段引导（§11.8）挂「确认入库」，不在上传（此时 facts 还没抽出来，
        # 缺口报告是空的，没料可引导）。
        await record_current_event(f"已上传简历：{filename}")
        return response


async def extract_facts_async(
    generation: int,
    document_id: int,
    markdown: str,
    cancel_event: asyncio.Event | None = None,
) -> None:
    """后台抽取：对文档 Markdown 抽一遍，结果进抽取状态待确认。

    这是异步后台任务（上传已返回，不阻塞聊天）。**单次抽取**（2026-08-08）：
    有确认兜底（卡片可编辑）+ 「重新抽取」按钮，不需要多抽对比——省 Token。
    generation：本次抽取的代次（作废旧抽取——旧代次结果丢弃，不覆写状态）。
    document_id：本次抽取锚定的文档 id（唯一身份，A3；仅作标签记录）。
    cancel_event：客户端断开/重传/重置时取消（C，2026-08-14 接线——extract_state 管
    当前在途抽取的 event，重传/重抽/重置时 set 它掐断旧抽取的 in-flight LLM）。

    健壮性（2026-08-08 修复）：后台任务必须 try/except 兜底——LLM 抛异常或返回
    空结果时，状态 → failed（可读错误），而不是静默卡 extracting（前端蒙版永久转圈）。

    C（2026-08-14）：CancelledError 是 BaseException 不是 Exception，必须显式捕获——
    否则冒到 Starlette BackgroundTasks 把后台任务搞崩。被 cancel = 代次已作废（begin/
    reset 已代次+1），静默退出即可，不写状态。
    """
    # 没显式传 event 时，从 extract_state 拿当前在途抽取的 event（C 接线）
    ev = cancel_event if cancel_event is not None else extract_state.get_cancel_event()
    try:
        result = await resume_parse.parse(markdown, cancel_event=ev)
        if not result.facts:
            # 空结果：视为失败，给可读提示（不是静默弹空卡片无解释）
            accepted = extract_state.fail_extract(generation, "模型没从这份简历里读出信息，可重试或换一份")
            if accepted:
                logger.info("resume_extract_empty_failed", accepted=True)
                # 系统气泡·错误红条落库（ADR 0017）：后台抽取失败不走 AppError 全局 handler，
                # 这里显式记错误事件进当前 session（留痕）。
                await record_current_event("模型没从这份简历里读出信息，可重试或换一份", kind="error_extract")
            return
        accepted = extract_state.complete_extract(generation, result.facts)
        logger.info("resume_extract_staged", saved=len(result.facts), accepted=accepted)
    except asyncio.CancelledError:
        # 被取消（重传/重抽/重置 set 了 event）：代次已作废，静默退出（不写状态、不穿透）
        logger.info("resume_extract_cancelled", generation=generation)
    except Exception as e:  # noqa: BLE001 - 后台任务必须兜底：LLM 异常 → failed，不静默卡 extracting
        logger.exception("resume_extract_failed", error=str(e))
        accepted = extract_state.fail_extract(generation, f"抽取失败：{e}")
        if accepted:
            logger.info("resume_extract_failed_accepted")
            # 系统气泡·错误红条落库（ADR 0017）：后台抽取失败显式记错误事件。
            await record_current_event(f"抽取失败：{e}", kind="error_extract")


async def confirm_extract(generation: int, facts: list[FactCreate]) -> FactConfirmResult:
    """确认卡片入库：代次校验（防陈旧确认）→ 统一冲突检测 → 入库（source=resume_upload）。

    状态：extracted → confirmed（蒙版消失、通道关闭）。返回 FactConfirmResult
    （saved 写入条数 + conflicts 未裁决的真伪冲突对——甲方案不自动 supersede，
    冲突交聊天 agent 反问用户裁决）。
    代次不匹配（用户对着旧卡片确认）：拒绝写入，抛 ValueError 由 Router 转 409。
    """
    current = extract_state.get_state()
    if current.state != "extracted":
        raise ConflictError("当前没有待确认的抽取结果")
    # generation 校验在 Router 层做（读 extract_state 的当前代次），这里只管状态。
    # 统一冲突检测在 confirm_facts 里（见 services/facts.py，三路径贯穿）。
    # 2026-09-09：上传路径冲突直接覆盖（auto_adjudicate=True）——抽取只在首次上传时
    # 发生（facts 少、冲突概率低），且经过人工确认卡片，人已确认的信息撞旧事实就以
    # 人确认的为准（旧 superseded、新写入），不再走冲突卡片反问（卡片是鸡肋）。
    result = await confirm_facts(facts, auto_adjudicate=True)
    extract_state.mark_confirmed()
    logger.info("resume_extract_confirmed", saved=result.saved, conflicts=len(result.conflicts))
    # 事件落库（§11.3）：确认结果归进当前 session。带 kind="upload"（§11.8 分阶段引导）——
    # 引导挂在「确认入库」：此时 facts 已入资料集，缺口报告才有料可判，agent 才知道
    # 往哪个方向引导用户补简历。
    sid = await record_current_event(f"已确认，{result.saved} 条信息收录进资料集", kind="upload")
    # 确认入库 = 分阶段引导的触发点（§11.8）：facts 齐了，缺口报告才有料。
    # 2026-08-29：开场回退为 opening.py 的轻量一次性 LLM（先聊求职方向，聊透才出优化范围），
    # 不再走聊天 agent 工具循环（那会慢一个数量级 + 让 agent 先抓优化点，方向没聊就抓点）。
    # 后台 fire-and-forget——引导的一次 LLM 往返不该拖住「确认」动作（用户反馈确认后等太久）。
    # session 钉定在确认事件的 session（引导落同一轮），生成后由前端短轮询带回来。
    schedule_opening("uploaded", session_id=sid)
    return result


async def reject_extract() -> None:
    """拒绝卡片：放弃这份简历——清空文档流 + 快照，抽取状态回 idle、通道重开。

    design/resume.md §6：拒绝 = 该批不入库、通道重开（可重传）。
    拒绝的是「这份简历」——连文档 v1 一起作废，才能干净地重传新简历
    （否则文档流还在，重传会撞 409 封闭通道守卫）。
    事实：候选本就未入库（确认才入库），无需清理；已入库的旧事实保留。
    """
    extract_state.reject()
    await reset_resume(clear_facts=False)
    logger.info("resume_extract_rejected")
    # 事件落库（§11.3）：拒绝结果归进当前 session
    await record_current_event("已拒绝这批信息，可以重新上传简历")


async def reconcile_extract_state() -> None:
    """启动对账：把内存抽取状态与 DB 对齐（2026-08-21 bug）。

    抽取状态是**内存态**，重启归零。但文档 v1 在 DB 里是持久的——「上传后抽取失败
    或未确认就关程序」会在重启后留下「文档 v1 在、facts 空、内存 idle」的组合。
    前端 `loadCurrent` 把「idle + 有文档」默认为 confirmed（ready、通道关闭）——
    于是能预览、却没有任何事实，也不能重新上传/重新抽取。

    对账规则（只补启动缺口，不覆盖会话内状态）：
    - 非 idle 态（extracting/extracted/failed/confirmed 是会话内活跃态）→ 不动，权威。
    - idle + 无文档 → 干净空态，保持 idle。
    - idle + 有文档 + 有 active facts → 正常确认过的简历 → confirmed（ready）。
    - idle + 有文档 + 无 active facts → 恢复 failed（可读提示，重抽/重传入口回归）。
    """
    if extract_state.get_state().state != "idle":
        return  # 会话内活跃态（extracting/extracted/…）是当前真相，不覆盖
    doc = await latest_document()
    if doc is None:
        return  # 无文档：干净空态，保持 idle
    active_facts = await list_facts(status="active")
    if active_facts:
        extract_state.mark_confirmed()
        logger.info("extract_state_reconciled_confirmed", document_id=doc.id, facts=len(active_facts))
        return
    # 有文档但一个事实都没有：上次抽取没成功确认（失败/没点确认就关了）→ failed 恢复可恢复入口
    extract_state.fail_extract(
        extract_state.current_generation(),
        "上次的简历信息没有抽取成功，可以重新抽取，或重新上传一份。",
    )
    logger.info("extract_state_reconciled_failed", document_id=doc.id)


async def retry_extract(doc_id: int) -> int:
    """重新抽取：代次 +1 作废旧抽取（旧后台任务结果丢弃），返回新代次。

    doc_id 由编排函数（parse_resume / retry_resume）取当前文档后传入。本函数不再自行
    `latest_document()`——否则与调用方各读一次，两次读之间文档可能被换掉，致代次绑 A、
    内容抽 B。
    """
    generation, _ = extract_state.begin_extract(document_id=doc_id)
    logger.info("resume_extract_retry_scheduled", document_id=doc_id, generation=generation)
    return generation


# ---------------------------------------------------------------------------
# 编排入口（Router 调用）：封闭通道状态机 + 代次校验 + 后台调度都收在这一层
# ——Router 只做 HTTP 收发、调一个 service 函数，不碰 extract_state / Repository。
# ---------------------------------------------------------------------------


def _guard_upload_channel(has_doc: bool) -> None:
    """封闭通道（S3-1，2026-08-13 收紧）：非 idle 态 + 已有文档流 → 拒绝直接再传。

    extracting/extracted/failed 都意味着「上一份上传的卡片还悬着」，直接叠传会生成 v2、
    把未确认的 v1 卡片晾在一边。重传必须先显式 reset（清空文档流）让状态回 idle。
    confirmed = 已就绪，换简历同样要先重置。idle 且无文档 = 干净首次/重置后上传，放行。
    """
    st = extract_state.get_state()
    if st.state == "confirmed":
        raise ConflictError("简历已就绪，换简历请先重置")
    if st.state != "idle" and has_doc:
        raise ConflictError("上一份简历还在处理中（卡片未确认/拒绝），请先重置或处理完再传")


async def parse_resume(
    filename: str,
    data: bytes,
    schedule: BackgroundScheduler,
    *,
    original_name: str | None = None,
) -> ResumeUploadResponse:
    """上传编排：封闭通道守卫 → 转文档 v1 → 后台调度抽取。

    Router 只传文件与调度器（BackgroundTasks.add_task）；「什么时候能传、抽取代次怎么记、
    后台任务绑哪个 cancel_event」都是本层策略。上传原件与 target 名一致（original_name
    缺省用 filename）。
    """
    _guard_upload_channel(await latest_document() is not None)
    logger.info("resume_upload_received", filename=filename, bytes=len(data))
    response = await upload_resume(filename, data, original_bytes=data, original_name=original_name or filename)
    generation, _ = extract_state.begin_extract(document_id=response.document_id)
    # C（2026-08-14）：显式把本次抽取的 cancel_event 绑进任务——不读运行时当前值
    # （否则重传后旧任务启动时读到新 event，取消会串）。
    schedule(extract_facts_async, generation, response.document_id, response.markdown, extract_state.get_cancel_event())
    logger.info("resume_extract_scheduled", document_id=response.document_id, version=response.version)
    return response


async def confirm_extract_requested(req: ExtractConfirmRequest) -> ExtractConfirmResponse:
    """确认端点编排：代次校验（防陈旧确认）→ 走 confirm_extract 入库。"""
    if req.generation != extract_state.current_generation():
        raise ConflictError("抽取已过期，请重新上传")
    result = await confirm_extract(req.generation, req.facts)
    return ExtractConfirmResponse(saved=result.saved, conflicts=result.conflicts)


async def reject_extract_requested(req: ExtractRejectRequest) -> None:
    """拒绝端点编排：代次校验 → 走 reject_extract。"""
    if req.generation != extract_state.current_generation():
        raise ConflictError("抽取已过期，请重新上传")
    await reject_extract()


async def retry_resume(schedule: BackgroundScheduler) -> ExtractRetryResponse:
    """重新抽取编排：取当前文档 → 代次 +1 → 后台重抽（绑本次 cancel_event）。"""
    doc = await latest_document()
    if doc is None:
        raise NotFoundError("还没有简历文档")
    generation = await retry_extract(doc.id)
    schedule(extract_facts_async, generation, doc.id, doc.markdown, extract_state.get_cancel_event())
    logger.info("resume_extract_retry_started", document_id=doc.id, version=doc.version, generation=generation)
    return ExtractRetryResponse(generation=generation, document_id=doc.id)


async def extract_status() -> ExtractState:
    """读后台抽取状态（编排入口——Router 不直接 import extract_state）。"""
    return extract_state.get_state()
