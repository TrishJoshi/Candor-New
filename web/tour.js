/* Candor guided tour: a step-by-step walkthrough that spotlights parts of the page.

   CandorTour.setup({key, steps, auto})
     key    localStorage key; the tour auto-opens once per browser for each key
     steps  () => [{sel?, title, body (HTML), action?: {label, href}, before?: fn}]
            steps whose `sel` matches nothing are skipped (a step without `sel` is a centred card)
     auto   open now if this browser hasn't seen it (default true); `?tour=1` always opens it
   Any element with [data-tour-start] (the "Tutorial" button in the nav) reopens the tour.
*/
(function () {
  "use strict";
  const CSS = `
.tour-btn{font:inherit;font-size:13px;font-weight:600;background:transparent;color:inherit;border:1px solid currentColor;border-radius:999px;padding:4px 11px;cursor:pointer;opacity:.85;white-space:nowrap}
.tour-btn:hover{opacity:1}
.ct-layer{position:fixed;inset:0;z-index:1000}
.ct-shade{position:fixed;inset:0;background:rgba(10,14,30,.55)}
.ct-spot{position:fixed;border-radius:10px;box-shadow:0 0 0 9999px rgba(10,14,30,.55),0 0 0 3px #3662e3;pointer-events:none;transition:all .25s ease}
.ct-card{position:fixed;width:min(380px,calc(100vw - 32px));background:#fff;color:#14182b;border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.3);padding:16px 18px 14px;font:14px/1.5 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;transition:top .25s ease,left .25s ease}
.ct-card h3{margin:0 0 6px;font-size:16px;font-weight:750;letter-spacing:-.01em}
.ct-card p{margin:0 0 8px}
.ct-card ul,.ct-card ol{margin:0 0 8px;padding-left:18px}
.ct-card li{margin:2px 0}
.ct-card .ct-step{font-size:12px;color:#5d6478;font-weight:600;margin-bottom:4px}
.ct-card .ct-dots{display:flex;gap:4px;margin:10px 0 0}
.ct-card .ct-dots i{width:6px;height:6px;border-radius:50%;background:#dfe3ec}
.ct-card .ct-dots i.on{background:#3662e3}
.ct-card .ct-btns{display:flex;gap:8px;align-items:center;margin-top:12px;flex-wrap:wrap}
.ct-card button,.ct-card a.ct-go{font:600 13px/1 Inter,system-ui,sans-serif;border-radius:9px;padding:9px 13px;cursor:pointer;border:1px solid #dfe3ec;background:#fff;color:#14182b;text-decoration:none}
.ct-card .ct-next,.ct-card a.ct-go{background:#3662e3;border-color:#3662e3;color:#fff}
.ct-card .ct-skip{border:0;background:none;color:#5d6478;padding-left:0;margin-right:auto}
.ct-card :focus-visible{outline:2px solid #3662e3;outline-offset:2px}
.ct-card .k{display:inline-block;padding:0 6px;border-radius:6px;font-weight:700;font-size:12px}
@media (max-width:600px){.ct-card{left:16px!important;right:16px;bottom:16px;top:auto!important;width:auto}}
@media (prefers-reduced-motion:reduce){.ct-spot,.ct-card{transition:none}}`;
  const ls = {
    get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private mode: the tour just reopens */ } },
  };
  let cfg = null, steps = [], i = 0, ui = null, target = null, lastFocus = null;

  function visible(el) { return !!el && el.getClientRects().length > 0; }
  function find(sel) { return sel ? [...document.querySelectorAll(sel)].find(visible) || null : null; }

  function buildUI() {
    if (!document.getElementById("ct-style")) {
      const st = document.createElement("style"); st.id = "ct-style"; st.textContent = CSS; document.head.append(st);
    }
    const layer = document.createElement("div"); layer.className = "ct-layer";
    layer.innerHTML = `<div class="ct-shade"></div><div class="ct-spot" hidden></div>
      <div class="ct-card" role="dialog" aria-modal="true" aria-labelledby="ct-title"></div>`;
    document.body.append(layer);
    return {layer, shade: layer.querySelector(".ct-shade"), spot: layer.querySelector(".ct-spot"), card: layer.querySelector(".ct-card")};
  }

  function place() {
    if (!ui) return;
    const {spot, shade, card} = ui;
    const cw = card.offsetWidth, ch = card.offsetHeight, M = 16;
    if (!target || !visible(target)) {
      spot.hidden = true; shade.style.display = "";
      card.style.left = Math.max(M, (innerWidth - cw) / 2) + "px";
      card.style.top = Math.max(M, (innerHeight - ch) / 2) + "px";
      return;
    }
    const r = target.getBoundingClientRect(), pad = 6;
    shade.style.display = "none"; spot.hidden = false;
    Object.assign(spot.style, {left: r.left - pad + "px", top: r.top - pad + "px", width: r.width + 2 * pad + "px", height: r.height + 2 * pad + "px"});
    let top, left = Math.min(Math.max(M, r.left), innerWidth - cw - M);
    if (r.bottom + pad + 12 + ch < innerHeight - M) top = r.bottom + pad + 12;
    else if (r.top - pad - 12 - ch > M) top = r.top - pad - 12 - ch;
    else if (r.right + 12 + cw < innerWidth - M) { left = r.right + 12; top = Math.max(M, Math.min(r.top, innerHeight - ch - M)); }
    else if (r.left - 12 - cw > M) { left = r.left - 12 - cw; top = Math.max(M, Math.min(r.top, innerHeight - ch - M)); }
    else top = innerHeight - ch - M;  // target fills the screen: dock the card at the bottom
    card.style.left = left + "px"; card.style.top = top + "px";
  }

  function show(n) {
    i = n;
    const s = steps[i];
    if (s.before) { try { s.before(); } catch (_) { /* a missing element just skips the setup */ } }
    target = find(s.sel);
    if (target) target.scrollIntoView({block: "center", inline: "nearest"});
    const last = i === steps.length - 1;
    const dots = steps.map((_, k) => `<i class="${k === i ? "on" : ""}"></i>`).join("");
    ui.card.innerHTML = `<div class="ct-step">Step ${i + 1} of ${steps.length}</div>
      <h3 id="ct-title">${s.title}</h3><div>${s.body}</div>
      <div class="ct-dots" aria-hidden="true">${dots}</div>
      <div class="ct-btns"><button class="ct-skip" type="button">${last ? "Close" : "Skip tour"}</button>
        ${i > 0 ? `<button class="ct-back" type="button">Back</button>` : ""}
        ${s.action ? `<a class="ct-go" href="${s.action.href}">${s.action.label}</a>` : ""}
        ${!s.action || !last ? `<button class="ct-next" type="button">${last ? "Finish" : "Next"}</button>` : ""}</div>`;
    ui.card.querySelector(".ct-skip").onclick = close;
    const back = ui.card.querySelector(".ct-back"); if (back) back.onclick = () => show(i - 1);
    const next = ui.card.querySelector(".ct-next"); if (next) next.onclick = () => (last ? close() : show(i + 1));
    const go = ui.card.querySelector(".ct-go"); if (go) go.onclick = () => ls.set(cfg.key, "done");
    requestAnimationFrame(place);
    (ui.card.querySelector(".ct-next,.ct-go") || ui.card.querySelector("button")).focus({preventScroll: true});
  }

  function onKey(e) {
    if (!ui) return;
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "ArrowRight" && i < steps.length - 1) show(i + 1);
    else if (e.key === "ArrowLeft" && i > 0) show(i - 1);
    else if (e.key === "Tab") {  // keep focus inside the card
      const f = [...ui.card.querySelectorAll("button,a[href]")];
      if (!f.length) return;
      const a = f[0], z = f[f.length - 1];
      if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
      else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
    }
  }
  const onMove = () => requestAnimationFrame(place);

  function start() {
    if (!cfg || ui) return;
    steps = cfg.steps().filter(s => !s.sel || find(s.sel));
    if (!steps.length) return;
    lastFocus = document.activeElement;
    ui = buildUI();
    document.addEventListener("keydown", onKey, true);
    addEventListener("resize", onMove); addEventListener("scroll", onMove, true);
    show(0);
  }
  function close() {
    if (!ui) return;
    ls.set(cfg.key, "done");
    ui.layer.remove(); ui = null; target = null;
    document.removeEventListener("keydown", onKey, true);
    removeEventListener("resize", onMove); removeEventListener("scroll", onMove, true);
    if (lastFocus && lastFocus.focus) lastFocus.focus({preventScroll: true});
  }

  document.addEventListener("click", e => {
    if (e.target.closest("[data-tour-start]")) { e.preventDefault(); start(); }
  });

  window.CandorTour = {
    setup(c) {
      cfg = c;
      if (!document.getElementById("ct-style")) {
        const st = document.createElement("style"); st.id = "ct-style"; st.textContent = CSS; document.head.append(st);
      }
      const forced = new URLSearchParams(location.search).get("tour") === "1";
      if (forced || (c.auto !== false && !ls.get(c.key))) setTimeout(start, 350);
    },
    start, close,
  };
})();
