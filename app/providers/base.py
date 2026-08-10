"""行情数据源抽象接口，便于后续替换 / 增加数据源。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import Quote


class QuoteProvider(ABC):
    @abstractmethod
    def fetch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        """批量拉取报价。返回 symbol -> Quote；失败的 symbol 也应有 Quote(ok=False) 条目。"""
        raise NotImplementedError
