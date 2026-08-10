"""Yahoo websocket 全时段行情 tick 流。

streamer.finance.yahoo.com 的 websocket 是成交流水线：任何时段（盘前/
盘中/盘后/夜盘）有成交即推送（2026-08-07 实测盘前+隔夜均实时到达，
change_percent 基准=昨收盘价，与同花顺/Yahoo 网页一致）。夜盘时段它是
唯一数据源——Yahoo v7/v8/v10 HTTP 接口全都不含夜盘字段（postMarket*
冻结在 20:00 ET），Yahoo 网页版的「BOATS Real Time Price」即来自此流。

**一条连接不能订太多 symbol**（2026-08-09 实测：单连接订 146 只时只有
~90 只有推送，NVDA/AXTI/GFS 等活跃票也静默丢失，且丢失集合随连接随机
变化）——因此按 WS_CHUNK 分片到多条连接并行订阅。

后台常驻线程订阅全部成分股代码，维护每只股票最新一条 tick；
读取方按时间戳自行判断是否属于当前交易时段。断线自动重连。
"""
from __future__ import annotations

import logging
import threading
import time

import yfinance as yf

logger = logging.getLogger(__name__)

# 断线重连间隔
RECONNECT_DELAY = 5.0
# 单条 websocket 连接的订阅上限（超出会被 Yahoo 静默丢弃部分 symbol）
WS_CHUNK = 30


class YahooTickStream:
    """Yahoo 行情 websocket 后台订阅，缓存各 symbol 最新 tick。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ticks: dict[str, dict] = {}   # symbol -> {price, change_percent, ts}
        self._symbols: list[str] = []
        self._last_msg = 0.0
        self._connected_at = 0.0
        self._ws_list: list[yf.WebSocket] = []
        self._thread: threading.Thread | None = None
        # tick 到达回调（由服务层注入，用于实时修补缓存 + 推送浏览器）
        self.on_tick = None

    def ensure_running(self, symbols: list[str]) -> None:
        """首次调用启动后台线程；后续有新代码时重连重分片。"""
        with self._lock:
            new = [s for s in symbols if s not in self._symbols]
            if new:
                self._symbols.extend(new)
        if new and self._ws_list:
            # 增量订阅会让某条连接超过 WS_CHUNK 上限，直接重连重新分片
            self.reconnect()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._run, name="yahoo-tick-ws", daemon=True)
            self._thread.start()

    def _on_msg(self, msg: dict) -> None:
        try:
            sym = msg.get("id")
            price = msg.get("price")
            pct = msg.get("change_percent")
            ts = msg.get("time")
            if not sym or price is None or pct is None or not ts:
                return
            self._last_msg = time.time()
            tick = {"price": float(price),
                    "change_percent": float(pct),
                    "ts": int(ts) // 1000}
            with self._lock:
                self._ticks[sym] = tick
            if self.on_tick is not None:
                try:
                    self.on_tick(sym, tick)
                except Exception:
                    logger.debug("on_tick 回调异常，忽略", exc_info=True)
        except Exception:
            pass  # 单条坏消息不影响流

    def _listen(self, ws: yf.WebSocket, tag: str) -> None:
        try:
            ws.listen(self._on_msg)  # 阻塞直到断开
        except Exception:
            logger.warning("tick websocket %s 监听异常", tag, exc_info=True)
        logger.warning("tick websocket %s 连接结束", tag)

    def _run(self) -> None:
        while True:
            listeners: list[threading.Thread] = []
            try:
                with self._lock:
                    symbols = list(self._symbols)
                chunks = [symbols[i:i + WS_CHUNK]
                          for i in range(0, len(symbols), WS_CHUNK)]
                self._ws_list = []
                for n, chunk in enumerate(chunks):
                    ws = yf.WebSocket(verbose=False)
                    ws.subscribe(chunk)
                    self._ws_list.append(ws)
                    t = threading.Thread(
                        target=self._listen, args=(ws, f"#{n + 1}"),
                        name=f"yahoo-tick-ws-{n + 1}", daemon=True)
                    t.start()
                    listeners.append(t)
                self._connected_at = time.time()
                logger.info("Yahoo tick websocket 已连接：%d 条连接订阅 %d 只",
                            len(chunks), len(symbols))
                # 监督：任何一条连接断开就整体重建（保证分片完整）
                while all(t.is_alive() for t in listeners):
                    time.sleep(1)
                logger.warning("部分 tick 连接断开，整体重建")
            except Exception:
                logger.warning("Yahoo tick websocket 异常，%ds 后重连",
                               RECONNECT_DELAY, exc_info=True)
            finally:
                for ws in self._ws_list:  # close 会让各 listener 的 listen 退出
                    try:
                        ws.close()
                    except Exception:
                        pass
                self._ws_list = []
            time.sleep(RECONNECT_DELAY)

    def reconnect(self) -> None:
        """主动断开全部连接（_run 循环会立即重连并重分片订阅全部代码）。

        Yahoo streamer 长连接会静默丢失个别 symbol 的推送（2026-08-09 实测：
        运行两天的连接 COHR/GFS 无夜盘 tick，同环境新连接立刻收到），
        换交易时段时调用本方法强制重订。
        """
        for ws in self._ws_list:
            try:
                ws.close()
            except Exception:
                logger.debug("tick websocket close 异常", exc_info=True)
        if self._ws_list:
            logger.info("tick websocket 主动重连（换时段强制重订）")

    def stats(self) -> dict:
        """流健康状态（/api/stream-debug 用）。"""
        with self._lock:
            symbols = list(self._symbols)
            ticks = dict(self._ticks)
        return {
            "connected": bool(self._ws_list),
            "connections": len(self._ws_list),
            "connected_at": self._connected_at or None,
            "subscribed": len(symbols),
            "symbols_with_tick": len(ticks),
            "last_msg_age_sec": (round(time.time() - self._last_msg, 1)
                                 if self._last_msg else None),
            "tick_ts": {s: t["ts"] for s, t in ticks.items()},
        }

    def snapshot(self) -> dict[str, dict]:
        """当前全部 tick 的拷贝（symbol -> {price, change_percent, ts}）。"""
        with self._lock:
            return dict(self._ticks)
