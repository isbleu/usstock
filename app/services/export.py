"""导出分享长图：用 Pillow 把全板块行情渲染为竖向 PNG。

版式：K·Bento 摘要风（2026-08-08 用户从五波 demo 中选定）——
顶部 2×2 市场摘要磁贴（上涨/下跌板块数、领涨/领跌板块），
板块头左侧彩色状态条 + 紧凑两行成分股行。支持深/浅双主题（?theme=dark|light）。
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from app.models import Board, Quote
from app.services import quotes as quotes_service
from app.services.boards import load_boards

logger = logging.getLogger(__name__)

# ---------- 调色板（深/浅双主题，与前端 style.css 的 CSS 变量一致，红涨绿跌） ----------
PALETTES = {
    "dark": dict(BG="#12161d", PANEL="#191e27", PANEL2="#222836",
                 BORDER="#262c38", TEXT="#e8eaed", DIM="#9aa0aa",
                 UP="#f23645", DOWN="#0ecb81", FLAT="#8a919c",
                 ACCENT="#5b8def", WARN="#f0b90b"),
    "light": dict(BG="#eef1f5", PANEL="#ffffff", PANEL2="#f0f2f6",
                  BORDER="#dfe4ea", TEXT="#171a20", DIM="#6b7280",
                  UP="#e0263a", DOWN="#0a9e66", FLAT="#8a919c",
                  ACCENT="#2f6fe4", WARN="#b8860b"),
}

WIDTH = 900   # 手机端分享优化：900px 宽（微信内等效放大 1.33 倍）
MARGIN = 28
PAD = 22          # 板块卡片内边距
ROW_H = 88        # 成分股行高（两行式：名称/价格涨幅 + 理由/时段标注）
HEAD_H = 64       # 板块头（左侧色条 + 名称 + 均幅 + 涨跌平）高度
GAP = 18          # 板块卡片间距
TILE_H = 100      # Bento 摘要磁贴高度
TILE_GAP = 14

STATE_LABEL = {
    "PRE": "盘前", "PREPRE": "夜盘", "REGULAR": "盘中",
    "POST": "盘后", "POSTPOST": "盘后", "CLOSED": "休市", "UNKNOWN": "未知",
}

# current（当前时段）口径下板块均幅前的时段标签，与前端 metricTag() 一致
# （CLOSED 时 current 口径实际取盘后值，标「盘后」而非「休市」）
METRIC_SESSION_LABEL = {
    "PRE": "盘前", "REGULAR": "盘中", "POST": "盘后",
    "POSTPOST": "盘后", "CLOSED": "盘后", "PREPRE": "夜盘",
}

# ---------- 字体（可回退查找列表，找不到中文字体时告警） ----------
# 每项可以是 path: str，或 (path: str, index: int) 元组（用于 .ttc 集合）
_REGULAR_CANDIDATES: list[tuple[str, int] | str] = [
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),   # W3，与 macOS 网页字体一致
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
_BOLD_CANDIDATES: list[tuple[str, int] | str] = [
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 2),   # W6（粗体）
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def _find_font(candidates: list[tuple[str, int] | str]) -> tuple[Optional[str], int]:
    for item in candidates:
        path, index = (item, 0) if isinstance(item, str) else item
        if Path(path).is_file():
            return path, index
    return None, 0


class _Fonts:
    """惰性加载的中文字体族；找不到中文字体时回退 Pillow 默认字体并告警。"""

    def __init__(self) -> None:
        regular_path, regular_idx = _find_font(_REGULAR_CANDIDATES)
        bold_path, bold_idx = _find_font(_BOLD_CANDIDATES)
        if bold_path is None:
            bold_path, bold_idx = regular_path, regular_idx
        if regular_path is None:
            logger.warning("未找到中文字体，导出长图的中文可能显示为方块；"
                           "请安装微软雅黑/黑体/Noto Sans CJK/文泉驿之一")
        else:
            logger.info("导出长图字体：regular=%s[%s] bold=%s[%s]",
                        regular_path, regular_idx, bold_path, bold_idx)
        self._regular = (regular_path, regular_idx)
        self._bold = (bold_path, bold_idx)

    def get(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        path, index = self._bold if bold else self._regular
        if path is None:
            return ImageFont.load_default()
        return ImageFont.truetype(path, size, index=index)


# ---------- 格式化 ----------
def _pct(v: Optional[float]) -> str:
    if v is None:
        return "--"
    return f"{'+' if v > 0 else ''}{v:.2f}%"


def _price(v: Optional[float]) -> str:
    if v is None:
        return "--"
    return f"{v:,.2f}"


def _color(pal: dict, v: Optional[float]) -> str:
    if v is None or v == 0:
        return pal["FLAT"]
    return pal["UP"] if v > 0 else pal["DOWN"]


def _session_note(market_state: str, row: dict) -> Optional[tuple[str, float]]:
    """第二行右侧的时段标注：PRE 显示盘前涨幅，POST/CLOSED 显示盘后涨幅，
    PREPRE 有实时 tick 显示夜盘涨幅、无 tick 回落盘后终值并标注「盘后」，
    REGULAR 不显示。"""
    ms = (market_state or "").upper()
    if ms == "PRE" and row.get("pre") is not None:
        return ("盘前 ", row["pre"])
    if ms == "PREPRE" and row.get("post") is not None:
        return ("夜盘 " if row.get("post_session") == "night" else "盘后 ",
                row["post"])
    if ms in ("POST", "POSTPOST", "CLOSED") and row.get("post") is not None:
        return ("盘后 ", row["post"])
    return None


# ---------- 数据组装（复用 quotes 服务缓存） ----------
def _build_rows(board: Board, quotes: dict[str, Quote],
                metric: str = "current") -> list[dict]:
    """成分股行：有效数据按所选口径涨跌幅降序（缺该口径数据排末尾），不可用行排末尾。"""
    valid: list[dict] = []
    unavailable: list[dict] = []
    for s in board.stocks:
        if s.unsupported:
            unavailable.append({"name": s.name or s.symbol, "symbol": s.symbol,
                                "reason": s.reason, "available": False})
            continue
        q = quotes.get(s.symbol)
        if q and q.ok:
            valid.append({
                "name": s.name or q.name or s.symbol,
                "symbol": s.symbol,
                "reason": s.reason,
                "available": True,
                "price": q.regular_price,
                "regular": q.regular_change_percent,
                "pre": q.pre_change_percent,
                "post": q.post_change_percent,
                "post_session": q.post_session,
                "current": q.current_change_percent,
            })
        else:
            unavailable.append({"name": s.name or s.symbol,
                                "symbol": s.symbol, "reason": s.reason,
                                "available": False})
    key = "regular" if metric == "regular" else "current"
    valid.sort(key=lambda r: (r[key] is None, -(r[key] or 0)))
    return valid + unavailable


def _collect(metric: str = "current") -> dict:
    quotes, fetched_at, stale = quotes_service.get_quotes()
    boards = []
    for b in load_boards():
        agg = quotes_service.aggregate_board(b, quotes, metric)
        boards.append({"agg": agg, "rows": _build_rows(b, quotes, metric)})
    boards.sort(key=lambda x: (x["agg"]["current_avg"] is None,
                               -(x["agg"]["current_avg"] or 0)))
    states = [x["agg"]["market_state"] for x in boards
              if x["agg"]["market_state"] != "UNKNOWN"]
    market_state = max(set(states), key=states.count) if states else "UNKNOWN"
    return {"boards": boards, "market_state": market_state,
            "fetched_at": fetched_at, "stale": stale}


# ---------- 绘制 ----------
def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    return draw.textlength(text, font=font)


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, max_w: float) -> str:
    if _text_w(draw, text, font) <= max_w:
        return text
    while text and _text_w(draw, text + "…", font) > max_w:
        text = text[:-1]
    return text + "…"


def _board_height(b: dict) -> int:
    return PAD + HEAD_H + len(b["rows"]) * ROW_H + PAD


def render_png(metric: str = "regular", theme: str = "dark") -> bytes:
    pal = PALETTES.get(theme, PALETTES["dark"])
    data = _collect(metric)
    boards: list[dict] = data["boards"]
    market_state: str = data["market_state"]

    now_bj = datetime.now(ZoneInfo("Asia/Shanghai"))
    data_et = datetime.fromtimestamp(data["fetched_at"], tz=timezone.utc) \
        .astimezone(ZoneInfo("America/New_York"))

    fonts = _Fonts()
    f_title = fonts.get(52, bold=True)
    f_sub = fonts.get(22)
    f_badge = fonts.get(22, bold=True)
    f_tile_label = fonts.get(20)
    f_tile_big = fonts.get(42, bold=True)
    f_tile_small = fonts.get(22)
    f_board = fonts.get(34, bold=True)
    f_avg = fonts.get(38, bold=True)
    f_meta = fonts.get(20)
    f_name = fonts.get(26, bold=True)    # 成分股第一行：名称
    f_sym = fonts.get(20)                # 成分股第一行：代码
    f_price = fonts.get(26)              # 成分股第一行：最新价
    f_chg = fonts.get(30, bold=True)     # 成分股第一行：盘中涨跌幅（主角）
    f_reason = fonts.get(20)             # 成分股第二行：入选理由
    f_note = fonts.get(20)               # 成分股第二行：盘前/盘后/夜盘标注
    f_footer = fonts.get(18)

    # ---- 市场摘要（Bento 磁贴数据） ----
    ups = [b for b in boards if (b["agg"]["current_avg"] or 0) > 0]
    downs = [b for b in boards if (b["agg"]["current_avg"] or 0) < 0]
    leader = max(boards, key=lambda b: b["agg"]["current_avg"] or -999)
    laggard = min(boards, key=lambda b: b["agg"]["current_avg"] or 999)
    tiles = [
        ("上涨板块", str(len(ups)), pal["UP"], f"/ {len(boards)}"),
        ("下跌板块", str(len(downs)), pal["DOWN"], f"/ {len(boards)}"),
        ("领涨板块", leader["agg"]["name"].replace("US ", ""), pal["UP"],
         _pct(leader["agg"]["current_avg"])),
        ("领跌板块", laggard["agg"]["name"].replace("US ", ""),
         _color(pal, laggard["agg"]["current_avg"]),
         _pct(laggard["agg"]["current_avg"])),
    ]

    # ---- 先算总高度 ----
    header_h = MARGIN + 70 + 34 + 34 + 24   # 标题 + 两行时间 + 余量
    summary_h = TILE_H * 2 + TILE_GAP + 28
    footer_h = 60
    board_hs = [_board_height(b) for b in boards]
    total_h = (header_h + summary_h + sum(board_hs) + GAP * len(boards)
               + footer_h + MARGIN)

    img = Image.new("RGB", (WIDTH, total_h), pal["BG"])
    draw = ImageDraw.Draw(img)

    # ---- 标题区 ----
    y = MARGIN + 4
    draw.text((MARGIN, y), "美股概念板块看板", font=f_title, fill=pal["TEXT"])
    state_label = STATE_LABEL.get(market_state.upper(), market_state)
    badge_w = _text_w(draw, state_label, f_badge) + 40
    badge_x = WIDTH - MARGIN - badge_w
    draw.rounded_rectangle([badge_x, y + 8, badge_x + badge_w, y + 56],
                           radius=22, fill=pal["PANEL2"], outline=pal["ACCENT"])
    draw.text((badge_x + 20, y + 22), state_label, font=f_badge,
              fill=pal["ACCENT"])

    y += 70
    draw.text((MARGIN, y), f"生成时间 {now_bj:%Y-%m-%d %H:%M}（北京时间）",
              font=f_sub, fill=pal["DIM"])
    y += 34
    draw.text((MARGIN, y), f"数据时间 {data_et:%Y-%m-%d %H:%M}（美东）",
              font=f_sub, fill=pal["DIM"])
    metric_label = "盘中涨跌幅" if metric == "regular" else "当前时段"
    ml = f"口径：{metric_label}"
    lw = _text_w(draw, ml, f_sub)
    draw.text((WIDTH - MARGIN - lw, y), ml, font=f_sub, fill=pal["DIM"])
    if data["stale"]:
        tip = "数据可能滞后"
        tw = _text_w(draw, tip, f_sub)
        draw.text((WIDTH - MARGIN - lw - 24 - tw, y), tip,
                  font=f_sub, fill=pal["WARN"])

    y = header_h

    # ---- Bento 摘要磁贴（2×2） ----
    tile_w = (WIDTH - MARGIN * 2 - TILE_GAP) // 2
    for i, (label, big, col, small) in enumerate(tiles):
        tx = MARGIN + (i % 2) * (tile_w + TILE_GAP)
        ty = y + (i // 2) * (TILE_H + TILE_GAP)
        draw.rounded_rectangle([tx, ty, tx + tile_w, ty + TILE_H], radius=16,
                               fill=pal["PANEL"], outline=pal["BORDER"])
        draw.text((tx + 20, ty + 14), label, font=f_tile_label, fill=pal["DIM"])
        draw.text((tx + 20, ty + 36), big, font=f_tile_big, fill=col)
        bw = _text_w(draw, big, f_tile_big)
        draw.text((tx + 20 + bw + 10, ty + 56), small, font=f_tile_small,
                  fill=col)
    y += summary_h

    # ---- 板块区块 ----
    for b, bh in zip(boards, board_hs):
        agg = b["agg"]
        avg_v = agg["current_avg"]
        x0, x1 = MARGIN, WIDTH - MARGIN
        draw.rounded_rectangle([x0, y, x1, y + bh], radius=14,
                               fill=pal["PANEL"], outline=pal["BORDER"])
        inner_l, inner_r = x0 + PAD, x1 - PAD
        cy = y + PAD

        # 板块头：左侧彩色状态条 + 名称 + 涨跌平 + 均幅（右）
        draw.rounded_rectangle([inner_l - 6, cy + 4, inner_l - 1,
                                cy + HEAD_H - 8], radius=2,
                               fill=_color(pal, avg_v))
        draw.text((inner_l + 12, cy + 8), agg["name"], font=f_board,
                  fill=pal["TEXT"])
        avg_txt = _pct(avg_v)
        avg_w = _text_w(draw, avg_txt, f_avg)
        # current 口径：均幅前加时段小标签（夜盘/盘后/盘前/盘中），与前端一致
        sess = METRIC_SESSION_LABEL.get(market_state.upper()) \
            if metric == "current" else None
        if sess:
            sess_w = _text_w(draw, sess, f_meta)
            draw.text((inner_r - avg_w - 8 - sess_w, cy + 16), sess,
                      font=f_meta, fill=pal["DIM"])
        draw.text((inner_r - avg_w, cy + 4), avg_txt, font=f_avg,
                  fill=_color(pal, avg_v))
        meta = (f"涨 {agg['up']} · 跌 {agg['down']} · 平 {agg['flat']}"
                + (f" · {agg['unsupported_count']} 只无数据"
                   if agg["unsupported_count"] else ""))
        name_w = _text_w(draw, agg["name"], f_board)
        draw.text((inner_l + 12 + name_w + 16, cy + 16), meta, font=f_meta,
                  fill=pal["DIM"])
        cy += HEAD_H

        # 成分股行（两行式，行间细分隔线）
        for i, row in enumerate(b["rows"]):
            ry = cy + i * ROW_H
            if i > 0:
                draw.line([inner_l, ry, inner_r, ry], fill=pal["BORDER"],
                          width=1)
            ty1 = ry + 10   # 第一行：名称/代码 + 最新价/盘中涨跌幅
            ty2 = ry + 52   # 第二行：入选理由 + 时段标注
            if not row["available"]:
                na = "数据不可用"
                na_w = _text_w(draw, na, f_price)
                name = _ellipsize(draw, row["name"], f_name,
                                  inner_r - inner_l - na_w - 40)
                draw.text((inner_l, ty1 + 8), name, font=f_name,
                          fill=pal["DIM"])
                draw.text((inner_l + _text_w(draw, name, f_name) + 10,
                           ty1 + 12), row["symbol"], font=f_sym,
                          fill=pal["DIM"])
                draw.text((inner_r - na_w, ty1 + 8), na, font=f_price,
                          fill=pal["DIM"])
                if row.get("reason"):
                    draw.text((inner_l, ty2), row["reason"], font=f_reason,
                              fill=pal["DIM"])
                continue
            # 第一行右侧：最新价 + 盘中涨跌幅（任何时段都固定显示盘中涨幅）
            chg_txt = _pct(row["regular"])
            chg_w = _text_w(draw, chg_txt, f_chg)
            draw.text((inner_r - chg_w, ty1), chg_txt, font=f_chg,
                      fill=_color(pal, row["regular"]))
            price_txt = _price(row["price"])
            price_w = _text_w(draw, price_txt, f_price)
            draw.text((inner_r - chg_w - 22 - price_w, ty1 + 2), price_txt,
                      font=f_price, fill=pal["TEXT"])
            # 第一行左侧：名称 + 代码（过长截断）
            sym_w = _text_w(draw, row["symbol"], f_sym)
            name_max = (inner_r - chg_w - 22 - price_w - 28
                        - inner_l - sym_w - 10)
            name = _ellipsize(draw, row["name"], f_name, name_max)
            draw.text((inner_l, ty1 + 2), name, font=f_name, fill=pal["TEXT"])
            draw.text((inner_l + _text_w(draw, name, f_name) + 10, ty1 + 7),
                      row["symbol"], font=f_sym, fill=pal["DIM"])
            # 第二行右侧：盘前/盘后/夜盘标注（右对齐）
            note = _session_note(market_state, row)
            note_x = inner_r
            if note:
                txt = note[0] + _pct(note[1])
                tw = _text_w(draw, txt, f_note)
                note_x = inner_r - tw
                draw.text((note_x, ty2), txt, font=f_note,
                          fill=_color(pal, note[1]))
            # 第二行左侧：入选理由（灰色，超长截断）
            if row.get("reason"):
                reason_max = note_x - 24 - inner_l if note \
                    else inner_r - inner_l
                draw.text((inner_l, ty2),
                          _ellipsize(draw, row["reason"], f_reason,
                                     reason_max),
                          font=f_reason, fill=pal["DIM"])

        y += bh + GAP

    # ---- 页脚 ----
    footer = "数据来源 Yahoo Finance · 红涨绿跌 · 仅供参考，不构成投资建议"
    fw = _text_w(draw, footer, f_footer)
    draw.text(((WIDTH - fw) / 2, total_h - MARGIN - 24), footer,
              font=f_footer, fill=pal["DIM"])

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def export_filename(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    return f"usstock_{now:%Y%m%d_%H%M}.png"
