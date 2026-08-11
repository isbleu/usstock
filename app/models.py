"""数据模型：行情报价与板块聚合结果。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Quote:
    symbol: str
    name: str = ""
    ok: bool = False
    error: Optional[str] = None
    currency: Optional[str] = None
    market_state: Optional[str] = None          # PRE / REGULAR / POST / POSTPOST / CLOSED ...
    regular_price: Optional[float] = None
    regular_change_percent: Optional[float] = None
    regular_time: Optional[int] = None          # unix 秒
    pre_price: Optional[float] = None
    pre_change_percent: Optional[float] = None
    post_price: Optional[float] = None
    post_change_percent: Optional[float] = None
    # post_* 值的来源时段："night"=夜盘 websocket 实时 tick；"post"=盘后
    # （含夜盘时段无 tick 时用盘后终值兜底）；None=非夜盘时段的普通快照
    post_session: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def current_change_percent(self) -> Optional[float]:
        """按当前市场状态选取应展示的涨跌幅，缺数据时回落到盘中涨跌幅。"""
        state = (self.market_state or "").upper()
        if state == "PRE" and self.pre_change_percent is not None:
            return self.pre_change_percent
        # PREPRE（夜盘/隔夜时段）post 字段优先承载 Yahoo websocket 实时推送；
        # 无 tick 时回落盘后终值（post_session="post"，UI 标注「盘后」而非「夜盘」）
        if state in ("POST", "POSTPOST", "CLOSED", "PREPRE") and self.post_change_percent is not None:
            return self.post_change_percent
        return self.regular_change_percent


@dataclass
class Stock:
    symbol: str
    name: str = ""
    market: str = "US"
    unsupported: bool = False
    reason: str = ""          # 入选该板块的理由（一句话）


@dataclass
class Board:
    id: str
    name: str
    stocks: list[Stock] = field(default_factory=list)
