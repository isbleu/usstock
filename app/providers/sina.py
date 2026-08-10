"""新浪财经美股批量报价（兜底数据源）。

- 接口：`https://hq.sinajs.cn/list=gb_<symbol>...`（GBK 编码，需 Referer 头）
- 批量一次请求、无 cookie/crumb、国内网络直连，无限流困扰
- 字段 1 = 最新价（**跟随当前交易时段**：盘前时段是盘前价、盘中是盘中价、
  盘后/夜盘时段是盘后价），字段 2 = 对应时段涨跌幅（相对昨收，百分数）
- 覆盖 gb_ 美股（含 SKHY/SPCX/BWET 等同花顺式代码）；OTC（如 IFNNY）无数据

仅作为 Yahoo 缺字段时的补充，不做主数据源（无盘前/盘中/盘后分拆字段）。
"""
from __future__ import annotations

import logging
import re
from datetime import date

import requests

logger = logging.getLogger(__name__)

SINA_URL = "https://hq.sinajs.cn/list={list}"
HEADERS = {"Referer": "https://finance.sina.com.cn"}
CHUNK_SIZE = 50
TIMEOUT = 10

_LINE_RE = re.compile(r'var hq_str_(\w+)="([^"]*)"')


def _sina_code(symbol: str) -> str | None:
    """Yahoo 代码 → 新浪代码。港股 rt_hk + 5 位数字，美股 gb_ + 小写。"""
    if symbol.endswith(".HK"):
        return "rt_hk" + symbol[:-3].zfill(5)
    if "." in symbol:  # 其他市场后缀不支持
        return None
    return "gb_" + symbol.lower()


def _is_stale(time_str: str) -> bool:
    """新浪对无效代码会返回价格 1.0 的陈旧假记录，按日期过滤（周末/假日数据保留）。"""
    try:
        d = date.fromisoformat(time_str.strip()[:10])
        return (date.today() - d).days > 10
    except ValueError:
        return False


def fetch_sina_quotes(symbols: list[str]) -> dict[str, dict]:
    """批量取新浪实时价。返回 {yahoo_symbol: {"price", "change_percent", "time"}}。"""
    code_map = {s: c for s in symbols if (c := _sina_code(s))}
    out: dict[str, dict] = {}
    # gb_ 与 rt_hk 混在一个 list 里新浪会丢 rt_hk，按前缀分组分别请求
    groups: dict[str, dict[str, str]] = {}
    for yahoo_sym, code in code_map.items():
        groups.setdefault(code.split("_")[0], {})[yahoo_sym] = code
    for chunk_all in groups.values():
        items = list(chunk_all.items())
        for i in range(0, len(items), CHUNK_SIZE):
            chunk = dict(items[i : i + CHUNK_SIZE])
            try:
                resp = requests.get(
                    SINA_URL.format(list=",".join(chunk.values())),
                    headers=HEADERS, timeout=TIMEOUT)
                resp.raise_for_status()
                text = resp.content.decode("gbk", errors="replace")
            except Exception:
                logger.warning("新浪批量报价请求失败", exc_info=True)
                continue
            sina_to_yahoo = {v: k for k, v in chunk.items()}
            for m in _LINE_RE.finditer(text):
                yahoo_sym = sina_to_yahoo.get(m.group(1))
                fields = m.group(2).split(",")
                if not yahoo_sym:
                    continue
                # 字段布局不同：gb_ 美股 [1]=最新价 [2]=涨跌幅 [3]=时间；
                # rt_hk 港股 [6]=最新价 [8]=涨跌幅 [17]=日期
                if m.group(1).startswith("rt_hk"):
                    p_idx, c_idx, t_idx = 6, 8, 17
                else:
                    p_idx, c_idx, t_idx = 1, 2, 3
                if len(fields) <= max(p_idx, c_idx) or not fields[p_idx]:
                    continue
                time_str = fields[t_idx] if len(fields) > t_idx else ""
                if _is_stale(time_str):
                    continue
                try:
                    out[yahoo_sym] = {
                        "price": float(fields[p_idx]),
                        "change_percent": float(fields[c_idx]),
                        "time": time_str,
                    }
                except ValueError:
                    continue
    return out
