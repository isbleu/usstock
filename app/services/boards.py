"""板块定义加载（data/boards.json）。"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.models import Board, Stock

DATA_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "boards.json"


@lru_cache(maxsize=1)
def load_boards() -> list[Board]:
    with open(DATA_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    boards = []
    for b in raw["boards"]:
        stocks = [
            Stock(
                symbol=s["symbol"],
                name=s.get("name", ""),
                market=s.get("market", "US"),
                unsupported=bool(s.get("unsupported", False)),
                reason=s.get("reason", ""),
            )
            for s in b["stocks"]
        ]
        boards.append(Board(id=b["id"], name=b["name"], stocks=stocks))
    return boards


def get_board(board_id: str) -> Board | None:
    for b in load_boards():
        if b.id == board_id:
            return b
    return None


def all_supported_symbols() -> list[str]:
    symbols: list[str] = []
    for b in load_boards():
        for s in b.stocks:
            if not s.unsupported and s.symbol not in symbols:
                symbols.append(s.symbol)
    return symbols
