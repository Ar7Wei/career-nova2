"""应用入口。本地单用户后端：无认证、无限流、无指标/可观测性/云端。

只保留：CORS、请求校验异常处理、/、/health。启动时初始化 SQLite。
"""

from contextlib import asynccontextmanager
from datetime import datetime

from dotenv import load_dotenv
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_router
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import logger
from app.graphs.checkpoint import close_checkpoint_store, init_checkpoint_store
from app.repositories import dispose_engine, health_check, init_db
from app.services import error_events  # noqa: F401 - import 即注入错误事件记录器（ADR 0017 系统气泡·错误红条落库）
from app.services.llm import llm_service
from app.services.resume import reconcile_extract_state
from app.services.settings import get_settings

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动建表 + 加载设置（同步语言/LLM 到 runtime），关释放连接。

    B1（2026-08-13）：init_db/get_settings 失败 = 致命，**直接抛出**让进程退出——
    不吞咽让服务带病启动（起在坏库上，请求时才各处炸）。Electron 侧 startup_failed
    捕获 waitForPort 超时并弹 Startup failed 对话框（后端进程退出 → 端口永不就绪）。
    """
    logger.info("application_startup", project_name=settings.PROJECT_NAME, version=settings.VERSION)
    await init_db()
    await get_settings()  # 加载持久化设置，同步 APP_LANGUAGE / LLM 三件套到 runtime
    await llm_service.load_degraded_providers()  # 读回降级记忆（json_schema 不支持的 base_url）
    await init_checkpoint_store()  # 装配编排 checkpointer（ADR 0012；:memory: 业务库时跳过）
    await reconcile_extract_state()  # 启动对账：内存抽取状态与 DB 对齐（上传后未确认就关闭的恢复）
    yield
    await close_checkpoint_store()
    await dispose_engine()
    logger.info("application_shutdown")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """把请求校验错误格式化成更易读的形式。"""
    logger.error(
        "validation_error",
        client_host=request.client.host if request.client else "unknown",
        path=request.url.path,
        errors=str(exc.errors()),
    )
    formatted = [
        {"field": " -> ".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]} for e in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Validation error", "errors": formatted},
    )


app.include_router(api_router, prefix=settings.API_V1_STR)

register_error_handlers(app)


@app.get("/")
async def root() -> dict:
    """根端点：返回基本 API 信息。"""
    logger.info("root_endpoint_called")
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "healthy",
        "swagger_url": "/docs",
    }


@app.get("/health")
async def health() -> JSONResponse:
    """健康检查：数据库不可达时返回 503。"""
    logger.info("health_check_called")
    db_healthy = await health_check()
    payload = {
        "status": "healthy" if db_healthy else "degraded",
        "version": settings.VERSION,
        "components": {"api": "healthy", "database": "healthy" if db_healthy else "unhealthy"},
        "timestamp": datetime.now().isoformat(),
    }
    code = status.HTTP_200_OK if db_healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=payload, status_code=code)
