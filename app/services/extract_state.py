"""后台抽取的内存态：驱动前端蒙版/卡片/上传通道开关。

单用户本地应用，状态存内存即可（重启后前端挂载恢复当前文档，状态重来）。
design/resume.md §6「抽取信息卡片」：
- 抽取结果进状态（候选，不入库），前端轮询 extract-status 拿到后弹卡片。
- 确认才入库（confirm 端点），状态 → confirmed。
- 拒绝则状态 → idle，通道重开。
- generation 代次：每次上传递增，旧代次的抽取结果**丢弃、不覆写状态**
  （解析窗口内重传时，旧抽取作废——见 design/resume.md §10）。

C（2026-08-14）接线 cancel_event：本模块管理当前在途抽取的取消信号。begin_extract/
reset_state/reject 时 set 旧 event 作废旧抽取（重传/重抽/重置时旧后台 LLM 该被掐断，
不再空烧 token）。单用户 + 抽取互斥锁（services/resume.py _upload_lock）保证同一时刻
一个在途抽取 → 一个 event 引用即可。

分层红线：本模块只碰内存态，不知道 LLM / DB 存在。
"""

import asyncio

from app.schemas.facts import ExtractedFact, ExtractState

# 状态：idle/extracting/extracted/failed/confirmed（见 ExtractState 文档）
_state: ExtractState = ExtractState()
# 代次：每次上传/重置递增；后台任务带代次，旧代次结果丢弃
_generation: int = 0
# 当前在途抽取的取消信号（C 接线）：begin_extract 新建，reset/reject/再 begin 时 set 旧
# event 作废旧抽取。None = 无在途抽取。
_cancel_event: asyncio.Event | None = None


def _cancel_previous() -> None:
    """作废上一个在途抽取：set 旧 event（掐断旧后台 LLM），清引用。"""
    global _cancel_event
    if _cancel_event is not None:
        _cancel_event.set()
        _cancel_event = None


def begin_extract(document_id: int) -> tuple[int, ExtractState]:
    """开始一次抽取：代次 +1，状态置 extracting。返回 (代次, 当前状态)。

    上传解析成功、进入后台抽取时调用。旧代次任务拿到的是旧 _generation，
    完成后比对发现落后即丢弃结果。document_id = 本次抽取锚定的文档（唯一身份，A3）。
    C：先作废旧抽取（set 旧 event），再为本次新建一个 event。
    """
    global _generation, _cancel_event
    _cancel_previous()
    _generation += 1
    _state.state = "extracting"
    _state.document_id = document_id
    _state.facts = []
    _state.error = None
    _cancel_event = asyncio.Event()
    return _generation, _state


def complete_extract(generation: int, facts: list[ExtractedFact]) -> bool:
    """抽取完成：若代次最新则置 extracted 并存候选。返回是否被采纳（旧代次丢弃）。"""
    if generation != _generation:
        return False  # 旧代次：作废，不覆写状态
    _state.state = "extracted"
    _state.facts = facts
    _state.error = None
    return True


def fail_extract(generation: int, error: str) -> bool:
    """抽取失败：若代次最新则置 failed 并记错误。返回是否被采纳。"""
    if generation != _generation:
        return False
    _state.state = "failed"
    _state.facts = []
    _state.error = error
    return True


def mark_confirmed() -> None:
    """确认入库完成：状态 → confirmed（蒙版消失、通道关闭、ready）。"""
    _state.state = "confirmed"
    _state.facts = []
    _state.error = None


def reject() -> None:
    """拒绝该批：状态回 idle（通道重开、可重传）。代次已过，无需递增。

    C：set 旧 event 作废旧抽取。
    """
    _cancel_previous()
    _state.state = "idle"
    _state.document_id = None
    _state.facts = []
    _state.error = None


def reset_state() -> None:
    """重置：回到 idle（重传 / 拒绝卡片 / 显式重置后）。代次递增作废旧抽取。

    C：set 旧 event 作废旧抽取。
    """
    global _generation
    _cancel_previous()
    _generation += 1
    _state.state = "idle"
    _state.document_id = None
    _state.facts = []
    _state.error = None


def get_cancel_event() -> asyncio.Event | None:
    """读当前在途抽取的取消信号（C 接线：extract_facts_async 透传给 parse）。"""
    return _cancel_event


def get_state() -> ExtractState:
    """读当前状态的传输副本（供前端轮询 extract-status）。"""
    state = _state.model_copy(deep=True)
    state.generation = _generation
    return state


def current_generation() -> int:
    """读当前代次（供 confirm/reject 校验——防陈旧确认）。"""
    return _generation
