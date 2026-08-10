"""SSE（Server-Sent Events）广播注册表。

夜盘 websocket 的 tick 到达后台线程后，通过这里把「有更新」的信号
实时推给浏览器（EventSource），前端收到即刷新，不再干等轮询周期。
线程侧用 call_soon_threadsafe 桥接到 asyncio 事件循环；带节流，
成交密集时也不会对每个 tick 都刷屏。
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# 广播节流：tick 爆发时最多每 1.5s 推一次（前端随后整页刷新一次取齐）
THROTTLE_SEC = 1.5

_loop: asyncio.AbstractEventLoop | None = None
_subs: set[asyncio.Queue] = set()
_last_broadcast = 0.0


def register_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=8)
    _subs.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subs.discard(q)


def broadcast(msg: str = "tick") -> None:
    """供非 asyncio 线程调用：通知所有浏览器连接（节流）。"""
    global _last_broadcast
    now = time.monotonic()
    if now - _last_broadcast < THROTTLE_SEC:
        return
    _last_broadcast = now
    if _loop is None or not _subs:
        return
    for q in list(_subs):
        try:
            _loop.call_soon_threadsafe(q.put_nowait, msg)
        except Exception:
            logger.debug("SSE 广播失败，忽略", exc_info=True)
