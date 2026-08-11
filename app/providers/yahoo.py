"""Yahoo Finance 批量报价 Provider。

复用 yfinance 内部的 YfData 会话（自动处理 cookie/crumb），
直接调用 v7 quote 端点，一次请求拉取全部 symbol。

v7 端点偶尔会缺盘前/盘后字段（Yahoo 侧问题），此时用
v10 quoteSummary 的 price 模块逐只补齐（并发请求）。
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from yfinance.data import YfData

from app.models import Quote
from app.providers.base import QuoteProvider
from app.providers.sina import fetch_sina_quotes
from app.providers.tickstream import YahooTickStream

logger = logging.getLogger(__name__)

QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
SUMMARY_URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
# Yahoo 单次请求 symbol 数上限较宽松，分批以防 URL 过长
CHUNK_SIZE = 50
# 盘前/盘后兜底补数的并发上限
FALLBACK_WORKERS = 6
# 单只兜底请求的重试次数（Yahoo 偶发限流/抖动）
FALLBACK_RETRIES = 2
# 兜底结果独立缓存秒数：避免每次行情刷新都对 Yahoo 打满 139 只的 quoteSummary
FALLBACK_CACHE_TTL = 300.0
# 每次兜底周期最多新请求的只数：quoteSummary 有限流墙（burst 约 50+ 后被拒），
# 切片慢补 + 缓存复用，几分钟内补全，稳态每周期只刷新少量过期项
FALLBACK_SLICE = 15

_ET = ZoneInfo("America/New_York")


def us_market_state() -> str:
    """按美东时钟计算美股当前时段（不信任 Yahoo 的 marketState，实测会卡顿）。

    PRE 04:00–09:30 盘前；REGULAR 09:30–16:00 盘中；POST 16:00–20:00 盘后；
    其余为 PREPRE（夜盘/隔夜，20:00–次日 04:00）。
    周末规则（Blue Ocean 周日晚～周五凌晨交易，周五晚/周六不交易）：
    周五 20:00 起至周日 20:00 为 CLOSED（周五晚显示盘后终值，不冒充夜盘）；
    周日 20:00 起为 PREPRE（当周第一个夜盘）。
    """
    now = datetime.now(_ET)
    wd = now.weekday()  # 周一=0 … 周日=6
    t = now.hour * 60 + now.minute
    if wd == 5:  # 周六全天
        return "CLOSED"
    if wd == 6:  # 周日：20:00 起 Blue Ocean 开市
        return "PREPRE" if t >= 20 * 60 else "CLOSED"
    if wd == 4 and t >= 20 * 60:  # 周五 20:00 后无夜盘
        return "CLOSED"
    if 4 * 60 <= t < 9 * 60 + 30:
        return "PRE"
    if 9 * 60 + 30 <= t < 16 * 60:
        return "REGULAR"
    if 16 * 60 <= t < 20 * 60:
        return "POST"
    return "PREPRE"


def session_window() -> tuple[str, int, int] | None:
    """返回 (当前时段, 时段起点 unix 秒, 当前 unix 秒)；周末 CLOSED 返回 None。

    tick 的时间戳落在该窗口内才视为当前交易时段的有效数据。
    起点：PRE 04:00 / REGULAR 09:30 / POST 16:00 / PREPRE 20:00（凌晨取前一天）。
    """
    state = us_market_state()
    if state == "CLOSED":
        return None
    now_et = datetime.now(_ET)
    start_min = {"PRE": 4 * 60, "REGULAR": 9 * 60 + 30,
                 "POST": 16 * 60, "PREPRE": 20 * 60}[state]
    start = now_et.replace(hour=start_min // 60, minute=start_min % 60,
                           second=0, microsecond=0)
    if start > now_et:  # 凌晨 0–4 点：夜盘时段起点是前一天 20:00
        start -= timedelta(days=1)
    return state, int(start.timestamp()), int(now_et.timestamp())


class YahooQuoteProvider(QuoteProvider):
    def __init__(self) -> None:
        self._data = YfData()
        self._stream = YahooTickStream()
        self._stream_state: str | None = None   # 上一周期时段（用于换时段重订）
        # symbol -> (取数时间戳, price 模块数据)，成功的兜底结果短期复用
        self._prepost_cache: dict[str, tuple[float, dict]] = {}

    def fetch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        quotes: dict[str, Quote] = {}
        for i in range(0, len(symbols), CHUNK_SIZE):
            chunk = symbols[i : i + CHUNK_SIZE]
            try:
                quotes.update(self._fetch_chunk(chunk))
            except Exception:
                # curl (16) 等瞬时 HTTP/2 抖动：2s 后整批重建会话重试一次，
                # 仍失败则抛给上层（quotes.get_quotes 回落到上次缓存 + stale 标记）
                logger.warning("v7 批量请求失败，2s 后重建会话重试一次",
                               exc_info=True)
                time.sleep(2)
                self._data = YfData()
                quotes.update(self._fetch_chunk(chunk))
        # 未返回的 symbol 标记为不可用
        for s in symbols:
            if s not in quotes:
                quotes[s] = Quote(symbol=s, ok=False, error="no_data")
        # 先新浪批量补（一次请求无限流），剩余缺口再走 quoteSummary 切片兜底，
        # 最后叠加 websocket 实时 tick（比任何 HTTP 快照都新）
        self._fill_from_sina(quotes)
        self._fill_missing_prepost(quotes)
        self._fill_from_stream(quotes)
        return quotes

    # ---------- websocket tick 覆盖（全时段；夜盘时段是唯一数据源） ----------

    def set_tick_handler(self, fn) -> None:
        """注入 tick 回调（服务层用来实时修补缓存 + 推送浏览器）。"""
        self._stream.on_tick = fn

    def stream_stats(self) -> dict:
        """tick 流健康状态（/api/stream-debug 用）。"""
        return self._stream.stats()

    def _fill_from_stream(self, quotes: dict[str, Quote]) -> None:
        """用 websocket 最新 tick 覆盖当前时段的价/涨幅字段。

        tick 比 HTTP 快照新（成交即推送），按时段写入对应字段：
        PRE→pre_*、REGULAR→regular_*、POST→post_*、PREPRE→post_*。
        REGULAR 时段额外校验 tick 必须比 v7 快照时间新，防止旧 tick 回退价格。

        夜盘（PREPRE）特殊：无任何 HTTP 快照源（v7/v10 盘后字段 20:00 后冻结）。
        本时段有 tick 的票 post 字段=实时夜盘价（post_session="night"，标注「夜盘」）；
        无 tick 的票回落 v7 快照里的盘后终值（post_session="post"，标注「盘后」，
        不冒充夜盘）。其他时段无 tick 则保留快照值（有轮询校正）。
        涨幅基准=盘中收盘价，与同花顺/Yahoo 网页一致。
        """
        window = session_window()
        if window is None:
            return
        state, start_ts, now_ts = window

        if state != self._stream_state:
            if self._stream_state is not None:
                # 换时段强制重订：Yahoo streamer 长连接会静默丢失个别 symbol
                # 的推送（2026-08-09 实测 COHR/GFS 无夜盘 tick，新连接正常）
                self._stream.reconnect()
            self._stream_state = state

        def in_session(ts: int) -> bool:
            return start_ts <= ts <= now_ts + 300

        targets = [s for s, q in quotes.items() if q.ok and not s.endswith(".HK")]
        if not targets:
            return

        self._stream.ensure_running(targets)
        ticks = self._stream.snapshot()
        filled = 0
        for s in targets:
            t = ticks.get(s)
            q = quotes[s]
            applied = False
            if t and in_session(t["ts"]):
                if state == "REGULAR":
                    # 快照（新浪/v7）也是实时的，tick 必须更新才覆盖，防旧 tick 回退
                    if q.regular_time is None or t["ts"] > q.regular_time:
                        q.regular_price = t["price"]
                        q.regular_change_percent = t["change_percent"]
                        applied = True
                elif state == "PRE":
                    q.pre_price = t["price"]
                    q.pre_change_percent = t["change_percent"]
                    applied = True
                else:  # POST / PREPRE
                    q.post_price = t["price"]
                    q.post_change_percent = t["change_percent"]
                    q.post_session = "night" if state == "PREPRE" else "post"
                    applied = True
            elif state == "PREPRE":
                # 夜盘无 tick：回落快照里的盘后终值（v7 盘后字段 20:00 后冻结，
                # 即 19:59 盘后收盘价），post_session="post" 让 UI 标注「盘后」
                q.post_session = "post"
            filled += applied
        logger.info("websocket tick 覆盖 %d/%d 只（时段 %s）",
                    filled, len(targets), state)

    # ---------- 新浪财经批量兜底（仅盘中时段覆盖） ----------

    def _fill_from_sina(self, quotes: dict[str, Quote]) -> None:
        """新浪/腾讯实测**只跟随盘中时段**（2026-08-07 盘后验证：新浪价=盘中收盘价），
        因此只在 REGULAR 时段用新浪覆盖盘中数据（实时性好、防 Yahoo v7 卡顿），
        盘前/盘后数据一律走 Yahoo v7 + quoteSummary 兜底。

        市场状态改用美东时钟（`us_market_state()`），不再信任 Yahoo 的 marketState。
        港股（.HK）时段不同，跳过。
        """
        state = us_market_state()
        self._sina_covered: set[str] = set()
        # 无论是否盘中，都把美股代码的市场状态校正为时钟状态
        for s, q in quotes.items():
            if q.ok and not s.endswith(".HK"):
                q.market_state = state
        if state != "REGULAR":
            return
        try:
            us_symbols = [s for s, q in quotes.items() if q.ok and not s.endswith(".HK")]
            sina = fetch_sina_quotes(us_symbols)
        except Exception:
            logger.warning("新浪兜底整体失败", exc_info=True)
            return
        if not sina:
            return
        filled = 0
        for s, sq in sina.items():
            q = quotes[s]
            q.regular_price, q.regular_change_percent = sq["price"], sq["change_percent"]
            self._sina_covered.add(s)
            filled += 1
        if filled:
            logger.info("新浪盘中数据覆盖 %d 只", filled)

    def _fetch_chunk(self, symbols: list[str]) -> dict[str, Quote]:
        # 显式 15s 超时（默认 30s）：挂起的请求尽早失败，走重试/缓存兜底
        resp = self._data.get(QUOTE_URL, timeout=15,
                              params={"symbols": ",".join(symbols)})
        resp.raise_for_status()
        result = resp.json().get("quoteResponse", {}).get("result", [])
        quotes: dict[str, Quote] = {}
        for item in result:
            symbol = item.get("symbol")
            if not symbol:
                continue
            quotes[symbol] = Quote(
                symbol=symbol,
                name=item.get("shortName") or item.get("longName") or "",
                ok=item.get("regularMarketPrice") is not None,
                error=None if item.get("regularMarketPrice") is not None else "no_price",
                currency=item.get("currency"),
                market_state=item.get("marketState"),
                regular_price=item.get("regularMarketPrice"),
                regular_change_percent=item.get("regularMarketChangePercent"),
                regular_time=item.get("regularMarketTime"),
                pre_price=item.get("preMarketPrice"),
                pre_change_percent=item.get("preMarketChangePercent"),
                post_price=item.get("postMarketPrice"),
                post_change_percent=item.get("postMarketChangePercent"),
            )
        return quotes

    # ---------- 盘前/盘后缺字段兜底（v10 quoteSummary price 模块） ----------

    def _fill_missing_prepost(self, quotes: dict[str, Quote]) -> None:
        """v7 缺当前时段应有的盘前/盘后字段时，逐只从 quoteSummary 补齐。

        美股时段以时钟（us_market_state）为准；新浪已覆盖当前时段的跳过。
        REGULAR 时段新浪未覆盖的（如 OTC IFNNY）连盘中数据一起用
        quoteSummary 覆盖（Yahoo v7 盘中数据可能卡顿过期）。
        """
        clock_state = us_market_state()
        covered = getattr(self, "_sina_covered", set())
        targets: list[str] = []
        need_regular: set[str] = set()
        for s, q in quotes.items():
            if not q.ok:
                continue
            if s.endswith(".HK"):
                # 港股时段不同，沿用 Yahoo 自身的状态字段
                state = (q.market_state or "").upper()
                need_pre = state in ("PRE", "PREPRE") and q.pre_change_percent is None
                need_post = state in ("POST", "POSTPOST", "CLOSED") and q.post_change_percent is None
                if need_pre or need_post:
                    targets.append(s)
                continue
            if s in covered and clock_state != "REGULAR":
                continue
            if clock_state == "REGULAR":
                # 新浪不提供盘前分拆数据，盘中时段所有美股都经 quoteSummary 切片补盘前（minis 展示）；
                # 新浪未覆盖的（如 OTC）连盘中数据一起覆盖（Yahoo v7 盘中可能过期）
                need_pre = q.pre_change_percent is None
                if s not in covered:
                    need_regular.add(s)
            else:
                need_pre = clock_state in ("PRE", "PREPRE") and q.pre_change_percent is None
            need_post = clock_state in ("POST", "POSTPOST", "CLOSED", "PREPRE") and q.post_change_percent is None
            if need_pre or need_post or s in need_regular:
                targets.append(s)
        if not targets:
            return
        # 命中短期缓存的直接复用，只对缓存缺失/过期的发请求（防限流）
        now = time.time()
        expired = [s for s in targets
                   if s not in self._prepost_cache
                   or now - self._prepost_cache[s][0] > FALLBACK_CACHE_TTL]
        # 从未取到过的优先，按缓存陈旧度排序后切片，控制每周期请求量
        expired.sort(key=lambda s: self._prepost_cache.get(s, (0.0, None))[0])
        to_fetch = expired[:FALLBACK_SLICE]
        results: dict[str, dict | None] = {
            s: self._prepost_cache[s][1] for s in targets
            if s not in to_fetch and s in self._prepost_cache}
        if to_fetch:
            logger.info("quoteSummary 兜底补数 %d 只（缓存命中 %d，排队待补 %d）",
                        len(to_fetch), len(targets) - len(expired), len(expired) - len(to_fetch))
            with ThreadPoolExecutor(max_workers=FALLBACK_WORKERS) as ex:
                fetched = dict(zip(to_fetch, ex.map(self._fetch_price_module, to_fetch)))
            for s, price in fetched.items():
                if price:
                    self._prepost_cache[s] = (now, price)
            results.update(fetched)
        filled = 0
        for s, price in results.items():
            if not price:
                continue
            q = quotes[s]
            if q.pre_price is None and price.get("preMarketPrice") is not None:
                q.pre_price = price["preMarketPrice"]
                pct = price.get("preMarketChangePercent")
                # quoteSummary 的涨跌幅是小数（0.011 = 1.1%），v7 是百分数
                q.pre_change_percent = pct * 100 if pct is not None else None
            if q.post_price is None and price.get("postMarketPrice") is not None:
                q.post_price = price["postMarketPrice"]
                pct = price.get("postMarketChangePercent")
                q.post_change_percent = pct * 100 if pct is not None else None
            if s in need_regular and price.get("regularMarketPrice") is not None:
                q.regular_price = price["regularMarketPrice"]
                pct = price.get("regularMarketChangePercent")
                q.regular_change_percent = pct * 100 if pct is not None else None
            filled += 1
        logger.info("兜底补数完成 %d/%d", filled, len(targets))

    def _fetch_price_module(self, symbol: str) -> dict | None:
        for attempt in range(FALLBACK_RETRIES):
            try:
                resp = self._data.get(SUMMARY_URL.format(symbol=symbol), params={"modules": "price"})
                resp.raise_for_status()
                price = resp.json()["quoteSummary"]["result"][0]["price"]
                def raw(key):
                    v = price.get(key)
                    return v.get("raw") if isinstance(v, dict) else v
                return {
                    "regularMarketPrice": raw("regularMarketPrice"),
                    "regularMarketChangePercent": raw("regularMarketChangePercent"),
                    "preMarketPrice": raw("preMarketPrice"),
                    "preMarketChangePercent": raw("preMarketChangePercent"),
                    "postMarketPrice": raw("postMarketPrice"),
                    "postMarketChangePercent": raw("postMarketChangePercent"),
                }
            except Exception:
                if attempt == FALLBACK_RETRIES - 1:
                    logger.warning("quoteSummary 兜底失败: %s", symbol, exc_info=True)
        return None
