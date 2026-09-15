"""上传原件（PDF/HTML 等）的磁盘存储。唯一碰 originals/ 目录的地方。

背景（2026-08-09）：上传的原件此前只活在当次会话的内存 blob URL，重启后丢失，
左栏预览退化成 Markdown。本模块把原文件持久化到 DB 旁的原件目录，让重启后
仍能原生预览原件。

位置：与 SQLite 同目录的 originals/（data_dir 即 DB 所在目录，Electron 在启动前
注入 DATABASE_URL）。文件名 = `v{version}_{消毒后的文件名}`——版本号隔离每个上传
版本的原件（回滚语义：切版本时原件也切回该版本的原件）。

安全：原文件名可能含路径分隔符/`..`（恶意构造的 filename），必须消毒到只留
basename，防路径穿越逃出 originals/ 目录。
"""

import tempfile
from pathlib import Path

from app.core.config import settings
from app.core.logging import logger

_ORIGINALS_DIRNAME = "originals"


def originals_dir() -> Path:
    """原件目录 = SQLite 文件同级的 originals/。不存在则创建。

    内存库（测试 conftest 覆盖 DATABASE_URL=:memory:）时回退到系统临时目录
    originals_test/——测试写原件**绝不落真实 data/originals/**（否则单测会把
    用户上传的原件覆盖掉，2026-08-10 事故）。临时目录不复用，测完自愈。
    """
    db_path = _sqlite_file_path()
    if db_path.name == "career_nova.db" and settings.DATABASE_URL.endswith(":memory:"):
        directory = Path(tempfile.gettempdir()) / "careernova_originals_test"
    else:
        directory = db_path.parent / _ORIGINALS_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def sanitize_filename(name: str) -> str:
    """消毒原文件名：只保留 basename，去掉路径分隔符和 `..`，空则给兜底名。

    防止上传的 filename 带 `../` 等路径穿越出 originals/ 目录。
    """
    # 取 basename：同时处理 / 与 \（Windows 路径分隔）
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not base or base in {".", ".."}:
        return "resume_original"
    return base


def original_path(version: int, sanitized_name: str) -> Path:
    """某版本原件文件的落盘路径。sanitized_name 必须是消毒后的文件名。"""
    return originals_dir() / f"v{version}_{sanitized_name}"


def save_original(version: int, original_bytes: bytes, sanitized_name: str) -> Path:
    """落盘原件文件，返回路径。"""
    path = original_path(version, sanitized_name)
    path.write_bytes(original_bytes)
    logger.info("original_saved", path=str(path), bytes=len(original_bytes))
    return path


def read_original_file(version: int, sanitized_name: str) -> bytes:
    """读回某版本的原件文件内容。文件不存在时抛 FileNotFoundError。"""
    return original_path(version, sanitized_name).read_bytes()


def delete_original(version: int, sanitized_name: str) -> bool:
    """删除某版本的原件文件（重置/清除用）。文件不存在返回 False。"""
    path = original_path(version, sanitized_name)
    if path.exists():
        path.unlink()
        logger.info("original_deleted", path=str(path))
        return True
    return False


def delete_all_originals() -> int:
    """清空 originals/ 目录（重置简历用），返回删除文件数。目录本身保留。"""
    directory = originals_dir()
    count = 0
    for path in directory.iterdir():
        if path.is_file():
            path.unlink()
            count += 1
    if count:
        logger.info("originals_cleared", count=count)
    return count


def _sqlite_file_path() -> Path:
    """从 DATABASE_URL 取 SQLite 文件路径；内存库（测试）回退当前目录。"""
    prefix = "sqlite+aiosqlite:///"
    if settings.DATABASE_URL.startswith(prefix) and ":memory:" not in settings.DATABASE_URL:
        return Path(settings.DATABASE_URL[len(prefix):])
    return Path("data/career_nova.db")
