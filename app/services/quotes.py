"""行情拉取 + TTL 缓存 + 板块聚合。"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from app.models import Board, Quote
from app.providers.base import QuoteProvider
from app.providers.yahoo import YahooQuoteProvider, session_window
from app.services import events
from app.services.boards import all_supported_symbols, load_boards

logger = logging.getLogger(__name__)

CACHE_TTL = float(os.environ.get("CACHE_TTL", "20"))

_provider: QuoteProvider = YahooQuoteProvider()

# 缓存：{"fetched_at": float, "quotes": dict[str, Quote]}
_cache: Optional[dict] = None
# 防 dogpile：数据源挂起/休眠唤醒时，多个并发请求不会同时打 Yahoo，
# 拿到锁后先复查缓存（等待期间其他线程可能已拉好）
_fetch_lock = threading.Lock()


def get_quotes() -> tuple[dict[str, Quote], float, bool]:
    """返回 (quotes, 数据时间戳, stale)。TTL 内复用缓存；整批失败时返回上次缓存并标记 stale。"""
    global _cache
    now = time.time()
    if _cache and now - _cache["fetched_at"] < CACHE_TTL:
        return _cache["quotes"], _cache["fetched_at"], False
    with _fetch_lock:
        now = time.time()
        if _cache and now - _cache["fetched_at"] < CACHE_TTL:
            return _cache["quotes"], _cache["fetched_at"], False
        try:
            quotes = _provider.fetch_quotes(all_supported_symbols())
            _cache = {"fetched_at": now, "quotes": quotes}
            return quotes, now, False
        except Exception:
            logger.exception("批量拉取行情失败")
            if _cache:
                return _cache["quotes"], _cache["fetched_at"], True
            # 从未成功过：返回空集合并标记 stale，让前端感知
            return {}, now, True


def _on_tick(symbol: str, tick: dict) -> None:
    """tick 实时到达（websocket 线程）：就地修补缓存 + 通知浏览器刷新。

    按当前时段写入对应字段（PRE→pre_*、REGULAR→regular_*、POST/PREPRE→post_*），
    修补后 summary/board_detail/all_details 的聚合都基于同一批 Quote 对象，
    前端被推送后重新拉取即可看到最新值，无需整批重拉数据源。
    """
    window = session_window()
    if window is None or _cache is None:
        return
    state, start_ts, now_ts = window
    if not (start_ts <= tick["ts"] <= now_ts + 300):
        return  # 非当前时段的 tick（如昨日/上个时段残留）不采纳
    q = _cache["quotes"].get(symbol)
    if q is None or not q.ok or symbol.endswith(".HK"):
        return
    if state == "REGULAR":
        # 快照（新浪/v7）也是实时的，tick 必须更新才覆盖，防旧 tick 回退
        if q.regular_time is not None and tick["ts"] <= q.regular_time:
            return
        q.regular_price = tick["price"]
        q.regular_change_percent = tick["change_percent"]
    elif state == "PRE":
        q.pre_price = tick["price"]
        q.pre_change_percent = tick["change_percent"]
    else:  # POST / PREPRE
        q.post_price = tick["price"]
        q.post_change_percent = tick["change_percent"]
    events.broadcast("tick")


_provider.set_tick_handler(_on_tick)


def stream_debug() -> dict:
    """tick 流健康诊断（/api/stream-debug 用）。"""
    stats = _provider.stream_stats()
    tick_ts = stats.pop("tick_ts", {})
    now = time.time()
    return {
        **stats,
        "tick_age_sec": {s: round(now - ts, 1) for s, ts in tick_ts.items()},
    }


def _avg(values: list[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def _market_state(quotes: list[Quote]) -> str:
    """取多数有效报价的市场状态作为整体状态。"""
    states = [q.market_state for q in quotes if q.ok and q.market_state]
    if not states:
        return "UNKNOWN"
    return max(set(states), key=states.count)


def aggregate_board(board: Board, quotes: dict[str, Quote],
                    metric: str = "current") -> dict:
    """单板块聚合：均值、涨/跌/平家数、领涨领跌、三时段各自均值。
    metric=regular：按盘中涨跌幅统计（无 regular 数据的成员跳过不计）；
    metric=current：按当前时段口径（盘前 pre / 盘中 regular / 盘后 post，缺数据回落 regular）。"""

    def _mv(q: Quote) -> Optional[float]:
        return q.regular_change_percent if metric == "regular" \
            else q.current_change_percent

    members: list[Quote] = []
    for s in board.stocks:
        if s.unsupported:
            continue
        q = quotes.get(s.symbol)
        if q and q.ok:
            # boards.json 里手写的显示名优先，其次 Yahoo shortName
            if s.name:
                q.name = s.name
            members.append(q)

    current = [v for q in members if (v := _mv(q)) is not None]

    def period_values(attr: str) -> list[float]:
        return [v for q in members if (v := getattr(q, attr)) is not None]

    up = sum(1 for v in current if v > 0)
    down = sum(1 for v in current if v < 0)
    flat = sum(1 for v in current if v == 0)

    leader = laggard = None
    if current:
        ranked = sorted(
            (q for q in members if _mv(q) is not None),
            key=_mv,
            reverse=True,
        )
        leader = {"symbol": ranked[0].symbol, "name": ranked[0].name,
                  "change_percent": round(_mv(ranked[0]), 4)}
        laggard = {"symbol": ranked[-1].symbol, "name": ranked[-1].name,
                   "change_percent": round(_mv(ranked[-1]), 4)}

    return {
        "id": board.id,
        "name": board.name,
        "count": len(members),
        "unsupported_count": sum(1 for s in board.stocks if s.unsupported),
        "market_state": _market_state(members),
        "current_avg": _avg(current),
        "up": up,
        "down": down,
        "flat": flat,
        "leader": leader,
        "laggard": laggard,
        "avg_pre": _avg(period_values("pre_change_percent")),
        "avg_regular": _avg(period_values("regular_change_percent")),
        "avg_post": _avg(period_values("post_change_percent")),
    }


def summary(metric: str = "regular") -> dict:
    quotes, fetched_at, stale = get_quotes()
    boards = [aggregate_board(b, quotes, metric) for b in load_boards()]
    states = [b["market_state"] for b in boards if b["market_state"] != "UNKNOWN"]
    return {
        "boards": boards,
        "market_state": max(set(states), key=states.count) if states else "UNKNOWN",
        "metric": metric,
        "timestamp": fetched_at,
        "stale": stale,
    }


def all_details(metric: str = "regular") -> dict:
    """全部板块完整明细（大字版一次拉取）：明细 + 聚合字段，按所选口径均值降序。"""
    quotes, fetched_at, stale = get_quotes()
    boards = []
    for b in load_boards():
        agg = aggregate_board(b, quotes, metric)
        detail = board_detail(b, metric)  # 命中同一 TTL 缓存，不会重复拉行情
        boards.append({**detail,
                       "current_avg": agg["current_avg"],
                       "up": agg["up"], "down": agg["down"],
                       "flat": agg["flat"]})
    boards.sort(key=lambda x: (x["current_avg"] is None,
                               -(x["current_avg"] or 0)))
    states = [b["market_state"] for b in boards
              if b["market_state"] != "UNKNOWN"]
    return {
        "boards": boards,
        "market_state": max(set(states), key=states.count) if states
        else "UNKNOWN",
        "metric": metric,
        "timestamp": fetched_at,
        "stale": stale,
    }


def board_detail(board: Board, metric: str = "current") -> dict:
    quotes, fetched_at, stale = get_quotes()
    stocks = []
    for s in board.stocks:
        if s.unsupported:
            stocks.append({"symbol": s.symbol, "name": s.name, "market": s.market,
                           "unsupported": True, "reason": s.reason})
            continue
        q = quotes.get(s.symbol)
        if q and q.ok:
            name = s.name or q.name or s.symbol
            stocks.append({**q.to_dict(), "name": name, "market": s.market,
                           "unsupported": False, "reason": s.reason,
                           "current_change_percent": q.current_change_percent})
        else:
            stocks.append({"symbol": s.symbol, "name": s.name or s.symbol,
                           "market": s.market, "unsupported": False,
                           "reason": s.reason,
                           "ok": False, "error": (q.error if q else "no_data")})
    # 有效数据按所选口径降序，缺该口径数据/无效/不支持排末尾
    key = "regular_change_percent" if metric == "regular" \
        else "current_change_percent"
    stocks.sort(key=lambda x: (
        x.get("unsupported", False) or not x.get("ok", False)
        or x.get(key) is None,
        -(x.get(key) or 0),
    ))
    return {
        "id": board.id,
        "name": board.name,
        "stocks": stocks,
        "market_state": _market_state(
            [q for s in board.stocks if not s.unsupported
             for q in [quotes.get(s.symbol)] if q and q.ok]
        ),
        "timestamp": fetched_at,
        "stale": stale,
    }
