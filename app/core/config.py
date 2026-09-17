"""应用配置管理。

本地单用户桌面应用：单一 .env 配置，无多环境、无云端、无认证。
用 Pydantic Settings 做类型安全配置。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置。默认值即可本地运行，敏感项经 .env 覆盖。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    PROJECT_NAME: str = "Career Nova"
    VERSION: str = "0.1.1"
    DESCRIPTION: str = "Career Nova v2 本地后端"
    API_V1_STR: str = "/api/v1"
    DEBUG: bool = True
    ALLOWED_ORIGINS: list[str] = ["*"]

    # LLM（OpenAI 兼容端点，可指向 deepseek 等）
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str | None = None
    # 默认模型名为空：本地不存"出厂默认模型"，模型名只来自用户配置/探测接口拉取
    #（2026-08-12）。未配置时业务调用会因 model 为空而报错——那是未配置的正当信号。
    DEFAULT_LLM_MODEL: str = ""
    DEFAULT_LLM_TEMPERATURE: float = 0.2
    MAX_TOKENS: int = 2000
    MAX_LLM_CALL_RETRIES: int = 3
    # 总超时：结构化识别等任务（含 json_schema→json_mode 降级重试）可能较慢，
    # 慢推理模型（如 deepseek-v4-pro）单次生成可超 50s。300s 留足余量，
    # 避免识别任务被误杀。这是代码默认值（打包后无 .env 也生效）。
    LLM_TOTAL_TIMEOUT: int = 300

    # 后台抽取：对文档 Markdown 单次抽取（多抽对比取最优的 EXTRACT_PASSES 配置已删——
    # 2026-08-13 死配置清理，graph/service 全程单次调 extract_facts_node，见 D1）。

    # 日志
    LOG_DIR: Path = Path("logs")
    LOG_LEVEL: str = "DEBUG"
    LOG_FORMAT: str = "console"
    # 日志保留天数：启动时清理早于该天数的 {YYYY-MM-DD}.jsonl。Electron 侧经 env 注入
    # 覆盖（log_retention_days 归 Electron settings.json 管）；纯 uvicorn 跑用此默认兜底。
    LOG_RETENTION_DAYS: int = 30

    # 数据库（SQLite）
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/career_nova.db"

    # 界面/LLM 输出语言（runtime 事实源，由 settings 表驱动；默认中文）
    APP_LANGUAGE: str = "zh"


settings = Settings()

# N1（2026-08-13，方向乙）：删除 .env 的 OPENAI_API_KEY 兜底。
# 部署形态 = 打包 exe 用户本地跑、无 .env，env 兜底是死设计。API key 唯一来源 =
# 设置面板 → SQLite 设置表（只写不回读）。.env 仅作本地开发覆盖（见 .env.example），
# 不再是 key 的兜底真相源。
