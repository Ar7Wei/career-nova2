"""system 路由：进程级控制（目前只有优雅关闭）。

`POST /system/shutdown` 给 Electron 壳用：关后端前先调它，让后端跑完整 lifespan 关闭
（折叠 WAL + 释放连接），而不是被 `taskkill /F` 直接砍掉留个没折叠的 WAL。

实现：延迟 `signal.raise_signal(SIGINT)`。uvicorn 装了 SIGINT handler，收到后触发
完整 graceful shutdown（lifespan 的关闭段会跑）然后正常退出。用 raise_signal 而非跨进程
发信号，是因为在本进程内调 C 的 `raise()` 绕开了 Windows 跨进程信号投递那块 notoriously
不可靠的地带（uv 还是中间进程，信号投递要穿过它）。

延迟是必须的：`raise_signal` 在请求处理 goroutine 里同步触发关停会打断响应本身，先返回
再关停，Electron 才能收到 200。超时兜底在 Electron 侧（关不掉就 taskkill）。
"""

import asyncio
import signal

from fastapi import APIRouter

from app.core.logging import logger

router = APIRouter()

# 收到请求后多久触发关停：够本响应写回 Electron。
_SHUTDOWN_DELAY_SECONDS = 0.2


def _raise_sigint() -> None:
    """在当前进程内触发 SIGINT，交给 uvicorn 的 handler 走完整 graceful shutdown。"""
    signal.raise_signal(signal.SIGINT)


@router.post("/system/shutdown")
async def shutdown() -> dict:
    """请求后端优雅关闭。立即返回，关停由一个稍后的定时器触发。"""
    logger.info("system_shutdown_requested")
    loop = asyncio.get_running_loop()
    loop.call_later(_SHUTDOWN_DELAY_SECONDS, _raise_sigint)
    return {"ok": True}
