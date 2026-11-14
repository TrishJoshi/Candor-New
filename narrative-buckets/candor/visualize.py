"""Stage 2 report: an interactive, self-contained HTML page.

* **Narrative map** — every comment projected to 2D (UMAP on the sentence
  embeddings, as in BERTopic's ``visualize_documents``). One topic is
  emphasised at a time; its narratives are labelled at their centres.
* **Topic list** — ranked by size, with the subreddit mix of each topic.
* **Word clouds** — c-TF-IDF keywords (BERTopic's topic representation) for the
  selected topic and each of its narratives, with representative comments.

The page loads Plotly from cdnjs; everything else (data, word-cloud layout) is
inlined, so the file can be opened locally or hosted as-is.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from candor.schemas import NOISE_ID, ProcessingResult
from candor.spatial_engine import SpatialEngineConfig

logger = logging.getLogger(__name__)

PLOTLY_URL = "https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.35.3/plotly-gl2d.min.js"
_MAX_HOVER_CHARS = 320


def project_2d(embeddings: np.ndarray, random_state: int | None = 42) -> np.ndarray:
    """2D UMAP layout for display (separate from the 5D clustering space)."""
    import umap

    n = len(embeddings)
    if n < 4:
        return np.zeros((n, 2))
    return umap.UMAP(
        n_neighbors=min(15, n - 1),
        n_components=2,
        min_dist=0.1,
        metric="cosine",
        random_state=random_state,
        n_jobs=1 if random_state is not None else -1,
        compatibility_layout=True,
    ).fit_transform(embeddings)


def build_report_data(
    result: ProcessingResult,
    embeddings: np.ndarray,
    config: SpatialEngineConfig,
    source: str = "",
    labels_source: str = "",
) -> dict:
    """Everything the page needs, as plain JSON-serialisable data."""
    keep = [i for i, c in enumerate(result.comments) if c.narrative_id is not None and c.text.strip()]
    coords = project_2d(embeddings[keep], config.random_state)
    comments = [result.comments[i] for i in keep]
    by_id = {c.comment_id: c for c in result.comments}

    narr = np.array([c.narrative_id for c in comments])
    centers = {
        n.narrative_id: np.median(coords[narr == n.narrative_id], axis=0).round(3).tolist()
        for n in result.narratives
    }
    subs_by_topic: dict[int, Counter] = {}
    for c in comments:
        if c.topic_id != NOISE_ID:
            subs_by_topic.setdefault(c.topic_id, Counter())[getattr(c, "subreddit", None) or "?"] += 1

    noise = int((narr == NOISE_ID).sum())
    return {
        "meta": {
            "source": source,
            "labels": labels_source,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "n_items": len(comments),
            "n_noise": noise,
            "params": {
                "embedder": config.embedding_backend,
                "min_cluster_size": config.hdbscan_min_cluster_size,
                "selection": config.hdbscan_selection_method,
                "topic_method": config.topic_method,
                "topic_threshold": (
                    config.resolved_topic_threshold()
                    if config.topic_method == "agglomerative"
                    else config.topic_eps
                ),
            },
        },
        "points": {
            "x": coords[:, 0].round(3).tolist(),
            "y": coords[:, 1].round(3).tolist(),
            "narrative": narr.tolist(),
            "topic": [c.topic_id for c in comments],
            "author": [c.author for c in comments],
            "subreddit": [getattr(c, "subreddit", None) or "" for c in comments],
            "kind": [getattr(c, "kind", None) or "comment" for c in comments],
            "text": [_truncate(c.text, _MAX_HOVER_CHARS) for c in comments],
        },
        "topics": [
            {
                "id": t.topic_id,
                "label": t.label,
                "size": t.size,
                "narratives": t.narrative_ids,
                "keywords": [[k.word, k.weight] for k in t.keywords],
                "subreddits": subs_by_topic.get(t.topic_id, Counter()).most_common(4),
            }
            for t in sorted(result.topics, key=lambda t: -t.size)
        ],
        "narratives": [
            {
                "id": n.narrative_id,
                "label": n.label,
                "topic": n.topic_id,
                "size": n.size,
                "center": centers[n.narrative_id],
                "keywords": [[k.word, k.weight] for k in n.keywords],
                "examples": [
                    {"author": by_id[cid].author, "text": _truncate(by_id[cid].text, 240)}
                    for cid in n.representative_comment_ids[:3]
                ],
            }
            for n in result.narratives
        ],
    }


def write_report(
    result: ProcessingResult,
    embeddings: np.ndarray,
    path: Path,
    config: SpatialEngineConfig | None = None,
    source: str = "",
    labels_source: str = "",
) -> Path:
    """``labels_source`` names where labels came from (e.g. the LLM model), shown in the subtitle."""
    data = build_report_data(
        result, embeddings, config or SpatialEngineConfig(), source, labels_source
    )
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = _TEMPLATE.replace("__PLOTLY_URL__", PLOTLY_URL).replace("__CANDOR_DATA__", payload)
    path = Path(path)
    path.write_text(html, encoding="utf-8")
    return path


def _truncate(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Narrative Map</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7;
  --surface: #fcfcfb;
  --surface-2: #f3f2ee;
  --ink: #0b0b0b;
  --ink-2: #52514e;
  --muted: #898781;
  --grid: #e1e0d9;
  --border: rgba(11,11,11,0.10);
  --series-1: #2a78d6;
  --series-2: #eb6834;
  --context: #b9b7af;
  --noise: #d6d4cc;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --surface-2: #232321;
    --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781; --grid: #2c2c2a;
    --border: rgba(255,255,255,0.10);
    --series-1: #3987e5; --series-2: #d95926; --context: #55544f; --noise: #343431;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --surface-2: #232321;
  --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781; --grid: #2c2c2a;
  --border: rgba(255,255,255,0.10);
  --series-1: #3987e5; --series-2: #d95926; --context: #55544f; --noise: #343431;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--page); color: var(--ink);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
  padding: max(20px, env(safe-area-inset-top)) 20px 40px;
}
.wrap { max-width: 1400px; margin: 0 auto; }
header h1 { font-size: 22px; margin: 0 0 2px; font-weight: 650; letter-spacing: -0.01em; }
header p { margin: 0; color: var(--ink-2); }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 18px 0; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
.tile .v { font-size: 24px; font-weight: 650; }
.tile .k { color: var(--ink-2); font-size: 12px; }
.layout { display: grid; grid-template-columns: minmax(260px, 320px) 1fr; gap: 16px; align-items: start; }
@media (max-width: 860px) { .layout { grid-template-columns: 1fr; } }
.panel { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; }
aside.panel { position: sticky; top: max(12px, env(safe-area-inset-top)); max-height: calc(100vh - 24px); display: flex; flex-direction: column; }
@media (max-width: 860px) { aside.panel { position: static; max-height: 360px; } }
.panel-head { padding: 12px 14px 8px; border-bottom: 1px solid var(--border); }
.panel-head h2 { font-size: 13px; margin: 0 0 8px; color: var(--ink-2); font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
input[type=search] {
  width: 100%; padding: 7px 10px; border-radius: 7px; border: 1px solid var(--border);
  background: var(--surface-2); color: var(--ink); font: inherit;
}
#topic-list { list-style: none; margin: 0; padding: 6px; overflow-y: auto; }
#topic-list li button {
  all: unset; display: block; width: 100%; box-sizing: border-box; cursor: pointer;
  padding: 8px 10px; border-radius: 7px;
}
#topic-list li button:hover { background: var(--surface-2); }
#topic-list li button[aria-pressed="true"] { background: var(--surface-2); box-shadow: inset 3px 0 0 var(--series-1); }
#topic-list li button:focus-visible { outline: 2px solid var(--series-1); outline-offset: -2px; }
.t-label { font-weight: 600; display: block; }
.t-meta { color: var(--ink-2); font-size: 12px; display: block; }
.t-bar { height: 3px; border-radius: 2px; background: var(--series-1); margin-top: 6px; opacity: .85; }
.map-head { display: flex; flex-wrap: wrap; gap: 8px 18px; align-items: baseline; padding: 12px 14px; border-bottom: 1px solid var(--border); }
.map-head h2 { margin: 0; font-size: 17px; }
.map-head .sub { color: var(--ink-2); }
.legend { display: flex; flex-wrap: wrap; gap: 6px 14px; color: var(--ink-2); font-size: 12px; margin-left: auto; align-items: center; }
.legend i { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 5px; vertical-align: -1px; }
.legend label { cursor: pointer; }
#map { width: 100%; height: min(68vh, 640px); min-height: 360px; }
.hint { color: var(--muted); font-size: 12px; padding: 0 14px 10px; margin: 0; }
h3.section { font-size: 13px; color: var(--ink-2); text-transform: uppercase; letter-spacing: .04em; margin: 22px 2px 10px; font-weight: 600; }
.clouds { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; position: relative; }
.card.topic-card { grid-column: 1 / -1; }
.card h4 { margin: 0; font-size: 15px; }
.card .meta { color: var(--ink-2); font-size: 12px; margin-bottom: 6px; }
.card canvas { width: 100%; height: 190px; display: block; }
.card.topic-card canvas { height: 220px; }
.card.hot { box-shadow: 0 0 0 2px var(--series-2); }
.examples { margin: 8px 0 0; padding: 0; list-style: none; border-top: 1px solid var(--border); }
.examples li { padding: 7px 0 0; color: var(--ink-2); font-size: 12.5px; }
.examples b { color: var(--ink); font-weight: 600; }
.tip {
  position: fixed; pointer-events: none; z-index: 10; background: var(--surface); color: var(--ink);
  border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; font-size: 12px;
  box-shadow: 0 2px 8px rgba(0,0,0,.12);
}
.empty { color: var(--muted); padding: 16px; }
</style>
<script src="__PLOTLY_URL__"></script>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Narrative Map</h1>
    <p id="subtitle"></p>
  </header>
  <section class="tiles" id="tiles"></section>
  <div class="layout">
    <aside class="panel" aria-label="Topics">
      <div class="panel-head">
        <h2>Topics</h2>
        <input type="search" id="topic-search" placeholder="Filter topics or keywords…" aria-label="Filter topics">
      </div>
      <ol id="topic-list"></ol>
    </aside>
    <main>
      <section class="panel">
        <div class="map-head">
          <h2 id="sel-title"></h2><span class="sub" id="sel-sub"></span>
          <div class="legend">
            <span><i style="background:var(--series-1)"></i>Selected topic</span>
            <span><i style="background:var(--series-2)"></i>Hovered narrative</span>
            <span><i style="background:var(--context)"></i>Other topics</span>
            <label><input type="checkbox" id="show-noise"> Noise</label>
            <label><input type="checkbox" id="zoom-topic"> Zoom to topic</label>
          </div>
        </div>
        <div id="map" role="img" aria-label="2D map of all comments, coloured by the selected topic"></div>
        <p class="hint">Each dot is a post or comment, placed by meaning (UMAP of sentence embeddings). Click a dot to select its topic; scroll to zoom, double-click to reset.</p>
      </section>
      <h3 class="section">Word clouds <span style="text-transform:none;letter-spacing:0;font-weight:400">· c-TF-IDF keywords, larger = more distinctive</span></h3>
      <div class="clouds" id="clouds"></div>
    </main>
  </div>
</div>
<div class="tip" id="tip" hidden></div>
<script type="application/json" id="candor-data">__CANDOR_DATA__</script>
<script>
(function () {
  "use strict";
  const DATA = JSON.parse(document.getElementById("candor-data").textContent);
  const P = DATA.points, N = P.x.length;
  const narrById = new Map(DATA.narratives.map(n => [n.id, n]));
  const topicById = new Map(DATA.topics.map(t => [t.id, t]));
  const fmt = new Intl.NumberFormat();
  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e; };

  let selected = DATA.topics.length ? DATA.topics[0].id : null;
  let hovered = null;
  let showNoise = false;

  // ---------------------------------------------------------------- header
  const m = DATA.meta;
  document.getElementById("subtitle").textContent =
    `${m.source || "dataset"} · generated ${m.generated} · embeddings: ${m.params.embedder} · HDBSCAN ${m.params.selection}, min cluster ${m.params.min_cluster_size} · topics: ${m.params.topic_method} @ ${m.params.topic_threshold}` +
    (m.labels ? ` · labels: ${m.labels}` : "");
  const tiles = [
    [fmt.format(m.n_items), "posts & comments"],
    [fmt.format(DATA.topics.length), "topics"],
    [fmt.format(DATA.narratives.length), "narratives"],
    [Math.round(100 * m.n_noise / Math.max(m.n_items, 1)) + "%", "unclustered (noise)"],
  ];
  document.getElementById("tiles").append(...tiles.map(([v, k]) =>
    el("div", {className: "tile"}, el("div", {className: "v", textContent: v}), el("div", {className: "k", textContent: k}))));

  // ------------------------------------------------------------ topic list
  const list = document.getElementById("topic-list");
  const maxSize = Math.max(1, ...DATA.topics.map(t => t.size));
  function renderTopicList(filter = "") {
    const q = filter.trim().toLowerCase();
    list.replaceChildren();
    const shown = DATA.topics.filter(t => !q || t.label.toLowerCase().includes(q) ||
      t.keywords.some(([w]) => w.includes(q)));
    if (!shown.length) { list.append(el("li", {className: "empty", textContent: "No matching topics"})); return; }
    for (const t of shown) {
      const subs = t.subreddits.map(([s, c]) => `r/${s} ${Math.round(100 * c / t.size)}%`).join(" · ");
      const btn = el("button", {type: "button"},
        el("span", {className: "t-label", textContent: t.label}),
        el("span", {className: "t-meta", textContent: `${fmt.format(t.size)} items · ${t.narratives.length} narrative${t.narratives.length > 1 ? "s" : ""}`}),
        el("span", {className: "t-meta", textContent: subs}),
        el("div", {className: "t-bar", style: `width:${Math.max(2, 100 * t.size / maxSize)}%`}));
      btn.setAttribute("aria-pressed", String(t.id === selected));
      btn.addEventListener("click", () => select(t.id));
      list.append(el("li", {}, btn));
    }
  }
  document.getElementById("topic-search").addEventListener("input", e => renderTopicList(e.target.value));

  // ------------------------------------------------------------------- map
  const hoverText = new Array(N);
  for (let i = 0; i < N; i++) {
    const n = narrById.get(P.narrative[i]);
    const words = esc(P.text[i]).replace(/(.{1,70})(\s|$)/g, "$1<br>");
    const who = `${P.kind[i] === "post" ? "post" : "comment"} by u/${esc(P.author[i])}${P.subreddit[i] ? " · r/" + esc(P.subreddit[i]) : ""}`;
    const head = n ? `<b>${esc(n.label)}</b><br>topic: ${esc(topicById.get(n.topic).label)}` : "<b>Noise</b> (unclustered)";
    hoverText[i] = `${head}<br><span style="font-size:11px">${who}</span><br><br>${words}`;
  }
  const idxBy = pred => { const out = []; for (let i = 0; i < N; i++) if (pred(i)) out.push(i); return out; };
  const pick = (arr, idx) => idx.map(i => arr[i]);

  function traces() {
    const noise = idxBy(i => P.narrative[i] === -1);
    const other = idxBy(i => P.narrative[i] !== -1 && P.topic[i] !== selected);
    const sel = idxBy(i => P.topic[i] === selected && P.narrative[i] !== hovered);
    const hot = hovered === null ? [] : idxBy(i => P.narrative[i] === hovered);
    const mk = (idx, name, color, size, opacity) => ({
      type: "scattergl", mode: "markers", name, x: pick(P.x, idx), y: pick(P.y, idx),
      text: pick(hoverText, idx), customdata: idx, hovertemplate: "%{text}<extra></extra>",
      marker: {color, size, opacity, line: {width: 0}},
    });
    return [
      mk(showNoise ? noise : [], "noise", css("--noise"), 4, 0.7),
      mk(other, "other", css("--context"), 4.5, 0.75),
      mk(sel, "selected", css("--series-1"), 7, 0.9),
      mk(hot, "hovered", css("--series-2"), 8, 1),
    ];
  }
  // View framing uses Tukey fences (IQR-based) so far-off UMAP islands, e.g.
  // copy-pasted bot boilerplate, don't squash the main cloud. Zooming out
  // (or the modebar's autoscale) still reaches them.
  const quantile = (s, q) => s[Math.floor(q * (s.length - 1))];
  const fenced = (arr, k, padFrac) => {
    if (!arr.length) return undefined;
    const s = [...arr].sort((a, b) => a - b);
    const q1 = quantile(s, 0.25), q3 = quantile(s, 0.75), iqr = (q3 - q1) || 1;
    const lo = Math.max(s[0], q1 - k * iqr), hi = Math.min(s[s.length - 1], q3 + k * iqr);
    const pad = padFrac * ((hi - lo) || 1);
    return [lo - pad, hi + pad];
  };
  const fullX = fenced(P.x, 1.5, 0.04), fullY = fenced(P.y, 1.5, 0.04);
  let zoomToTopic = false;
  function viewRanges() {
    if (!zoomToTopic) return [fullX, fullY];
    const idx = idxBy(i => P.topic[i] === selected);
    if (!idx.length) return [fullX, fullY];
    return [fenced(pick(P.x, idx), 1.0, 0.18), fenced(pick(P.y, idx), 1.0, 0.18)];
  }
  const MAX_MAP_LABELS = 6;  // more than this piles up; the cards list every narrative

  function layout() {
    const t = topicById.get(selected);
    const labelled = (t ? [...t.narratives] : [])
      .sort((a, b) => narrById.get(b).size - narrById.get(a).size).slice(0, MAX_MAP_LABELS);
    const annotations = labelled.map(id => {
      const n = narrById.get(id);
      return {
        x: n.center[0], y: n.center[1], text: esc(n.label), showarrow: false,
        font: {size: 12, color: css("--ink")}, bgcolor: css("--surface"), bordercolor: css("--border"),
        borderpad: 3, borderwidth: 1, opacity: 0.92,
      };
    });
    return {
      margin: {l: 8, r: 8, t: 8, b: 8}, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      xaxis: {visible: false, range: viewRanges()[0]}, yaxis: {visible: false, range: viewRanges()[1]},
      showlegend: false, hovermode: "closest", dragmode: "pan", annotations,
      hoverlabel: {bgcolor: css("--surface"), bordercolor: css("--border"), font: {color: css("--ink"), size: 12}, align: "left"},
      // Keep the user's pan/zoom across hovers; reframe on topic or zoom-mode change.
      uirevision: zoomToTopic ? "topic-" + selected : "all",
    };
  }
  const mapEl = document.getElementById("map");
  const plotConfig = {responsive: true, scrollZoom: true, displaylogo: false,
    modeBarButtonsToRemove: ["select2d", "lasso2d", "toImage"]};
  function drawMap() {
    if (!window.Plotly) { mapEl.replaceChildren(el("p", {className: "empty", textContent: "Plotly failed to load (offline?). Topic list and word clouds still work."})); return; }
    Plotly.react(mapEl, traces(), layout(), plotConfig);
  }

  // ----------------------------------------------------------- word clouds
  function layoutCloud(canvas, words) {
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.clientWidth, H = canvas.clientHeight;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    canvas._boxes = [];
    if (!words.length) return;
    const hi = words[0][1], lo = words[words.length - 1][1];
    const maxF = Math.min(42, H / 4.2), minF = 11;
    const ink = css("--ink"), ink2 = css("--ink-2");
    const placed = [];
    words.forEach(([word, weight], rank) => {
      const f = minF + (maxF - minF) * Math.sqrt((weight - lo) / ((hi - lo) || 1));
      ctx.font = `${rank < 3 ? 650 : 500} ${f}px system-ui, -apple-system, "Segoe UI", sans-serif`;
      const w = ctx.measureText(word).width, h = f * 1.05;
      for (let s = 0; s < 1600; s++) {
        const a = s * 0.21, r = 1.6 * a;
        const x = W / 2 + r * Math.cos(a) * 1.7 - w / 2, y = H / 2 + r * Math.sin(a) - h / 2;
        if (x < 2 || y < 2 || x + w > W - 2 || y + h > H - 2) continue;
        if (placed.some(b => x < b.x + b.w + 4 && x + w + 4 > b.x && y < b.y + b.h && y + h > b.y)) continue;
        const box = {x, y, w, h, word, weight};
        placed.push(box);
        ctx.fillStyle = rank < 5 ? ink : ink2;
        ctx.textBaseline = "top";
        ctx.fillText(word, x, y + f * 0.05);
        break;
      }
    });
    canvas._boxes = placed;
  }
  const tip = document.getElementById("tip");
  function attachCloudTip(canvas) {
    canvas.addEventListener("mousemove", e => {
      const r = canvas.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
      const b = (canvas._boxes || []).find(b => x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h);
      if (!b) { tip.hidden = true; return; }
      tip.textContent = `${b.word} · weight ${b.weight.toFixed(4)}`;
      tip.style.left = (e.clientX + 12) + "px"; tip.style.top = (e.clientY + 12) + "px";
      tip.hidden = false;
    });
    canvas.addEventListener("mouseleave", () => { tip.hidden = true; });
  }
  function cloudCard(title, meta, words, examples, cls) {
    const canvas = el("canvas", {role: "img", ariaLabel: `Word cloud: ${words.slice(0, 10).map(w => w[0]).join(", ")}`});
    const card = el("div", {className: "card " + (cls || "")},
      el("h4", {textContent: title}), el("div", {className: "meta", textContent: meta}), canvas);
    if (examples && examples.length) {
      card.append(el("ul", {className: "examples"}, ...examples.map(ex =>
        el("li", {}, el("b", {textContent: "u/" + ex.author + ": "}), ex.text))));
    }
    canvas._words = words;
    attachCloudTip(canvas);
    return card;
  }
  const cloudsEl = document.getElementById("clouds");
  function drawClouds() {
    cloudsEl.replaceChildren();
    const t = topicById.get(selected);
    if (!t) { cloudsEl.append(el("p", {className: "empty", textContent: "No topics found."})); return; }
    cloudsEl.append(cloudCard(`Topic: ${t.label}`, `${fmt.format(t.size)} items across ${t.narratives.length} narrative(s)`, t.keywords, null, "topic-card"));
    for (const id of [...t.narratives].sort((a, b) => narrById.get(b).size - narrById.get(a).size)) {
      const n = narrById.get(id);
      const card = cloudCard(n.label, `Narrative ${n.id} · ${fmt.format(n.size)} items`, n.keywords, n.examples);
      card.addEventListener("mouseenter", () => setHovered(n.id, card));
      card.addEventListener("mouseleave", () => setHovered(null, null));
      cloudsEl.append(card);
    }
    redrawCanvases();
  }
  function redrawCanvases() { cloudsEl.querySelectorAll("canvas").forEach(c => layoutCloud(c, c._words)); }

  // ------------------------------------------------------------- behaviour
  function setHovered(id, card) {
    hovered = id;
    cloudsEl.querySelectorAll(".card.hot").forEach(c => c.classList.remove("hot"));
    if (card) card.classList.add("hot");
    if (window.Plotly) Plotly.react(mapEl, traces(), layout(), plotConfig);
  }
  function select(id) {
    selected = id; hovered = null;
    const t = topicById.get(id);
    document.getElementById("sel-title").textContent = t ? t.label : "—";
    document.getElementById("sel-sub").textContent = t ? `${fmt.format(t.size)} items · ${t.narratives.length} narrative(s)` : "";
    renderTopicList(document.getElementById("topic-search").value);
    drawMap(); drawClouds();
  }
  document.getElementById("show-noise").addEventListener("change", e => { showNoise = e.target.checked; drawMap(); });
  document.getElementById("zoom-topic").addEventListener("change", e => { zoomToTopic = e.target.checked; drawMap(); });

  select(selected);
  if (window.Plotly) {
    mapEl.on("plotly_click", ev => {
      const i = ev.points[0] && ev.points[0].customdata;
      if (i != null && P.topic[i] !== -1 && P.topic[i] !== selected) select(P.topic[i]);
    });
  }
  // Re-theme on OS or viewer theme change; relayout clouds on resize.
  const rerender = () => { drawMap(); redrawCanvases(); };
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", rerender);
  new MutationObserver(rerender).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
  let rt; window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(redrawCanvases, 150); });
})();
</script>
</body>
</html>
"""
