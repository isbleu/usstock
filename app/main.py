"""FastAPI 入口：API 路由 + 静态文件托管。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.services import events
from app.services import export as export_service
from app.services import quotes as quotes_service
from app.services.boards import get_board

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="美股概念板块实时行情看板")


_METRICS = ("regular", "current")


def _metric(m: str) -> str:
    """口径参数白名单：regular（盘中，默认）/ current（当前时段）。"""
    return m if m in _METRICS else "regular"


@app.get("/api/summary")
async def api_summary(metric: str = "regular") -> dict:
    # yfinance 为同步阻塞调用，放到线程池避免卡住事件循环
    return await run_in_threadpool(quotes_service.summary, _metric(metric))


@app.get("/api/boards/{board_id}")
async def api_board(board_id: str) -> dict:
    board = get_board(board_id)
    if board is None:
        raise HTTPException(status_code=404, detail="板块不存在")
    return await run_in_threadpool(quotes_service.board_detail, board)


@app.get("/api/all")
async def api_all(metric: str = "regular") -> dict:
    """全部板块完整明细（前端大字版一次拉取）。"""
    return await run_in_threadpool(quotes_service.all_details, _metric(metric))


@app.get("/api/export.png")
async def api_export_png(metric: str = "regular", theme: str = "dark") -> Response:
    """导出全板块行情长图（Pillow 服务端渲染，复用行情缓存）。
    theme=dark|light：跟随前端主题设置。"""
    png = await run_in_threadpool(
        export_service.render_png, _metric(metric),
        theme if theme in ("dark", "light") else "dark")
    filename = export_service.export_filename()
    return Response(
        content=png,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/stream-debug")
async def api_stream_debug() -> dict:
    """tick 流健康诊断：连接状态、已订阅数、各 symbol 最新 tick 时间戳。"""
    return await run_in_threadpool(quotes_service.stream_debug)


@app.get("/api/config")
async def api_config() -> dict:
    return {
        "refresh_open_ms": int(os.environ.get("FRONTEND_REFRESH_OPEN", "30000")),
        "refresh_closed_ms": int(os.environ.get("FRONTEND_REFRESH_CLOSED", "300000")),
    }


@app.get("/api/events")
async def api_events(request: Request) -> StreamingResponse:
    """SSE 实时推送：夜盘 tick 到达即通知浏览器刷新（轮询仍作兜底）。

    消息本体只是「有更新」的信号（tick），前端收到后重新拉取
    /api/summary 等接口取齐数据——服务端缓存已被 tick 就地修补，
    重新聚合即可拿到最新值，不会触发额外的数据源请求。
    """
    events.register_loop(asyncio.get_running_loop())
    q = events.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # 心跳，防代理断连
        finally:
            events.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
