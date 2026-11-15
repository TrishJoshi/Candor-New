import { launch, sleep } from './cdp.mjs';
import { mkdirSync } from 'node:fs';
const OUT = process.argv[2]; for (const d of ['', 'tour', 'mobile']) mkdirSync(OUT + d, { recursive: true });
const ONLY = process.argv[3] ? new RegExp(process.argv[3]) : null;
const B = 'https://candor-junction-2026.tech/';
const EX = 'ex-435db701fe5f66dd';          // r/trump thread, 7 flagged accounts
const EX_DIR = '435db701fe5f66dd';
const b = await launch();
const log = (...a) => console.log(...a);

const exists = sel => b.js(`!!document.querySelector(${JSON.stringify(sel)})`);
async function skipTour() { if (await exists('.ct-skip')) { await b.js(`document.querySelector('.ct-skip').click()`); await sleep(400); } }
async function rect(sel, scroll = true) {
  return b.js(`(() => { const e = typeof ${JSON.stringify(sel)} === 'string' ? document.querySelector(${JSON.stringify(sel)}) : null;
    if (!e) return null; ${scroll ? "e.scrollIntoView({block:'center'});" : ''}
    const r = e.getBoundingClientRect(); return {x:r.x, y:r.y, w:r.width, h:r.height, sx:scrollX, sy:scrollY}; })()`);
}
async function shotEl(name, sel, pad = 12) {
  const r = await rect(sel); if (!r) { log('MISSING', sel); return; } await sleep(500);
  const r2 = await rect(sel, false);
  const res = await b.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true,
    clip: { x: Math.max(0, r2.x + r2.sx - pad), y: Math.max(0, r2.y + r2.sy - pad), width: r2.w + 2 * pad, height: Math.min(r2.h + 2 * pad, 5000), scale: 1 } });
  (await import('node:fs')).writeFileSync(OUT + name + '.png', Buffer.from(res.data, 'base64')); log('saved', name);
}
const shot = (name, o) => b.shot(OUT + name + '.png', o);
async function clickSel(sel) { const ok = await b.js(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return false; e.scrollIntoView({block:'center'}); e.click(); return true; })()`); if (!ok) log('MISSING', sel); await sleep(900); return ok; }
async function clickText(tag, text) { const ok = await b.js(`(() => { const e = [...document.querySelectorAll(${JSON.stringify(tag)})].find(x => x.textContent.includes(${JSON.stringify(text)})); if (!e) return false; e.scrollIntoView({block:'center'}); e.click(); return true; })()`); if (!ok) log('MISSING text', text); await sleep(1000); return ok; }
async function hover(sel) { const r = await rect(sel); if (!r) { log('MISSING', sel); return false; } await sleep(300); const r2 = await rect(sel, false); await b.mouse(r2.x + r2.w / 2, r2.y + r2.h / 2); await sleep(700); return true; }
async function tour(prefix, url) {
  await b.go(url, 4000);
  for (let i = 1; i <= 15; i++) {
    if (!(await exists('.ct-next'))) break;
    await shot(`${prefix}-step${String(i).padStart(2, '0')}`);
    const last = await b.js(`/finish|done|got it/i.test(document.querySelector('.ct-next').textContent)`);
    await b.js(`document.querySelector('.ct-next').click()`); await sleep(1300);
    if (last) break;
  }
}
const want = n => !ONLY || ONLY.test(n);

// ---------- desktop ----------
await b.size(1440, 900);
if (want('tour')) {
  await tour('tour/analyze', B);
  await tour('tour/demo', B + 'demo.html');
  await tour('tour/result', B + 'view.html?id=example');
  await tour('tour/dashboard', B + 'dashboard.html');
}
// make sure every tour is dismissed for clean shots
for (const p of ['', 'demo.html', 'view.html?id=example', 'dashboard.html']) { await b.go(B + p, 2500); await skipTour(); }

if (want('analyze')) {
  await b.go(B, 3000); await skipTour();
  await shot('01-analyze');
  await b.js(`(() => { const i = document.querySelector('#url'); i.value = 'https://www.reddit.com/r/politics/comments/…'; i.focus(); })()`); await sleep(300);
  await shot('02-analyze-paste-link');
  await shot('03-analyze-full', { full: true });
}
if (want('demo')) {
  await b.go(B + 'demo.html', 3500); await skipTour();
  await shot('04-demo-feed');
  await shot('05-demo-feed-full', { full: true });
  await shotEl('06-demo-card-health', '.card.post');
}
if (want('result')) {
  const V = B + 'view.html?id=' + EX;
  await b.go(V, 4500); await skipTour();
  await shot('07-result-thread');
  await shot('08-result-full', { full: true });
  await shotEl('09-narrative-distribution-pill', '.npill');
  await shotEl('10-narratives-sidebar', '#narrCard');
  await shotEl('11-authenticity-shield-card', '#shieldCard');
  await shotEl('12-thread-health', '.thread-health');
  // mini dashboard
  await clickSel('.npill'); await shot('13-mini-dashboard');
  await b.go(V, 3500);
  // account badge popover (a flagged account)
  await clickSel('.badge.lv-bad'); await shot('14-account-score-popover');
  await b.go(V, 3500);
  await clickSel('.badge.lv-ok'); await shot('15-account-score-popover-authentic');
  await b.go(V, 3500);
  // narrative chip highlight
  await clickSel('.nchip'); await shot('16-narrative-highlight');
  await b.go(V, 3500);
  // tooltip on an underlined number in the sidebar
  if (await hover('#narrCard [data-tip], #narrCard abbr, #narrCard .tip, #narrCard u')) await shot('17-tooltip');
  // labels off
  await b.go(V, 3500); await clickSel('#shieldBtn'); await sleep(500); await shot('18-labels-off');
  // breakdown & method
  await b.go(V, 3500); await clickText('a,button', 'Breakdown & method'); await shot('19-breakdown-and-method');
  await shot('20-breakdown-and-method-full', { full: true });
  // per-thread narrative map
  await b.go(B + `examples/${EX_DIR}/map.html`, 5000); await shot('21-thread-narrative-map');
  await shot('22-thread-narrative-map-full', { full: true });
}
if (want('dash')) {
  await b.go(B + 'dashboard.html', 6000); await skipTour();
  await shot('23-dashboard');
  const M = B + 'api/dashboard/map';
  await b.go(M, 6000);
  await shot('24-dashboard-map-overview');
  await shotEl('25-dashboard-tiles', '#tiles');
  await clickText('#topic-list button', 'Trump Administration'); await sleep(800); await shot('26-dashboard-topic-selected');
  await clickSel('#zoom-topic'); await sleep(1200); await shot('27-dashboard-zoom-to-topic');
  await shotEl('28-dashboard-word-clouds', '#clouds');
  await b.go(M, 5000);
  await b.js(`(() => { const s = document.querySelector('#sub-filter'); s.value = [...s.options].find(o => o.text.startsWith('r/politics')).value; s.dispatchEvent(new Event('change', {bubbles:true})); })()`); await sleep(2000);
  await shot('29-dashboard-subreddit-politics');
  await b.go(M, 5000);
  await shotEl('30-dashboard-inauthentic-narratives', '#inauth-panel');
  await shotEl('31-dashboard-timeline', '#timeline');
  if (await hover('#tiles [data-tip]')) await shot('32-dashboard-tooltip');
  await clickSel('#show-noise'); await sleep(1200); await shot('33-dashboard-with-noise');
  await shot('34-dashboard-map-full', { full: true });
  // narrative weather time-lapse
  const T = B + 'api/dashboard/timelapse';
  await b.go(T, 3000);
  await b.js(`document.querySelector('#play')?.click()`);
  for (const [i, v] of [[1, 10], [2, 35], [3, 60], [4, 95]]) {
    await b.js(`(() => { const s = document.querySelector('#scrub'); s.value = ${v}; s.dispatchEvent(new Event('input', {bubbles:true})); s.dispatchEvent(new Event('change', {bubbles:true})); })()`); await sleep(1200);
    await shot(`35-narrative-weather-frame${i}`);
  }
  await b.js(`(() => { const s = document.querySelector('#colour'); s.value = [...s.options].find(o => /auth/i.test(o.text)).value; s.dispatchEvent(new Event('change', {bubbles:true})); })()`); await sleep(1500);
  await shot('36-narrative-weather-authenticity');
  await b.go(B + 'dashboard.html', 4000); await clickText('button', 'Narrative weather'); await sleep(4000); await shot('37-dashboard-narrative-weather-tab');
}
// ---------- mobile ----------
if (want('mobile')) {
  await b.size(390, 844, true, 3);
  for (const [n, p] of [['m01-analyze', ''], ['m02-demo', 'demo.html'], ['m03-result', 'view.html?id=' + EX], ['m05-dashboard', 'dashboard.html']]) {
    await b.go(B + p, 4000); await skipTour(); await shot('mobile/' + n);
  }
  await b.go(B + 'view.html?id=' + EX, 3500); await clickSel('.npill'); await shot('mobile/m04-mini-dashboard');
}
await b.close();
