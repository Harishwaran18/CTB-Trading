"use strict";

const $ = (id) => document.getElementById(id);

const state = { auto: true, busy: false };

/* ------------------------------------------------------------------ utils */
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));

const num = (v, d = 4) => {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(d) : "—";
};

const fmtPrice = (v) => {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1000) return n.toFixed(2);
  if (Math.abs(n) >= 1) return n.toFixed(4);
  return n.toFixed(6);
};

async function api(path) {
  const res = await fetch(path, { cache: "no-store" });
  let body = null;
  try { body = await res.json(); } catch (_) { body = null; }
  if (!res.ok) throw new Error((body && body.detail) || `HTTP ${res.status}`);
  return body;
}

const dirClass = (d) => (d === "BUY" ? "up" : d === "SELL" ? "down" : "flat");

function setStatus(kind, text) {
  const pill = $("status-pill");
  pill.classList.remove("ok", "err");
  if (kind) pill.classList.add(kind);
  $("status-text").textContent = text;
}

/* ------------------------------------------------------------------- cards */
function ring(conf, cls) {
  const r = 26;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, Number(conf) || 0));
  const off = c * (1 - pct / 100);
  return `<svg class="ring" viewBox="0 0 62 62" role="img" aria-label="confidence ${Math.round(pct)} percent">
    <circle class="ring-bg" cx="31" cy="31" r="${r}"></circle>
    <circle class="ring-fg ${cls}" cx="31" cy="31" r="${r}"
      stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${off.toFixed(2)}"
      transform="rotate(-90 31 31)"></circle>
    <text class="ring-text" x="31" y="36" text-anchor="middle">${Math.round(pct)}</text>
  </svg>`;
}

function cardHtml(sig) {
  if (sig.error) {
    return `<article class="card error"><div class="sym">${esc(sig.symbol)}</div>
      <p class="muted">${esc(sig.error)}</p></article>`;
  }
  const cls = dirClass(sig.direction);
  const reasons = (sig.reasons || []).slice(0, 3).map((r) => `<li>${esc(r)}</li>`).join("");
  const action = sig.actionable
    ? `<span class="on">● actionable</span>`
    : `<span class="off">○ below threshold</span>`;
  const label = sig.binary ? `${sig.direction} · ${sig.binary}` : sig.direction;
  const width = Math.min(100, Math.abs(Number(sig.score) || 0));
  return `<article class="card ${cls} ${sig.actionable ? "" : "dim"}"
      data-symbol="${esc(sig.symbol)}" data-interval="${esc(sig.interval)}">
    <div class="card-top">
      <div>
        <div class="sym">${esc(sig.symbol)}</div>
        <div class="chips">
          <span class="chip">${esc(sig.asset_class)}</span>
          <span class="chip regime ${esc(sig.regime)}">${esc(sig.regime)}</span>
        </div>
      </div>
      ${ring(sig.confidence, cls)}
    </div>
    <div class="dir-row">
      <span class="badge ${cls}">${esc(label)}</span>
      <span class="price">${fmtPrice(sig.price)}</span>
    </div>
    <div class="levels">
      <div><span>Entry</span><b>${fmtPrice(sig.entry)}</b></div>
      <div><span>Stop</span><b class="down">${fmtPrice(sig.stop_loss)}</b></div>
      <div><span>Target</span><b class="up">${fmtPrice(sig.take_profit)}</b></div>
      <div><span>R:R</span><b>1 : ${num(sig.risk_reward, 2)}</b></div>
    </div>
    <div class="scorebar"><i style="width:${width}%"></i></div>
    <ul class="reasons">${reasons}</ul>
    <div class="card-foot">${action}<span>HTF ${esc(sig.higher_direction)}</span></div>
  </article>`;
}

function renderCards(signals) {
  const host = $("cards");
  if (!signals.length) {
    host.innerHTML = `<div class="card error">No symbols configured.</div>`;
    return;
  }
  host.innerHTML = signals.map(cardHtml).join("");
  host.querySelectorAll(".card[data-symbol]").forEach((el) => {
    el.addEventListener("click", () => openMarket(el.dataset.symbol, el.dataset.interval));
  });
}

/* --------------------------------------------------------------- data load */
async function loadScan(force = false) {
  if (state.busy) return;
  state.busy = true;
  try {
    const data = await api(`/api/scan${force ? "?refresh=true" : ""}`);
    renderCards(data.signals || []);
    const actionable = (data.signals || []).filter((s) => s.actionable).length;
    $("kpi-signals").textContent = String(actionable);
    const when = new Date(data.ts * 1000).toLocaleTimeString();
    $("scan-meta").textContent = `updated ${when}${data.cached ? " · cached" : ""}`;
    $("foot-ts").textContent = `last scan ${when}`;
    setStatus("ok", `live · ${(data.signals || []).length} markets`);
  } catch (err) {
    setStatus("err", "offline");
    $("scan-meta").textContent = err.message;
  } finally {
    state.busy = false;
  }
}

async function loadPerf() {
  try {
    const data = await api("/api/performance?limit=20");
    const o = data.overall || {};
    $("kpi-win").textContent = o.trades ? `${o.win_rate}%` : "—";
    $("kpi-win-sub").textContent = o.trades ? `${o.wins}/${o.trades} trades` : "no trades yet";
    $("kpi-avgr").textContent = o.trades
      ? `${o.avg_r >= 0 ? "+" : ""}${o.avg_r}`
      : "—";
    $("kpi-pf").textContent = o.trades
      ? (o.profit_factor === null || o.profit_factor === undefined ? "∞" : o.profit_factor)
      : "—";
    renderSignals(data.recent || []);
  } catch (_) {
    /* keep previous values */
  }
}

function renderSignals(rows) {
  const body = $("signals-body");
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="5" class="muted">No stored signals yet.</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((r) => {
    const cls = dirClass(r.direction);
    const t = new Date((r.generated_at || 0) * 1000).toLocaleTimeString();
    return `<tr>
      <td>${t}</td>
      <td>${esc(r.symbol)}</td>
      <td class="${cls}">${esc(r.direction)}</td>
      <td>${num(r.confidence, 1)}</td>
      <td>${fmtPrice(r.price)}</td>
    </tr>`;
  }).join("");
}

/* ------------------------------------------------------------------- modal */
function candleChart(candles) {
  if (!candles || !candles.length) return `<p class="muted">No candles.</p>`;
  const W = 860, H = 320, padL = 8, padR = 62, padT = 14, padB = 18;
  const data = candles.slice(-140);
  const lo = Math.min(...data.map((d) => d.l));
  const hi = Math.max(...data.map((d) => d.h));
  const span = hi - lo || 1;
  const plotW = W - padL - padR;
  const step = plotW / data.length;
  const bw = Math.max(1.5, Math.min(9, step * 0.62));
  const x = (i) => padL + (i + 0.5) * step;
  const y = (p) => padT + ((hi - p) / span) * (H - padT - padB);

  let grid = "";
  for (let g = 0; g <= 4; g += 1) {
    const py = padT + (g / 4) * (H - padT - padB);
    const val = hi - (g / 4) * span;
    grid += `<line x1="${padL}" y1="${py}" x2="${W - padR}" y2="${py}" stroke="#141c27" stroke-width="1"/>`;
    grid += `<text x="${W - padR + 6}" y="${py + 4}" fill="#7c8a9e" font-family="ui-monospace" font-size="10">${fmtPrice(val)}</text>`;
  }

  let body = "";
  data.forEach((d, i) => {
    const up = d.c >= d.o;
    const color = up ? "#2ee6a4" : "#ff5f6d";
    const cx = x(i);
    body += `<line x1="${cx}" y1="${y(d.h)}" x2="${cx}" y2="${y(d.l)}" stroke="${color}" stroke-width="1"/>`;
    const top = y(Math.max(d.o, d.c));
    const bot = y(Math.min(d.o, d.c));
    body += `<rect x="${(cx - bw / 2).toFixed(2)}" y="${top.toFixed(2)}" width="${bw.toFixed(2)}"
      height="${Math.max(1, bot - top).toFixed(2)}" fill="${color}" opacity="0.9"/>`;
  });

  const last = data[data.length - 1];
  const ly = y(last.c);
  const line = `<line x1="${padL}" y1="${ly}" x2="${W - padR}" y2="${ly}" stroke="#5aa9ff" stroke-dasharray="4 4" stroke-width="1" opacity="0.85"/>`;

  return `<svg class="candle-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img"
    aria-label="candlestick chart">${grid}${line}${body}</svg>`;
}

async function openMarket(symbol, interval) {
  const modal = $("modal");
  modal.hidden = false;
  $("modal-title").textContent = symbol;
  $("modal-sub").textContent = "loading market…";
  $("modal-body").innerHTML = `<p class="muted">Fetching candles and indicators…</p>`;
  try {
    const data = await api(`/api/market/${symbol}?interval=${encodeURIComponent(interval)}`);
    const sig = data.signal;
    $("modal-sub").textContent = `${data.asset_class} · ${data.interval} · ${data.candles.length} candles`;

    const summary = sig
      ? `<div class="bt-tiles">
          <div class="tile"><span>Signal</span><b class="${dirClass(sig.direction)}">${esc(sig.direction)}${sig.binary ? ` · ${esc(sig.binary)}` : ""}</b></div>
          <div class="tile"><span>Confidence</span><b>${num(sig.confidence, 1)}%</b></div>
          <div class="tile"><span>Regime</span><b>${esc(sig.regime)}</b></div>
          <div class="tile"><span>R:R</span><b>1 : ${num(sig.risk_reward, 2)}</b></div>
        </div>
        <ul class="reasons">${(sig.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`
      : `<p class="muted">No signal for this market right now.</p>`;

    const ind = data.indicators || {};
    const grid = Object.entries(ind)
      .filter(([, v]) => typeof v === "number")
      .map(([k, v]) => `<div class="ind"><span>${esc(k)}</span><b>${num(v, k === "volume" || k === "vol_ma" ? 0 : 4)}</b></div>`)
      .join("");

    $("modal-body").innerHTML = `${candleChart(data.candles)}${summary}<div class="ind-grid">${grid}</div>`;
  } catch (err) {
    $("modal-body").innerHTML = `<p class="muted">Could not load market: ${esc(err.message)}</p>`;
  }
}

function closeModal() {
  $("modal").hidden = true;
}

/* ---------------------------------------------------------------- backtest */
function renderBacktest(data) {
  const o = (data.summary && data.summary.overall) || {};
  if (!o.trades) {
    $("bt-result").innerHTML = `<p class="muted">No trades triggered. Try more bars or a different market/strategy.</p>`;
    return;
  }
  const pf = o.profit_factor === null || o.profit_factor === undefined ? "∞" : o.profit_factor;
  const tiles = [
    ["Trades", o.trades],
    ["Win rate", `${o.win_rate}%`],
    ["Avg R", `${o.avg_r >= 0 ? "+" : ""}${o.avg_r}`],
    ["Profit factor", pf],
    ["Directional", `${data.directional}%`],
    ["Max DD", `${o.max_drawdown_r} R`],
  ].map(([label, val]) => `<div class="tile"><span>${label}</span><b>${val}</b></div>`).join("");

  const buckets = Object.entries(data.buckets || {}).map(([label, st]) => {
    const w = Math.max(0, Math.min(100, st.win_rate || 0));
    return `<div class="bucket">
      <span>${esc(label)}</span>
      <div class="bucket-bar"><i style="width:${w}%"></i></div>
      <small>${st.trades} · ${st.win_rate}%</small>
    </div>`;
  }).join("");

  $("bt-result").innerHTML =
    `<div class="bt-tiles">${tiles}</div>` +
    (buckets ? `<div class="buckets">${buckets}</div>` : "") +
    `<pre class="raw">${esc(data.text)}</pre>`;
}

async function runBacktest(event) {
  event.preventDefault();
  const btn = $("bt-run");
  btn.disabled = true;
  btn.textContent = "Running…";
  $("bt-result").innerHTML = `<p class="muted">Replaying history bar by bar…</p>`;
  const params = new URLSearchParams({
    symbol: $("bt-symbol").value.trim() || "EUR/USD",
    interval: $("bt-interval").value,
    bars: $("bt-bars").value || "2000",
    strategy: $("bt-strategy").value,
  });
  try {
    const data = await api(`/api/backtest?${params.toString()}`);
    renderBacktest(data);
  } catch (err) {
    $("bt-result").innerHTML = `<p class="muted">Backtest failed: ${esc(err.message)}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Run backtest";
  }
}

/* -------------------------------------------------------------------- boot */
function tickClock() {
  $("clock").textContent = new Date().toLocaleTimeString([], { hour12: false });
}

function boot() {
  tickClock();
  setInterval(tickClock, 1000);

  $("refresh-btn").addEventListener("click", () => { loadScan(true); loadPerf(); });
  $("auto-toggle").addEventListener("change", (e) => { state.auto = e.target.checked; });
  $("bt-form").addEventListener("submit", runBacktest);
  $("modal-close").addEventListener("click", closeModal);
  $("modal").addEventListener("click", (e) => { if (e.target === $("modal")) closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  loadScan(true);
  loadPerf();

  setInterval(() => { if (state.auto) loadScan(); }, 10000);
  setInterval(() => { if (state.auto) loadPerf(); }, 30000);
}

document.addEventListener("DOMContentLoaded", boot);
