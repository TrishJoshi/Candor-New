"""Narrative weather: an animated time-lapse of the narrative map.

Every comment lights up on the 2D narrative map (same UMAP layout as candor.visualize) at the
moment it was posted, as a glow sized by its upvotes, and fades over time, so narratives
swell into bright "clouds" when they draw a burst of engagement and dim as attention moves
on, like a weather-radar loop. Controls: play/pause, scrubber over an activity strip, speed,
trail length, subreddit filter, colour by topic or by author authenticity, and a live
"trending now" list of narratives by upvotes.

Self-contained and separate from the narrative map page: it only reads the data that
candor.visualize.build_report_data() produces. Remove this module (and its callers in
candor/dashboard.py, GET /api/dashboard/timelapse and the toggle in web/dashboard.html)
to drop the feature.

Caveat shown on the page: we only have each comment's final upvote count, so its upvotes
are credited at the moment it was posted.
"""

from __future__ import annotations

import json
from pathlib import Path


def build_timelapse_data(report: dict) -> dict:
    """The subset of a narrative-map report the time-lapse needs (no comment texts)."""
    p = report["points"]
    keep = ("x", "y", "t", "narrative", "topic", "subreddit", "upvotes", "band")
    return {
        "meta": {k: report["meta"].get(k) for k in ("source", "generated", "n_items", "has_upvotes", "has_authenticity")},
        "points": {k: p[k] for k in keep if k in p},
        "narratives": [{k: n.get(k) for k in ("id", "label", "topic", "center", "summary")} for n in report["narratives"]],
        "topics": [{k: t.get(k) for k in ("id", "label", "size")} for t in report["topics"]],
    }


def write_timelapse(report: dict, path: Path) -> Path:
    payload = json.dumps(build_timelapse_data(report), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    path = Path(path)
    path.write_text(_TEMPLATE.replace("__TIMELAPSE_DATA__", payload), encoding="utf-8")
    return path


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Narrative Weather</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --surface-2: #f3f2ee; --ink: #0b0b0b; --ink-2: #52514e;
  --muted: #898781; --border: rgba(11,11,11,0.10); --accent: #2a78d6;
  --stage: #0a0f1e; --stage-ink: #e8ecf6; --stage-muted: #8b93ab;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --surface-2: #232321; --ink: #f0efec; --ink-2: #c3c2b7;
    --muted: #898781; --border: rgba(255,255,255,0.10); --accent: #3987e5; --stage: #05080f;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --surface-2: #232321; --ink: #f0efec; --ink-2: #c3c2b7;
  --muted: #898781; --border: rgba(255,255,255,0.10); --accent: #3987e5; --stage: #05080f;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink); font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
  padding: max(16px, env(safe-area-inset-top)) 16px 32px; }
.wrap { max-width: 1400px; margin: 0 auto; }
header h1 { font-size: 22px; margin: 0 0 2px; font-weight: 650; letter-spacing: -0.01em; }
header p { margin: 0; color: var(--ink-2); max-width: 900px; }
.controls { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; margin: 14px 0 10px; }
.play { width: 42px; height: 42px; border-radius: 50%; border: 0; background: var(--accent); color: #fff; font-size: 16px; cursor: pointer; flex: none; }
.play:focus-visible, select:focus-visible, input:focus-visible, .trend button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.now { font-size: 20px; font-weight: 650; font-variant-numeric: tabular-nums; min-width: 190px; }
.ctl { display: flex; align-items: center; gap: 6px; color: var(--ink-2); font-size: 13px; }
select { padding: 6px 8px; border-radius: 7px; border: 1px solid var(--border); background: var(--surface); color: var(--ink); font: inherit; }
.layout { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 14px; align-items: start; }
@media (max-width: 900px) { .layout { grid-template-columns: minmax(0, 1fr); } }
.stage { position: relative; background: var(--stage); border-radius: 12px; overflow: hidden; }
#sky { display: block; width: 100%; height: min(64vh, 620px); min-height: 340px; }
.legend { position: absolute; left: 10px; bottom: 10px; display: flex; flex-wrap: wrap; gap: 4px 10px; max-width: calc(100% - 20px);
  font-size: 11.5px; color: var(--stage-ink); background: rgba(5,8,15,.55); padding: 6px 9px; border-radius: 8px; }
.legend i { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 4px; vertical-align: -1px; }
.stamp { position: absolute; right: 12px; top: 10px; color: var(--stage-muted); font-size: 12px; text-align: right; pointer-events: none; }
.strip { position: relative; margin-top: 8px; height: 58px; }
#activity { width: 100%; height: 58px; display: block; border-radius: 8px; background: var(--surface-2); }
#scrub { position: absolute; inset: 0; width: 100%; height: 100%; margin: 0; opacity: 0; cursor: ew-resize; }
.axis { display: flex; justify-content: space-between; color: var(--muted); font-size: 11.5px; margin-top: 3px; }
.panel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; }
.panel h2 { font-size: 13px; margin: 0; padding: 12px 14px 8px; color: var(--ink-2); font-weight: 600; text-transform: uppercase; letter-spacing: .04em; border-bottom: 1px solid var(--border); }
.trend { list-style: none; margin: 0; padding: 4px 6px 8px; }
.trend button { all: unset; box-sizing: border-box; display: block; width: 100%; padding: 8px; border-radius: 8px; cursor: pointer; }
.trend button:hover { background: var(--surface-2); }
.trend button[aria-pressed="true"] { background: var(--surface-2); box-shadow: inset 3px 0 0 var(--accent); }
.trend .nm { font-weight: 600; display: flex; gap: 6px; align-items: center; }
.trend .nm i { width: 9px; height: 9px; border-radius: 50%; flex: none; }
.trend .viral { font-size: 10.5px; font-weight: 700; color: #fff; background: #e5484d; border-radius: 8px; padding: 0 6px; margin-left: auto; }
.trend .mt { color: var(--ink-2); font-size: 12px; }
.trend .bar { height: 4px; border-radius: 2px; background: var(--surface-2); margin-top: 5px; overflow: hidden; }
.trend .bar span { display: block; height: 100%; border-radius: 2px; }
.empty { color: var(--muted); padding: 12px 14px; }
.note { color: var(--muted); font-size: 12px; margin: 10px 2px 0; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Narrative weather</h1>
    <p>Watch the conversation move. Every comment lights up where it sits on the narrative map when it's posted, glowing brighter and bigger the more upvotes it got, then fades. Narratives that go viral swell into bright clouds.</p>
  </header>
  <div class="controls">
    <button class="play" id="play" type="button" aria-label="Pause">❚❚</button>
    <div class="now" id="now" aria-live="off"></div>
    <label class="ctl" title="By activity: every comment gets the same screen time, so busy hours play slowly and quiet weeks pass quickly. Clock: real time.">Time <select id="mode"><option value="activity" selected>By activity</option><option value="clock">Clock</option></select></label>
    <label class="ctl">Speed <select id="speed"><option value="0.5">½×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select></label>
    <label class="ctl">Trail <select id="trail"><option value="40">Short</option><option value="12" selected>Medium</option><option value="4">Long</option><option value="0">Cumulative</option></select></label>
    <label class="ctl">Colour <select id="colour"><option value="topic">Topic</option><option value="auth">Authenticity</option></select></label>
    <label class="ctl" id="sub-wrap" hidden>Subreddit <select id="sub"></select></label>
  </div>
  <div class="layout">
    <div>
      <div class="stage">
        <canvas id="sky" role="img" aria-label="Animated map of comments appearing over time, grouped by narrative"></canvas>
        <div class="stamp" id="stamp"></div>
        <div class="legend" id="legend"></div>
      </div>
      <div class="strip">
        <canvas id="activity" aria-hidden="true"></canvas>
        <input type="range" id="scrub" min="0" max="1000" value="0" aria-label="Time">
      </div>
      <div class="axis"><span id="t0"></span><span id="t-mid"></span><span id="t1"></span></div>
      <p class="note" id="note"></p>
    </div>
    <aside class="panel" aria-label="Trending narratives">
      <h2 id="trend-h">Trending now</h2>
      <ol class="trend" id="trend"></ol>
    </aside>
  </div>
</div>
<script type="application/json" id="tl-data">__TIMELAPSE_DATA__</script>
<script>
(function () {
  "use strict";
  const D = JSON.parse(document.getElementById("tl-data").textContent);
  const P = D.points, N = P.x.length;
  const HAS_UP = Array.isArray(P.upvotes), HAS_AUTH = Array.isArray(P.band);
  const narr = new Map(D.narratives.map(n => [n.id, n]));
  const topic = new Map(D.topics.map(t => [t.id, t]));
  const $ = id => document.getElementById(id);
  const fmt = new Intl.NumberFormat(), compact = new Intl.NumberFormat(undefined, {notation: "compact"});
  const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const PALETTE = ["#4f9cf9", "#ff8a4c", "#3ddc97", "#c77dff", "#ffd166", "#ff5d8f", "#4dd4e8", "#a3d65c",
                   "#7c8cff", "#ffb36b", "#5be0c4", "#e58bff"];
  const LEGEND_TOPICS = 8;
  const OTHER = "#7d8597";
  const BAND = {ok: ["#3ddc97", "likely authentic"], warn: ["#ffd166", "uncertain"], bad: ["#ff4d5e", "suspicious"], na: ["#7d8597", "not scored"]};
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ------------------------------------------------------------------ state
  // Playback runs on a progress axis u in [0, 1]. "activity": u = a comment's rank in time, so every
  // comment gets the same screen time; "clock": u is linear in real time. Trails decay in u.
  let mode = "activity", sub = "", colourBy = "topic", trailDiv = 12, speed = 1, playing = !reduced, focus = null;
  let idx = [], ts = [], lo = 0, hi = 1, uNow = 0, before = 0, topicColour = new Map();
  const upR = new Float32Array(N), U = new Float64Array(N);
  const LOOP_S = 45;  // seconds for one pass at 1x

  // sprites: a soft radial glow per colour, drawn scaled with additive blending
  const sprites = new Map();
  function sprite(colour) {
    if (sprites.has(colour)) return sprites.get(colour);
    const c = document.createElement("canvas"); c.width = c.height = 64;
    const g = c.getContext("2d"), gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    gr.addColorStop(0, "rgba(255,255,255,0.55)"); gr.addColorStop(0.12, colour);
    gr.addColorStop(0.55, colour + "55"); gr.addColorStop(1, colour + "00");
    g.fillStyle = gr; g.fillRect(0, 0, 64, 64);
    sprites.set(colour, c); return c;
  }

  // ------------------------------------------------------------ data window
  const subs = new Map();
  P.subreddit.forEach(s => { if (s) subs.set(s, (subs.get(s) || 0) + 1); });
  if (subs.size > 1) {
    $("sub-wrap").hidden = false;
    $("sub").innerHTML = `<option value="">All (${fmt.format(N)})</option>` +
      [...subs].sort((a, b) => b[1] - a[1]).map(([s, c]) => `<option value="${esc(s)}">r/${esc(s)} (${fmt.format(c)})</option>`).join("");
  }
  function prepare() {
    const all = [];
    for (let i = 0; i < N; i++) if (P.t[i] != null && P.narrative[i] !== -1 && (!sub || P.subreddit[i] === sub)) all.push(i);
    all.sort((a, b) => P.t[a] - P.t[b]);
    if (mode === "clock") {
      // the shortest window holding 95% of the comments: a few much older ones would stretch the loop
      const s = all.map(i => P.t[i]), k = Math.max(1, Math.ceil(0.95 * s.length) - 1);
      let best = 0;
      for (let j = 1; j + k < s.length; j++) if (s[j + k] - s[j] < s[best + k] - s[best]) best = j;
      lo = s[best] ?? 0; hi = s[Math.min(best + k, s.length - 1)] ?? 1;
      if (hi - lo < 3600) { lo -= 1800; hi += 1800; }
      const pad = (hi - lo) * 0.02; lo -= pad; hi += pad;
      idx = all.filter(i => P.t[i] >= lo && P.t[i] <= hi);
      for (const i of idx) U[i] = (P.t[i] - lo) / (hi - lo);
    } else {
      idx = all;
      idx.forEach((i, r) => { U[i] = idx.length > 1 ? r / (idx.length - 1) : 0; });
      lo = idx.length ? P.t[idx[0]] : 0; hi = idx.length ? P.t[idx[idx.length - 1]] : 1;
    }
    ts = idx.map(i => P.t[i]);
    before = all.length - idx.length;
    // size by upvotes (square root, capped at the 98th percentile)
    const ups = idx.map(i => HAS_UP ? (P.upvotes[i] || 0) : 0).sort((a, b) => a - b);
    const cap = Math.max(1, ups[Math.floor(0.98 * (ups.length - 1))] || 1);
    for (const i of idx) upR[i] = Math.sqrt(Math.min(HAS_UP ? (P.upvotes[i] || 0) : 0, cap) / cap);
    // topic colours, cycling through the palette by upvotes here; the legend names the top 8
    const tu = new Map();
    for (const i of idx) tu.set(P.topic[i], (tu.get(P.topic[i]) || 0) + (HAS_UP ? (P.upvotes[i] || 0) : 0) + 1);
    topicColour = new Map([...tu].sort((a, b) => b[1] - a[1]).map(([t], j) => [t, PALETTE[j % PALETTE.length]]));
    fitView(); drawActivity(); legend();
    uNow = 0; focus = null;
    $("t0").textContent = when(tAt(0), true); $("t1").textContent = when(tAt(1), true); $("t-mid").textContent = when(tAt(0.5), true);
    $("note").textContent = (mode === "activity"
        ? "Playback follows the conversation: every comment gets the same screen time, so busy hours play slowly and quiet stretches pass quickly (the clock shows real time). "
        : (before ? `${fmt.format(before)} comment(s) outside ${when(lo, true)} – ${when(hi, true)} are left out of the loop. ` : "")) +
      "Upvotes are the final counts Reddit showed when we scraped; each comment's upvotes are credited when it was posted." +
      (HAS_AUTH ? "" : " Authenticity colouring needs author scores.");
  }
  const colourOf = i => colourBy === "auth" && HAS_AUTH ? BAND[P.band[i] || "na"][0] : (topicColour.get(P.topic[i]) || OTHER);
  const trailU = () => trailDiv ? 1 / trailDiv : Infinity;  // trail length on the progress axis
  function tAt(u) {  // real time at progress u
    if (mode === "clock" || ts.length < 2) return lo + u * (hi - lo);
    const x = Math.max(0, Math.min(1, u)) * (ts.length - 1), j = Math.floor(x);
    return j >= ts.length - 1 ? ts[ts.length - 1] : ts[j] + (x - j) * (ts[j + 1] - ts[j]);
  }
  function when(t, short) {
    const d = new Date(t * 1000);
    return short ? d.toLocaleDateString(undefined, {day: "numeric", month: "short"})
      : d.toLocaleString(undefined, {weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit"});
  }
  function human(s) {
    if (!isFinite(s)) return "all so far";
    return s < 3600 ? Math.round(s / 60) + " min" : s < 2 * 86400 ? (s / 3600).toFixed(s < 36000 ? 1 : 0) + " h" : Math.round(s / 86400) + " days";
  }

  // ------------------------------------------------------------------- view
  const sky = $("sky"), ctx = sky.getContext("2d");
  let W = 0, H = 0, dpr = 1, sx = v => v, sy = v => v, ghost = null;
  function fenced(arr) {
    const s = [...arr].sort((a, b) => a - b), q = f => s[Math.floor(f * (s.length - 1))];
    const q1 = q(0.25), q3 = q(0.75), iqr = (q3 - q1) || 1;
    return [Math.max(s[0], q1 - 1.5 * iqr), Math.min(s[s.length - 1], q3 + 1.5 * iqr)];
  }
  function fitView() {
    dpr = devicePixelRatio || 1; W = sky.clientWidth; H = sky.clientHeight;
    sky.width = Math.round(W * dpr); sky.height = Math.round(H * dpr);
    if (!idx.length) return;
    const [x0, x1] = fenced(idx.map(i => P.x[i])), [y0, y1] = fenced(idx.map(i => P.y[i])), m = 36;
    sx = v => m + (v - x0) / ((x1 - x0) || 1) * (W - 2 * m);
    sy = v => H - m - (v - y0) / ((y1 - y0) || 1) * (H - 2 * m);
    // faint "terrain": every comment of the loop, so the geography stays readable
    ghost = document.createElement("canvas"); ghost.width = sky.width; ghost.height = sky.height;
    const g = ghost.getContext("2d"); g.scale(dpr, dpr); g.fillStyle = "rgba(160,175,210,0.13)";
    for (const i of idx) { g.beginPath(); g.arc(sx(P.x[i]), sy(P.y[i]), 1.3, 0, 6.283); g.fill(); }
  }

  function weightOf(age) { const tr = trailU(); return isFinite(tr) ? Math.exp(-age / tr) : 1; }
  function frame() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--stage").trim() || "#0a0f1e";
    ctx.fillRect(0, 0, W, H);
    if (ghost) ctx.drawImage(ghost, 0, 0, W, H);
    ctx.globalCompositeOperation = "lighter";
    const R0 = Math.max(6, Math.min(W, H) / 46), fresh = 1 / 120, score = new Map();
    for (const i of idx) {
      if (U[i] > uNow) break;  // idx is sorted by time
      const age = uNow - U[i], w = weightOf(age);
      if (w < 0.03) continue;
      const pop = age < fresh ? 1 + 0.8 * (1 - age / fresh) : 1;  // new comments pop in
      const r = R0 * (0.45 + 2.6 * upR[i]) * pop;
      const dim = focus !== null && P.narrative[i] !== focus ? 0.12 : 1;
      ctx.globalAlpha = Math.min(1, 0.75 * w * dim);
      ctx.drawImage(sprite(colourOf(i)), sx(P.x[i]) - r, sy(P.y[i]) - r, 2 * r, 2 * r);
      const up = HAS_UP ? (P.upvotes[i] || 0) : 0;
      score.set(P.narrative[i], (score.get(P.narrative[i]) || 0) + w * (up + 1));
    }
    // labels for the brightest narratives right now
    ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
    const top = [...score].sort((a, b) => b[1] - a[1]).slice(0, focus === null ? 5 : 0);
    if (focus !== null && score.has(focus)) top.push([focus, score.get(focus)]);
    const maxS = top.length ? top[0][1] : 1, boxes = [];
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    const legendTop = H - ($("legend").offsetHeight || 0) - 14;
    for (const [id, s] of top) {
      const n = narr.get(id); if (!n || !n.center) continue;
      const size = 11 + 6 * Math.sqrt(s / maxS);
      ctx.font = `600 ${size}px system-ui, -apple-system, "Segoe UI", sans-serif`;
      const w = ctx.measureText(n.label).width, x = Math.max(w / 2 + 6, Math.min(W - w / 2 - 6, sx(n.center[0])));
      let y = Math.min(sy(n.center[1]), legendTop - size / 2);
      // nudge down/up until it doesn't overlap a brighter narrative's label; skip if it can't fit
      let ok = false;
      for (const dy of [0, size + 4, -(size + 4), 2 * (size + 4), -2 * (size + 4)]) {
        const b = {x0: x - w / 2 - 4, x1: x + w / 2 + 4, y0: y + dy - size / 2 - 2, y1: y + dy + size / 2 + 2};
        if (b.y0 < 4 || b.y1 > legendTop) continue;
        if (!boxes.some(o => b.x0 < o.x1 && b.x1 > o.x0 && b.y0 < o.y1 && b.y1 > o.y0)) { boxes.push(b); y += dy; ok = true; break; }
      }
      if (!ok) continue;
      ctx.shadowColor = "rgba(0,0,0,.85)"; ctx.shadowBlur = 6; ctx.fillStyle = "#f4f6fb";
      ctx.fillText(n.label, x, y);
    }
    ctx.shadowBlur = 0;
    $("now").textContent = when(tAt(uNow));
    $("stamp").textContent = isFinite(trailU()) ? `trail: last ${human(tAt(uNow) - tAt(Math.max(0, uNow - trailU())))}` +
      (mode === "activity" ? ` · ${fmt.format(Math.round(trailU() * idx.length))} comments` : "") : "trail: all so far";
    $("scrub").value = String(Math.round(1000 * uNow));
    drawCursor();
  }

  // --------------------------------------------------------- activity strip
  const act = $("activity"), actx = act.getContext("2d");
  let bins = [], actImg = null;
  function drawActivity() {
    const w = act.clientWidth, h = act.clientHeight, d = devicePixelRatio || 1;
    act.width = Math.round(w * d); act.height = Math.round(h * d);
    const nb = Math.max(40, Math.min(160, Math.round(w / 6)));
    bins = new Array(nb).fill(0);
    for (const i of idx) bins[Math.min(nb - 1, Math.floor(U[i] * nb))] += (HAS_UP ? (P.upvotes[i] || 0) : 0) + 1;
    const max = Math.max(1, ...bins);
    actx.setTransform(d, 0, 0, d, 0, 0); actx.clearRect(0, 0, w, h);
    actx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--muted").trim();
    bins.forEach((b, k) => { const bh = (h - 6) * Math.sqrt(b / max); actx.fillRect(k * w / nb + 0.5, h - bh, w / nb - 1, bh); });
    actImg = actx.getImageData(0, 0, act.width, act.height);
  }
  function drawCursor() {
    if (!actImg) return;
    const w = act.clientWidth, h = act.clientHeight, d = devicePixelRatio || 1, x = uNow * w;
    actx.putImageData(actImg, 0, 0);
    actx.setTransform(d, 0, 0, d, 0, 0);
    actx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
    actx.globalAlpha = 0.16; actx.fillRect(0, 0, x, h); actx.globalAlpha = 1;
    actx.fillRect(x - 1, 0, 2, h);
  }

  // ---------------------------------------------------------------- trending
  function trending() {
    const tr = isFinite(trailU()) ? trailU() : 1, now = new Map(), prev = new Map(), total = new Map();
    for (const i of idx) {
      const u = U[i]; if (u > uNow) break;
      const n = P.narrative[i], up = HAS_UP ? (P.upvotes[i] || 0) : 0;
      total.set(n, (total.get(n) || 0) + up);
      if (u > uNow - tr) { const v = now.get(n) || {up: 0, c: 0}; v.up += up; v.c++; now.set(n, v); }
      else if (u > uNow - 2 * tr) prev.set(n, (prev.get(n) || 0) + up);
    }
    const rows = [...now].sort((a, b) => b[1].up - a[1].up || b[1].c - a[1].c).slice(0, 8);
    $("trend-h").textContent = isFinite(trailU()) ? `Trending · last ${human(tAt(uNow) - tAt(Math.max(0, uNow - tr)))}` : "Most upvoted so far";
    const ol = $("trend");
    if (!rows.length) { ol.innerHTML = `<li class="empty">Nothing posted yet in this window. Press play.</li>`; return; }
    const max = Math.max(1, rows[0][1].up);
    ol.innerHTML = rows.map(([id, v]) => {
      const n = narr.get(id), t = n && topic.get(n.topic), c = colourBy === "auth" ? "#8b93ab" : (topicColour.get(n ? n.topic : -1) || OTHER);
      const p = prev.get(id) || 0, viral = isFinite(trailU()) && v.up >= 100 && v.up > 4 * (p + 20);
      return `<li><button type="button" data-n="${id}" aria-pressed="${focus === id}">
        <span class="nm"><i style="background:${c}"></i>${esc(n ? n.label : "?")}${viral ? `<span class="viral" title="Upvotes in this window are more than 4× the window before">↑ surging</span>` : ""}</span>
        <span class="mt">▲ ${compact.format(v.up)} upvotes · ${v.c} comment${v.c > 1 ? "s" : ""}${isFinite(trailU()) ? ` · ${compact.format(total.get(id) || 0)} so far` : ""}${t ? ` · ${esc(t.label)}` : ""}</span>
        <div class="bar"><span style="width:${Math.max(3, 100 * v.up / max)}%;background:${c}"></span></div></button></li>`;
    }).join("");
  }
  $("trend").addEventListener("click", e => {
    const b = e.target.closest("[data-n]"); if (!b) return;
    const id = +b.dataset.n; focus = focus === id ? null : id; trending(); if (!playing) frame();
  });

  function legend() {
    const items = colourBy === "auth" && HAS_AUTH ? Object.values(BAND)
      : [...topicColour].slice(0, LEGEND_TOPICS).map(([t, c]) => [c, (topic.get(t) || {label: "?"}).label])
          .concat(topicColour.size > LEGEND_TOPICS ? [[OTHER, `+${topicColour.size - LEGEND_TOPICS} more topics (colours repeat)`]] : []);
    $("legend").innerHTML = items.map(([c, l]) => `<span><i style="background:${c}"></i>${esc(l)}</span>`).join("");
  }

  // ---------------------------------------------------------------- playback
  let last = null, lastTrend = 0, holdUntil = 0;
  function tick(clock) {
    if (playing) {
      if (last !== null) {
        if (uNow >= 1) { if (clock > holdUntil) uNow = 0; }  // hold the last frame briefly, then loop
        else {
          uNow += (clock - last) / 1000 * speed / LOOP_S;
          if (uNow >= 1) { uNow = 1; holdUntil = clock + 1800; }
        }
      }
      last = clock;
      frame();
      if (clock - lastTrend > 300) { trending(); lastTrend = clock; }
    } else last = null;
    requestAnimationFrame(tick);
  }
  function setPlaying(p) {
    playing = p; $("play").textContent = p ? "❚❚" : "▶"; $("play").setAttribute("aria-label", p ? "Pause" : "Play");
    if (p && uNow >= 1) uNow = 0;
  }
  $("play").addEventListener("click", () => setPlaying(!playing));
  addEventListener("keydown", e => { if (e.code === "Space" && !e.target.closest("select,input,button")) { e.preventDefault(); setPlaying(!playing); } });
  $("scrub").addEventListener("input", e => { uNow = +e.target.value / 1000; frame(); trending(); });
  $("mode").addEventListener("change", e => { mode = e.target.value; prepare(); frame(); trending(); });
  $("speed").addEventListener("change", e => { speed = +e.target.value; });
  $("trail").addEventListener("change", e => { trailDiv = +e.target.value; frame(); trending(); });
  $("colour").addEventListener("change", e => { colourBy = e.target.value; legend(); frame(); trending(); });
  $("sub").addEventListener("change", e => { sub = e.target.value; prepare(); frame(); trending(); });
  if (!HAS_AUTH) $("colour").querySelector('[value="auth"]').disabled = true;
  let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { fitView(); drawActivity(); frame(); }, 150); });

  prepare();
  if (!playing) { uNow = 1; setPlaying(false); }  // reduced motion: show the end state, still scrubbable
  frame(); trending();
  requestAnimationFrame(tick);
})();
</script>
</body>
</html>
"""
