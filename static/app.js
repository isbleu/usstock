/* 美股板块实时看板前端逻辑 */
"use strict";

const state = {
  config: { refresh_open_ms: 30000, refresh_closed_ms: 300000 },
  summary: null,
  openBoardId: null,
  timer: null,
  countdown: 0,
  mode: localStorage.getItem("view-mode") === "big" ? "big" : "std",
  metric: localStorage.getItem("metric") === "current" ? "current" : "regular",
  theme: localStorage.getItem("theme") === "light" ? "light" : "dark",
};

const $ = (sel) => document.querySelector(sel);

/* ---------- 格式化 ---------- */
function pct(v, digits = 2) {
  if (v === null || v === undefined) return "--";
  return (v > 0 ? "+" : "") + Number(v).toFixed(digits) + "%";
}
function price(v) {
  if (v === null || v === undefined) return "--";
  return Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function cls(v) {
  if (v === null || v === undefined || v === 0) return "flat-text";
  return v > 0 ? "up-text" : "down-text";
}
/* 渐进底色：网格卡片沿用分档 tint（大字版板块头/导出长图已改用左侧状态条）。
   颜色与 --up/--down/--flat 对齐；|avg| <0.5/1.5/3/5% 分档加深，0 或无数据为中性灰。 */
function tintFor(avg) {
  if (avg === null || avg === undefined || avg === 0) return "rgba(154,160,170,0.08)";
  const a = Math.abs(avg);
  const alpha = a < 0.5 ? 0.07 : a < 1.5 ? 0.14 : a < 3 ? 0.22 : a < 5 ? 0.32 : 0.44;
  const light = state.theme === "light";
  return avg > 0
    ? `rgba(${light ? "224,38,58" : "242,54,69"},${alpha})`
    : `rgba(${light ? "10,158,102" : "14,203,129"},${alpha})`;
}
function fmtTime(unixSec) {
  if (!unixSec) return "--";
  return new Date(unixSec * 1000).toLocaleString("zh-CN", { hour12: false });
}

const STATE_LABEL = {
  PRE: "盘前", REGULAR: "盘中", POST: "盘后", POSTPOST: "盘后",
  CLOSED: "休市", PREPRE: "夜盘", UNKNOWN: "未知",
};

/* ---------- 状态栏 ---------- */
function renderStatusbar(data) {
  const ms = (data.market_state || "UNKNOWN").toUpperCase();
  const badge = $("#market-state");
  badge.textContent = STATE_LABEL[ms] || ms;
  badge.className = "badge " + (
    ms === "REGULAR" ? "open" :
    ms === "PRE" || ms === "PREPRE" ? "pre" :
    ms === "POST" || ms === "POSTPOST" ? "post" : "closed"
  );
  $("#data-time").textContent = fmtTime(data.timestamp);
  $("#stale-tip").classList.toggle("hidden", !data.stale);
}

function tickClocks() {
  // 美东时间
  $("#et-time").textContent = new Date().toLocaleString("zh-CN", {
    timeZone: "America/New_York", hour12: false,
  });
  // SSE 推送活跃（10s 内有推送刷新）时倒计时无意义，直接显示「已实时推送」；
  // 推送停了（休市/SSE 断连）才显示轮询倒计时
  if (Date.now() - _lastPushRefresh < 10000) {
    $("#refresh-hint").textContent = "已实时推送";
    return;
  }
  if (state.countdown > 0) {
    state.countdown -= 1;
  }
  $("#refresh-hint").innerHTML = `刷新 <b>${state.countdown}</b>s`;
}

/* ---------- 总览卡片 ---------- */
function renderOverview(boards) {
  const sorted = [...boards].sort((a, b) => (b.current_avg ?? -Infinity) - (a.current_avg ?? -Infinity));
  const el = $("#overview");
  el.innerHTML = "";
  for (const b of sorted) {
    const card = document.createElement("div");
    card.className = "card";
    card.style.setProperty("--tint", tintFor(b.current_avg));
    card.innerHTML = `
      <div class="board-name">${b.name}</div>
      <div class="avg ${cls(b.current_avg)}">${pct(b.current_avg)}</div>
      <div class="meta">
        <span class="up-text">涨 ${b.up}</span> ·
        <span class="down-text">跌 ${b.down}</span> · 平 ${b.flat}
        ${b.unsupported_count ? ` · ${b.unsupported_count} 只无数据` : ""}<br>
        领涨 ${b.leader ? `${b.leader.name || b.leader.symbol} <b class="${cls(b.leader.change_percent)}">${pct(b.leader.change_percent)}</b>` : "--"}
      </div>
      <div class="minis">
        <span>盘前 <b class="${cls(b.avg_pre)}">${pct(b.avg_pre)}</b></span>
        <span>盘中 <b class="${cls(b.avg_regular)}">${pct(b.avg_regular)}</b></span>
        <span>${postLabel()} <b class="${cls(b.avg_post)}">${pct(b.avg_post)}</b></span>
      </div>`;
    card.addEventListener("click", () => openBoard(b.id));
    el.appendChild(card);
  }
}

/* ---------- 板块明细 ---------- */
async function openBoard(boardId) {
  state.openBoardId = boardId;
  $("#detail").classList.remove("hidden");
  await loadDetail();
  $("#detail").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadDetail() {
  if (!state.openBoardId) return;
  try {
    const resp = await fetch(`/api/boards/${state.openBoardId}`);
    if (!resp.ok) return;
    const data = await resp.json();
    $("#detail-title").textContent = `${data.name}（${STATE_LABEL[(data.market_state || "").toUpperCase()] || data.market_state}）`;
    renderDetailTable(data);
  } catch (e) {
    console.error("加载板块明细失败", e);
  }
}

function currentPeriod(ms) {
  ms = (ms || "").toUpperCase();
  if (ms === "PRE") return "pre";
  // PREPRE（夜盘）沿用盘后口径高亮
  if (ms === "POST" || ms === "POSTPOST" || ms === "PREPRE") return "post";
  if (ms === "REGULAR") return "regular";
  return null; // 休市不高亮
}

// 夜盘时段 post 通道承载雪球 Blue Ocean 夜盘数据，标签随之切换
function postLabel(ms) {
  ms = ms || ((state.summary || {}).market_state || "");
  return ms.toUpperCase() === "PREPRE" ? "夜盘" : "盘后";
}

function renderDetailTable(data) {
  const period = currentPeriod(data.market_state);
  const postTh = postLabel(data.market_state);
  document.querySelectorAll("#detail-table th").forEach((th) => {
    th.classList.toggle("current", th.dataset.period === period);
    // 夜盘时段盘后列改标「夜盘价/夜盘%」
    if (th.dataset.period === "post")
      th.textContent = postTh + (th.textContent.endsWith("%") ? "%" : "价");
  });
  const tbody = $("#detail-table tbody");
  tbody.innerHTML = "";
  for (const s of data.stocks) {
    const tr = document.createElement("tr");
    if (s.unsupported || s.ok === false) {
      tr.className = "unavailable";
      tr.innerHTML = `
        <td class="col-name">${s.name || s.symbol}<span class="sym">${s.symbol}</span></td>
        <td class="col-reason">${s.reason || ""}</td>
        <td colspan="6">${s.unsupported ? "数据不可用（非同花顺外代码）" : "暂无数据"}</td>`;
    } else {
      const cell = (p, v, isPct) =>
        `<td class="${p === period ? "current " : ""}${isPct ? cls(v) : ""}">${isPct ? pct(v) : price(v)}</td>`;
      tr.innerHTML = `
        <td class="col-name">${s.name || s.symbol}<span class="sym">${s.symbol}</span></td>
        <td class="col-reason">${s.reason || ""}</td>
        ${cell("regular", s.regular_price)}
        ${cell("regular", s.regular_change_percent, true)}
        ${cell("pre", s.pre_price)}
        ${cell("pre", s.pre_change_percent, true)}
        ${cell("post", s.post_price)}
        ${cell("post", s.post_change_percent, true)}`;
    }
    tbody.appendChild(tr);
  }
}

$("#detail-close").addEventListener("click", () => {
  state.openBoardId = null;
  $("#detail").classList.add("hidden");
});

/* ---------- 标准看板 / 大字版切换 ---------- */
function applyMode(mode) {
  state.mode = mode;
  localStorage.setItem("view-mode", mode);
  document.querySelectorAll("#view-tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === mode));
  const big = mode === "big";
  $("#bigview").classList.toggle("hidden", !big);
  $("#overview").classList.toggle("hidden", big);
  if (big) {
    $("#detail").classList.add("hidden");
    loadBigView();
  } else if (!state.openBoardId) {
    $("#detail").classList.add("hidden");
  } else {
    $("#detail").classList.remove("hidden");
  }
}

document.querySelectorAll("#view-tabs button").forEach((b) =>
  b.addEventListener("click", () => applyMode(b.dataset.mode)));

/* ---------- 设置：主题（dark=深色默认 / light=浅色，导出长图同步跟随） ---------- */
function applyTheme(t) {
  state.theme = t;
  localStorage.setItem("theme", t);
  document.documentElement.dataset.theme = t;
  document.querySelectorAll('input[name="theme"]').forEach((r) => { r.checked = r.value === t; });
}
document.querySelectorAll('input[name="theme"]').forEach((r) => {
  r.addEventListener("change", () => {
    applyTheme(r.value);
    refresh();   // 换主题后重渲染（网格卡片 tint 颜色随主题变化）
  });
});
applyTheme(state.theme);

/* ---------- 设置：板块涨跌幅口径（regular=盘中默认 / current=当前时段） ---------- */
document.querySelectorAll('input[name="metric"]').forEach((r) => {
  r.checked = r.value === state.metric;
  r.addEventListener("change", () => {
    state.metric = r.value;
    localStorage.setItem("metric", state.metric);
    refresh();   // 立即用新口径重新拉取渲染（两个视图都走 refresh 链路）
  });
});
$("#settings-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  $("#settings-pop").classList.toggle("hidden");
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#settings-wrap"))
    $("#settings-pop").classList.add("hidden");
});

/* ---------- 大字版（导出长图同款两行卡片排版） ---------- */
function sessionNote(ms, s) {
  // 与导出长图一致：PRE 显示盘前、POST/CLOSED 显示盘后、PREPRE 显示夜盘、REGULAR 不显示
  ms = (ms || "").toUpperCase();
  if (ms === "PRE" && s.pre_change_percent != null)
    return ["盘前 ", s.pre_change_percent];
  if (ms === "PREPRE" && s.post_change_percent != null)
    return ["夜盘 ", s.post_change_percent];
  if ((ms === "POST" || ms === "POSTPOST" || ms === "CLOSED") && s.post_change_percent != null)
    return ["盘后 ", s.post_change_percent];
  return null;
}

async function loadBigView() {
  try {
    const resp = await fetch(`/api/all?metric=${state.metric}`);
    if (!resp.ok) return;
    renderBigView(await resp.json());
  } catch (e) {
    console.error("加载大字版失败", e);
  }
}

function renderBigView(data) {
  const el = $("#bigview");
  el.innerHTML = "";

  // K 版式 Bento 摘要磁贴：boards 已按 current_avg 降序
  const boards = data.boards || [];
  const ups = boards.filter((b) => (b.current_avg ?? 0) > 0).length;
  const downs = boards.filter((b) => (b.current_avg ?? 0) < 0).length;
  const leader = boards.find((b) => b.current_avg != null);
  const laggard = [...boards].reverse().find((b) => b.current_avg != null);
  const shortName = (b) => (b ? b.name.replace(/^US /, "") : "--");
  const bento = document.createElement("div");
  bento.className = "bento";
  bento.innerHTML = `
    <div class="bento-tile"><div class="bento-label">上涨板块</div>
      <div class="bento-big up-text">${ups}<span class="bento-small flat-text">/ ${boards.length}</span></div></div>
    <div class="bento-tile"><div class="bento-label">下跌板块</div>
      <div class="bento-big down-text">${downs}<span class="bento-small flat-text">/ ${boards.length}</span></div></div>
    <div class="bento-tile"><div class="bento-label">领涨板块</div>
      <div class="bento-big ${leader ? cls(leader.current_avg) : ""}">${shortName(leader)}<span class="bento-small">${leader ? pct(leader.current_avg) : ""}</span></div></div>
    <div class="bento-tile"><div class="bento-label">领跌板块</div>
      <div class="bento-big ${laggard ? cls(laggard.current_avg) : ""}">${shortName(laggard)}<span class="bento-small">${laggard ? pct(laggard.current_avg) : ""}</span></div></div>`;
  el.appendChild(bento);

  for (const b of data.boards) {
    const board = document.createElement("div");
    board.className = "big-board";
    const stocksHtml = b.stocks.map((s) => {
      if (s.unsupported || s.ok === false) {
        return `<div class="big-stock unavailable">
          <div class="big-line1">
            <span class="big-name">${s.name || s.symbol}<span class="big-sym">${s.symbol}</span></span>
            <span class="big-na">数据不可用</span>
          </div>
          ${s.reason ? `<div class="big-line2"><span class="big-reason">${s.reason}</span></div>` : ""}
        </div>`;
      }
      const note = sessionNote(data.market_state, s);
      return `<div class="big-stock">
        <div class="big-line1">
          <span class="big-name">${s.name || s.symbol}<span class="big-sym">${s.symbol}</span></span>
          <span class="big-quote">
            <span class="big-price">${price(s.regular_price)}</span>
            <span class="big-chg ${cls(s.regular_change_percent)}">${pct(s.regular_change_percent)}</span>
          </span>
        </div>
        <div class="big-line2">
          <span class="big-reason">${s.reason || ""}</span>
          ${note ? `<span class="big-note ${cls(note[1])}">${note[0]}${pct(note[1])}</span>` : ""}
        </div>
      </div>`;
    }).join("");
    // 板块头左侧彩色状态条（与导出长图 K 版式一致）
    const accent = b.current_avg == null || b.current_avg === 0
      ? "var(--flat)" : b.current_avg > 0 ? "var(--up)" : "var(--down)";
    board.innerHTML = `
      <div class="big-board-head" style="--head-accent:${accent}">
        <span class="big-board-name">${b.name}</span>
        <span class="big-board-meta">涨 ${b.up} · 跌 ${b.down} · 平 ${b.flat}${b.unsupported_count ? ` · ${b.unsupported_count} 只无数据` : ""}</span>
        <span class="big-board-avg ${cls(b.current_avg)}">${pct(b.current_avg)}</span>
      </div>
      ${stocksHtml}`;
    el.appendChild(board);
  }
}

/* ---------- 导出分享长图 ---------- */
$("#export-btn").addEventListener("click", async () => {
  const btn = $("#export-btn");
  if (btn.disabled) return;
  btn.disabled = true;
  const oldText = btn.textContent;
  btn.textContent = "生成中…";
  try {
    const resp = await fetch(`/api/export.png?metric=${state.metric}&theme=${state.theme}`);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const blob = await resp.blob();
    const cd = resp.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename="?([^";]+)"?/);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = m ? m[1] : "usstock.png";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (e) {
    console.error("导出长图失败", e);
    btn.textContent = "导出失败，重试";
    setTimeout(() => { btn.textContent = oldText; }, 2000);
    return;
  } finally {
    btn.disabled = false;
  }
  btn.textContent = oldText;
});

/* ---------- 刷新调度 ---------- */
function refreshIntervalMs() {
  const ms = ((state.summary && state.summary.market_state) || "").toUpperCase();
  const openLike = ["PRE", "PREPRE", "REGULAR", "POST", "POSTPOST"].includes(ms);
  return openLike ? state.config.refresh_open_ms : state.config.refresh_closed_ms;
}

async function refresh() {
  try {
    const resp = await fetch(`/api/summary?metric=${state.metric}`);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    state.summary = await resp.json();
    renderStatusbar(state.summary);
    renderOverview(state.summary.boards);
    if (state.openBoardId) loadDetail();
    if (state.mode === "big") loadBigView();
  } catch (e) {
    console.error("刷新失败，保留旧数据", e);
    $("#stale-tip").classList.remove("hidden");
  } finally {
    scheduleNext();
  }
}

function scheduleNext() {
  clearTimeout(state.timer);
  const interval = refreshIntervalMs();
  state.countdown = Math.round(interval / 1000);
  // 倒计时文案统一由 tickClocks 每秒渲染（推送活跃时显示「已实时推送」）
  state.timer = setTimeout(refresh, interval);
}

/* ---------- 夜盘实时推送（SSE） ----------
   服务端夜盘 tick 到达即推 "tick"，收到立即刷新，不再干等轮询周期；
   轮询仍保留作兜底（SSE 断连/非夜盘时段无推送时）。前端再节流 1.5s，
   tick 爆发时整页刷新一次取齐，不逐条抖动。 */
let _lastPushRefresh = 0;
function startEventStream() {
  if (!window.EventSource) return;
  const es = new EventSource("/api/events");
  es.onmessage = () => {
    const now = Date.now();
    if (now - _lastPushRefresh < 1500) return;
    _lastPushRefresh = now;
    refresh();  // refresh 内部会 scheduleNext()，轮询计时随之重置
  };
  es.onerror = () => { /* EventSource 自动重连，无需处理 */ };
}

/* ---------- 启动 ---------- */
(async function init() {
  try {
    const resp = await fetch("/api/config");
    if (resp.ok) state.config = await resp.json();
  } catch (e) { /* 用默认值 */ }
  setInterval(tickClocks, 1000);
  tickClocks();
  startEventStream();
  // ?view=big|std 可覆盖 localStorage（便于分享大字版链接）
  const urlView = new URLSearchParams(location.search).get("view");
  applyMode(urlView === "big" || urlView === "std" ? urlView : state.mode);
  refresh();
})();
