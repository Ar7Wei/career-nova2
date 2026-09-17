"""PyInstaller 冻结态入口。

开发态后端走 `uv run uvicorn app.main:app`（后端一律用 uv 管依赖与运行）；
打包分发时后端是 PyInstaller 冻结的 onedir exe，由本脚本在进程内 `uvicorn.run()` 起服务——
冻结 uvicorn 命令行本身在多进程/重载场景不可靠，故直接以应用对象启动。

用法（Electron process-manager exe 模式 spawn）：
    backend.exe --host 127.0.0.1 --port 8765
"""

import argparse
import multiprocessing

import uvicorn

from app.core.logging import logger


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Career Nova 冻结后端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main() -> None:
    """冻结后端入口：解析 host/port，以 uvicorn 在进程内拉起 FastAPI 应用。"""
    args = _parse_args()
    logger.info("frozen_backend_starting", host=args.host, port=args.port)
    # 冻结态无 reload/workers（单用户本地），直接以应用对象起。
    # 延迟 import app.main 放到这里，让 argparse 先解析、日志先就绪。
    from app.main import app

    uvicorn.run(app, host=args.host, port=args.port, log_config=None)


if __name__ == "__main__":
    # PyInstaller 冻结 + Windows spawn 语义下，uvicorn 若派生子进程需此保护；
    # 单进程下是 no-op，但留着防未来开 workers。
    multiprocessing.freeze_support()
    main()
