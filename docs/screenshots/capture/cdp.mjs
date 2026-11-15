// Minimal Chrome DevTools Protocol driver (no deps; Node >= 22 WebSocket).
import { spawn } from 'node:child_process';
import { writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export async function launch({ port = 9333 } = {}) {
  const prof = mkdtempSync(join(process.env.JOBTMP || tmpdir(), 'chr-'));
  const proc = spawn('chromium', ['--headless=new', `--remote-debugging-port=${port}`,
    `--user-data-dir=${prof}`, '--no-first-run', '--hide-scrollbars', '--disable-gpu',
    '--force-color-profile=srgb', 'about:blank'], { stdio: 'ignore' });
  let ws;
  for (let i = 0; i < 50; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
      const page = list.find(t => t.type === 'page');
      if (page) { ws = new WebSocket(page.webSocketDebuggerUrl); break; }
    } catch {}
    await sleep(200);
  }
  await new Promise(r => ws.addEventListener('open', r, { once: true }));
  let id = 0; const pending = new Map(); const listeners = [];
  ws.addEventListener('message', ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result); }
    else listeners.forEach(l => l(m));
  });
  const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
  await send('Page.enable'); await send('Runtime.enable');
  const b = {
    send,
    async size(w, h, mobile = false, scale = 2) { await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: scale, mobile }); },
    async go(url, wait = 2500) {
      const loaded = new Promise(r => { const l = m => { if (m.method === 'Page.loadEventFired') { listeners.splice(listeners.indexOf(l), 1); r(); } }; listeners.push(l); });
      await send('Page.navigate', { url }); await Promise.race([loaded, sleep(30000)]); await sleep(wait);
    },
    async js(expr) {
      const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
      return r.result.value;
    },
    async shot(path, { full = false } = {}) {
      const p = { format: 'png', captureBeyondViewport: full };
      if (full) {
        const m = await send('Page.getLayoutMetrics');
        const s = m.cssContentSize; p.clip = { x: 0, y: 0, width: s.width, height: Math.min(s.height, 6000), scale: 1 };
      }
      const r = await send('Page.captureScreenshot', p);
      writeFileSync(path, Buffer.from(r.data, 'base64')); console.log('saved', path);
    },
    async mouse(x, y, type = 'mouseMoved') { await send('Input.dispatchMouseEvent', { type, x, y, button: type === 'mouseMoved' ? 'none' : 'left', clickCount: 1 }); },
    async click(x, y) { await b.mouse(x, y); await b.mouse(x, y, 'mousePressed'); await b.mouse(x, y, 'mouseReleased'); },
    async close() { try { await send('Browser.close'); } catch {} proc.kill(); },
  };
  return b;
}
export const sleep = ms => new Promise(r => setTimeout(r, ms));
