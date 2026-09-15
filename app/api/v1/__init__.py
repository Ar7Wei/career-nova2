"""API v1 路由聚合。"""

from fastapi import APIRouter

from app.api.v1.analysis import router as analysis_router
from app.api.v1.chat import router as chat_router
from app.api.v1.direction import router as direction_router
from app.api.v1.documents import router as documents_router
from app.api.v1.facts import router as facts_router
from app.api.v1.generation import router as generation_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.optimization import router as optimization_router
from app.api.v1.resume import router as resume_router
from app.api.v1.sessions import router as sessions_router
from app.api.v1.settings import router as settings_router
from app.api.v1.system import router as system_router

api_router = APIRouter()
api_router.include_router(analysis_router, tags=["Analysis"])
api_router.include_router(chat_router, tags=["Chat"])
api_router.include_router(sessions_router, tags=["Sessions"])
api_router.include_router(documents_router, tags=["Documents"])
api_router.include_router(facts_router, tags=["Facts"])
api_router.include_router(jobs_router, tags=["Jobs"])
api_router.include_router(direction_router, tags=["Direction"])
api_router.include_router(optimization_router, tags=["Optimization"])
api_router.include_router(generation_router, tags=["Generation"])
api_router.include_router(resume_router, tags=["Resume"])
api_router.include_router(settings_router, tags=["Settings"])
api_router.include_router(system_router, tags=["System"])

__all__ = ["api_router"]
