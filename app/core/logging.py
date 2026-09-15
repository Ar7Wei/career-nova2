"""结构化日志配置（structlog）。

继承自模板的工程铁律：事件名 lowercase_with_underscores、事件里不用 f-string
（变量走 kwargs）、异常用 logger.exception()。本地单用户：console 输出 + JSONL 文件。
去掉了 asgi-correlation-id 与多环境耦合。
"""

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, override

import structlog

from app.core.config import settings

settings.LOG_DIR.mkdir(parents=True, exist_ok=True)


def _parse_date_file(name: str) -> datetime | None:
    """从日志文件名解析日期（{YYYY-MM-DD}.jsonl），失败返回 None。"""
    try:
        return datetime.strptime(name.removesuffix(".jsonl"), "%Y-%m-%d")
    except ValueError:
        return None


def cleanup_old_log_files(log_dir: Path, retention_days: int) -> list[str]:
    """清理早于 retention_days 的按日期分文件日志（{YYYY-MM-DD}.jsonl）。

    按文件名日期判定（比 mtime 稳，不受 touch/复制影响）；解析失败回退 mtime。
    只删本仓库命名的按日期日志，绝不碰无关文件。返回被删除的文件名。
    纯函数：不引 DB / Service（logging 层不碰 Repository，避免循环 import）。
    """
    if retention_days <= 0 or not log_dir.is_dir():
        return []
    cutoff = datetime.now() - timedelta(days=retention_days)
    removed: list[str] = []
    for path in log_dir.glob("*.jsonl"):
        date = _parse_date_file(path.name)
        if date is None:
            continue
        if date < cutoff:
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                # 后端进程在跑时文件被 open(...,"a") 持有（Windows），删不掉跳过，
                # 下次启动再清；不报错出声。
                continue
    if removed:
        logger.info("log_cleanup_removed", count=len(removed), retention_days=retention_days, files=removed)
    return removed


def get_log_file_path() -> Path:
    """按日期返回 JSONL 日志文件路径。"""
    return settings.LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"


class JsonlFileHandler(logging.Handler):
    """把日志记录写成每日 JSONL 文件。"""

    def __init__(self, file_path: Path):
        """初始化 JSONL 文件 handler。"""
        super().__init__()
        self.file_path = file_path

    @override
    def emit(self, record: logging.LogRecord) -> None:
        try:
            log_entry = {
                "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                "level": record.levelname,
                "message": record.getMessage(),
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
            }
            extra = getattr(record, "extra", None)
            if isinstance(extra, dict):
                log_entry.update(extra)
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        except Exception:
            self.handleError(record)


def _shared_processors() -> list[Any]:
    return [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]


def setup_logging() -> None:
    """配置 structlog：console 友好输出 + JSONL 文件。"""
    log_level = logging.DEBUG if settings.DEBUG else logging.INFO

    # stdout 走 UTF-8：日志全量含中文（简历内容/对话事实/LLM 请求体），Windows 控制台默认 GBK，
    # 直接写会 UnicodeEncodeError。reconfigure 成 UTF-8（写不进控制台的罕见字符用 replace 兜底，
    # 绝不因日志编码崩进程）。VS Code/Windows Terminal 均为 UTF-8，可正常显示中文。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    file_handler = JsonlFileHandler(get_log_file_path())
    file_handler.setLevel(log_level)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    logging.basicConfig(format="%(message)s", level=log_level, handlers=[file_handler, console_handler])

    # SQL 驱动层噪音降噪（2026-08-09）：aiosqlite/sqlite3 的 `executing functools.partial(...)`
    # / `operation ... completed` 每句打 2 行，DEBUG 下与 sql_echo 叠加会把日志写爆。
    # 这些是驱动内部调用细节，排查 SQL 用上面的紧凑 sql_echo 事件即可。
    for name in ("aiosqlite", "sqlite3", "sqlalchemy.engine.Engine", "sqlalchemy.pool"):
        logging.getLogger(name).setLevel(logging.WARNING)
    # httpx/openai 在 DEBUG 级会把发给 LLM 的整段请求体（含中文 prompt/事实）打进 stdout——
    # 既刷爆日志又含敏感内容。降到 INFO，只看事件不看请求体。
    for name in ("httpx", "openai", "httpcore"):
        logging.getLogger(name).setLevel(logging.INFO)

    # colors=False：关掉 ANSI 颜色码。stdout 会被 Electron 转发进终端/日志文件，颜色码在里面
    # 是裸 ESC 序列乱码（[32m[1m…）。终端要看颜色可单独开，这里统一输出纯文本。
    renderer: Any = (
        structlog.dev.ConsoleRenderer(colors=False)
        if settings.LOG_FORMAT == "console"
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[*_shared_processors(), renderer],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


setup_logging()

logger = structlog.get_logger()
logger.info("logging_initialized", log_level="DEBUG" if settings.DEBUG else "INFO", log_format=settings.LOG_FORMAT)
# 启动即清一次过期日志（import 时 LOG_DIR 已 mkdir；logger 就绪后再调，事件可正常记录）。
# retention_days 由 Electron 经 env 注入覆盖，纯 uvicorn 跑用 config 默认 30 天兜底。
cleanup_old_log_files(settings.LOG_DIR, settings.LOG_RETENTION_DAYS)
