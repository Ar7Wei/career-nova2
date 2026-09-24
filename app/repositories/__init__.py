"""Repository 层：唯一访问 SQLite 的地方。"""

from app.repositories.base import async_session_maker, dispose_engine, engine, health_check, init_db
from app.repositories.facts import create_facts, delete_fact, list_facts, update_fact
from app.repositories.settings import (
    bump_resume_epoch,
    get_data_epoch,
    get_degraded_providers,
    get_resume_epoch,
    get_settings_json,
    save_degraded_providers,
    save_settings_json,
)

__all__ = [
    "async_session_maker",
    "bump_resume_epoch",
    "create_facts",
    "delete_fact",
    "dispose_engine",
    "engine",
    "get_data_epoch",
    "get_degraded_providers",
    "get_resume_epoch",
    "get_settings_json",
    "health_check",
    "init_db",
    "list_facts",
    "save_degraded_providers",
    "save_settings_json",
    "update_fact",
]
