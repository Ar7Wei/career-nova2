"""facts 路由：仅做 HTTP 收发，业务交给 facts service（红线：Router 不写业务）。

错误由 service 层抛 AppError、全局 handler 统一收口。
"""

from fastapi import APIRouter, Query

from app.core.errors import NotFoundError
from app.schemas.facts import (
    Fact,
    FactCategory,
    FactsConfirmRequest,
    FactsConfirmResponse,
    FactsListResponse,
    FactStatus,
    FactUpdate,
)
from app.services.facts import confirm_facts, get_facts, modify_fact, remove_fact

router = APIRouter()


@router.post("/facts/confirm", response_model=FactsConfirmResponse)
async def confirm(request: FactsConfirmRequest) -> FactsConfirmResponse:
    """确认入库：批量写入编辑后的事实清单。

    信息库页手填/编辑是明确意图 → auto_adjudicate=True（冲突自动 supersede 旧条，
    用户正在界面上亲手编辑，不存在"无感知顶掉"）。
    """
    result = await confirm_facts(request.facts, auto_adjudicate=True)
    return FactsConfirmResponse(saved=result.saved)


@router.get("/facts", response_model=FactsListResponse)
async def read_facts(
    category: FactCategory | None = Query(default=None),
    status: FactStatus | None = Query(default="active"),
) -> FactsListResponse:
    """读事实列表（信息库页按分类/状态过滤，默认 active）。"""
    facts = await get_facts(category=category, status=status)
    return FactsListResponse(facts=facts)


@router.patch("/facts/{fact_id}", response_model=Fact)
async def patch_fact(fact_id: int, patch: FactUpdate) -> Fact:
    """部分更新一条事实（信息库页编辑；标 superseded 留 history 也走这里）。"""
    updated = await modify_fact(fact_id, patch)
    if updated is None:
        raise NotFoundError("事实不存在")
    return updated


@router.delete("/facts/{fact_id}", status_code=204)
async def delete_fact(fact_id: int) -> None:
    """删除一条事实。"""
    removed = await remove_fact(fact_id)
    if not removed:
        raise NotFoundError("事实不存在")
