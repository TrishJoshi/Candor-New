"""Build the narrated product demo shown on the "Video Demo" tab (web/video.html).

    .venv-demo/bin/python tools/demo_video/build.py [--base https://candor-junction-2026.tech]

To record unreleased changes, serve this checkout first (an empty data dir is enough: the
demo feed falls back to web/examples, the dashboard to web/dashboard) and pass --base:

    CANDOR_DATA_DIR=$(mktemp -d) .venv/bin/python -m uvicorn candor.api:app --port 8791
    .venv-demo/bin/python tools/demo_video/build.py --base http://127.0.0.1:8791

1. Narration: every line of SCENES is spoken by Piper (local neural TTS, no cloud).
2. Screen: Playwright drives Chromium through the live site (read-only: it types a link
   but never presses Analyze) while CDP screencasts frames with wall-clock timestamps.
   A fake cursor and burned-in captions are injected into each page.
3. ffmpeg lays the narration clips at the times they were spoken and encodes
   web/video/candor-demo.mp4 (+ poster.jpg and transcript.json for the page).

Needs: requirements-demo.txt, ffmpeg, a Chromium binary (--chromium), a Piper voice
(downloaded on first run into --voices). Output is committed; the server only serves it.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from playwright.async_api import Page, async_playwright

log = logging.getLogger("demo_video")
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "web" / "video"
VIEW_W, VIEW_H, SCALE = 1280, 720, 1.5          # recorded at 1920x1080
GAP = 0.35                                      # silence between narration lines (s)
MAX_SECONDS = 120.0                             # the tab promises "up to 2 minutes"
TOUR_KEYS = ["home", "demo", "result", "dashboard"]


@dataclass
class Line:
    say: str                    # what the voice reads (spelled for TTS)
    caption: str | None = None  # what is shown on screen, if different


@dataclass
class Scene:
    name: str
    lines: list[Line]
    action: Callable[["Recorder"], Awaitable[None]] | None = None
    hold: float = 0.4           # pause after the narration ends


# ---------------------------------------------------------------- page overlay
OVERLAY_JS = r"""
([x, y, cap]) => {
  const root = document.documentElement;
  if (!document.getElementById('__demo_css')) {
    const s = document.createElement('style'); s.id = '__demo_css';
    s.textContent = `
      #__demo_cursor{position:fixed;left:0;top:0;width:26px;height:26px;z-index:2147483647;pointer-events:none;
        transition-property:transform;transition-timing-function:cubic-bezier(.45,0,.2,1)}
      #__demo_cursor svg{filter:drop-shadow(0 2px 3px rgba(0,0,0,.35))}
      .__demo_ripple{position:fixed;width:40px;height:40px;margin:-20px 0 0 -20px;border-radius:50%;
        border:3px solid #3662e3;z-index:2147483646;pointer-events:none;animation:__demo_r .6s ease-out forwards}
      @keyframes __demo_r{from{transform:scale(.3);opacity:1}to{transform:scale(1.7);opacity:0}}
      #__demo_cap{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);width:max-content;max-width:min(1040px,88vw);
        background:rgba(20,24,43,.9);color:#fff;font:600 21px/1.4 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;
        padding:10px 20px;border-radius:12px;text-align:center;z-index:2147483645;pointer-events:none;
        box-shadow:0 6px 24px rgba(0,0,0,.25)}
      #__demo_cap:empty{display:none}`;
    root.appendChild(s);
  }
  let c = document.getElementById('__demo_cursor');
  if (!c) {
    c = document.createElement('div'); c.id = '__demo_cursor';
    c.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M4 2l16 9.5-7 1.6-3.6 6.6z" fill="#fff" stroke="#14182b" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    root.appendChild(c);
  }
  c.style.transitionDuration = '0s'; c.style.transform = `translate(${x}px,${y}px)`;
  let p = document.getElementById('__demo_cap');
  if (!p) { p = document.createElement('div'); p.id = '__demo_cap'; root.appendChild(p); }
  p.textContent = cap || '';
  window.__demo = {
    move(x, y, d) { c.style.transitionDuration = d + 's'; c.style.transform = `translate(${x}px,${y}px)`; },
    ripple(x, y) { const r = document.createElement('div'); r.className = '__demo_ripple';
      r.style.left = x + 'px'; r.style.top = y + 'px'; root.appendChild(r); setTimeout(() => r.remove(), 700); },
    cap(t) { p.textContent = t || ''; },
    scroll(dy, d) { return new Promise(res => { const y0 = scrollY, t0 = performance.now();
      const f = t => { const k = Math.min(1, (t - t0) / (d * 1000)), e = k < .5 ? 2*k*k : 1 - Math.pow(-2*k + 2, 2) / 2;
        scrollTo(0, y0 + dy * e); k < 1 ? requestAnimationFrame(f) : res(); }; requestAnimationFrame(f); }); },
  };
}
"""

TITLE_CARD = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
<style>
html,body{margin:0;height:100%}
body{background:#14182b;color:#fff;font-family:Inter,system-ui,sans-serif;display:flex;align-items:center;justify-content:center;overflow:hidden}
.c{max-width:980px;padding:0 48px;text-align:center;animation:in .8s ease-out both}
@keyframes in{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
.brand{display:inline-flex;align-items:center;gap:12px;font-weight:800;font-size:28px;margin-bottom:34px;color:#c9cee0}
.flag{display:flex;gap:4px}.flag i{width:8px;height:26px;border-radius:2px}
h1{font-size:64px;line-height:1.05;letter-spacing:-.03em;margin:0;font-weight:800}
h1 span{color:#6f93ff}
p{font-size:26px;color:#c9cee0;margin:22px 0 0;line-height:1.4}
</style></head><body><div class="c">
<div class="brand"><span class="flag"><i style="background:#3662e3"></i><i style="background:#1f9d55"></i><i style="background:#e03e3e"></i></span>Candor</div>
<h1>{{title}}</h1>{{sub}}</div></body></html>"""


class Recorder:
    """Drives one page, keeps the caption/cursor state and the narration timeline."""

    def __init__(self, page: Page, base: str, clips: dict[str, tuple[Path, float]]):
        self.page, self.base, self.clips = page, base.rstrip("/"), clips
        self.t0 = 0.0
        self.cursor = (VIEW_W * 0.62, VIEW_H * 0.62)
        self.caption = ""
        self.placed: list[tuple[Path, float]] = []          # (wav, offset from t0)
        self.cues: list[dict] = []                          # transcript
        self._line_started: list[asyncio.Event] = []

    # -- overlay
    async def overlay(self) -> None:
        try:
            await self.page.evaluate(OVERLAY_JS, [self.cursor[0], self.cursor[1], self.caption])
        except Exception as e:  # page mid-navigation: re-applied after the next load
            log.debug("overlay: %s", e)

    async def set_caption(self, text: str) -> None:
        self.caption = text
        try:
            await self.page.evaluate("t => window.__demo && window.__demo.cap(t)", text)
        except Exception:
            pass

    # -- navigation
    async def goto(self, path: str) -> None:
        await self.page.goto(self.base + path, wait_until="networkidle")
        await self.overlay()

    async def card(self, title: str, sub: str = "") -> None:
        self.cursor = (VIEW_W + 40, VIEW_H * 0.7)              # park the pointer off-screen
        await self.page.set_content(TITLE_CARD.replace("{{title}}", title).replace("{{sub}}", f"<p>{sub}</p>" if sub else ""),
                                    wait_until="networkidle")
        await self.overlay()

    # -- pointer
    async def move(self, x: float, y: float, dur: float = 0.8) -> None:
        self.cursor = (x, y)
        await self.page.evaluate("([x,y,d]) => window.__demo.move(x,y,d)", [x, y, dur])
        await asyncio.sleep(dur)
        await self.page.mouse.move(x, y)

    async def point(self, locator, dur: float = 0.8, dx: float = 0.5, dy: float = 0.5) -> tuple[float, float]:
        box = await locator.bounding_box()
        if box is None:
            raise RuntimeError(f"not visible: {locator}")
        x, y = box["x"] + box["width"] * dx, box["y"] + box["height"] * dy
        await self.move(x, y, dur)
        return x, y

    async def click(self, locator, dur: float = 0.8, **kw) -> None:
        x, y = await self.point(locator, dur, **kw)
        await self.page.evaluate("([x,y]) => window.__demo.ripple(x,y)", [x, y])
        await self.page.mouse.click(x, y)

    async def scroll(self, dy: float, dur: float = 1.2) -> None:
        await self.page.evaluate("([dy,d]) => window.__demo.scroll(dy,d)", [dy, dur])

    async def scroll_to(self, target, top: float = 110, dur: float = 1.2) -> None:
        """Smooth-scroll so that target (a selector or locator) sits `top` px below the viewport top."""
        loc = self.page.locator(target) if isinstance(target, str) else target
        dy = await loc.first.evaluate("(e, t) => e.getBoundingClientRect().top - t", top)
        await self.scroll(dy, dur)
        await asyncio.sleep(dur)

    async def reveal(self, locator, dur: float = 0.9) -> None:
        """Smooth-scroll only if the element is outside the middle of the viewport."""
        top = await locator.evaluate("e => e.getBoundingClientRect().top")
        if not 90 < top < VIEW_H - 160:
            await self.scroll_to(locator, top=VIEW_H * 0.4, dur=dur)

    # -- timing
    async def line(self, i: int) -> None:
        """Wait until narration line i of the current scene starts."""
        await self._line_started[i].wait()

    async def pause(self, s: float) -> None:
        await asyncio.sleep(s)

    async def _narrate(self, lines: list[Line]) -> None:
        for i, ln in enumerate(lines):
            wav, dur = self.clips[ln.say]
            start = time.time() - self.t0
            await self.set_caption(ln.caption or ln.say)
            self.placed.append((wav, start))
            self.cues.append({"start": round(start, 2), "end": round(start + dur, 2), "text": ln.caption or ln.say})
            self._line_started[i].set()
            await asyncio.sleep(dur + GAP)

    async def run(self, scene: Scene) -> None:
        log.info("scene %-10s at %5.1fs", scene.name, time.time() - self.t0)
        self._line_started = [asyncio.Event() for _ in scene.lines]
        narration = asyncio.create_task(self._narrate(scene.lines))
        if scene.action:
            await scene.action(self)
        await narration
        await asyncio.sleep(scene.hold)


# ---------------------------------------------------------------- the storyboard
# Follows the pitch: hook -> problem -> approach -> stage 1 (accounts) -> stage 2
# (narratives) -> dashboard across subreddits -> closing.

async def a_hook(r: Recorder) -> None:
    await r.card("Who's really in<br>the <span>comments</span>?")


async def a_problem(r: Recorder) -> None:
    await r.card("A million likes on a burger debate?<br><span>Who cares.</span>")
    await r.line(1)
    await r.card("When it affects people you love,<br><span>perception matters.</span>")


async def a_home(r: Recorder) -> None:
    await r.goto("/")
    await r.pause(0.6)
    url = await r.page.get_attribute("#samples [data-url]", "data-url")
    await r.line(1)
    await r.click(r.page.locator("#url"), dx=0.15)
    await r.page.keyboard.type(url or "https://www.reddit.com/r/technology/comments/", delay=22)
    await r.point(r.page.locator("#live button"), dur=0.6)      # hover Analyze, never submit


async def a_feed(r: Recorder) -> None:
    await r.goto("/demo.html")
    await r.page.wait_for_selector("a.card.post")
    await r.point(r.page.locator("a.card.post .npill").first, dur=0.9)
    await r.line(1)
    card = r.page.locator("a.card.post", has_text="not alone").first   # r/trump, 7 flagged
    await r.scroll_to(card, top=120, dur=1.4)
    await r.pause(0.6)
    await r.point(card.locator(".thread-health"), dur=0.8)


async def a_accounts(r: Recorder) -> None:
    card = r.page.locator("a.card.post", has_text="not alone").first
    async with r.page.expect_navigation(wait_until="networkidle"):
        await r.click(card.locator("h2"), dur=0.6)
    await r.overlay()
    await r.page.wait_for_selector("#shieldCard")
    await r.pause(0.8)
    await r.scroll_to("#shieldCard", top=70, dur=1.4)
    await r.point(r.page.locator("#shieldCard .dist"), dur=0.8)
    await r.line(2)
    items = r.page.locator("#shieldCard .crit-list li")
    for i in range(5):                                          # one criterion per question
        await r.point(items.nth(i), dur=0.5, dx=0.3)
        await r.pause(1.15)
    await r.line(3)
    badge = r.page.locator("#main .badge.lv-bad").first
    await r.reveal(badge)
    await r.click(badge, dur=0.7)


async def a_narratives(r: Recorder) -> None:
    await r.page.mouse.click(5, VIEW_H - 5)                     # close the evidence popup
    await r.scroll(-(await r.page.evaluate("scrollY")), dur=1.2)
    await r.pause(1.3)
    await r.point(r.page.locator("#narrCard .narr li").first, dur=0.8)
    await r.line(1)
    await r.click(r.page.locator("#narrCard [data-narr]").first, dur=0.4)
    await r.pause(0.8)
    await r.scroll(560, dur=1.6)


async def a_filter(r: Recorder) -> None:
    await r.scroll_to("#filterCard", top=160, dur=1.0)
    slider = r.page.locator("#thr")
    box = await slider.bounding_box()
    x0, y = box["x"] + 8, box["y"] + box["height"] / 2
    await r.move(x0, y, 0.7)
    await r.page.mouse.down()
    x1 = box["x"] + box["width"] * 80 / 95
    for k in range(1, 21):                                      # slow, visible drag
        x = x0 + (x1 - x0) * k / 20
        r.cursor = (x, y)
        await r.page.evaluate("([x,y]) => window.__demo.move(x,y,0.05)", [x, y])
        await r.page.mouse.move(x, y)
        await asyncio.sleep(0.06)
    await r.page.mouse.up()


async def a_dashboard(r: Recorder) -> None:
    await r.goto("/dashboard.html")
    frame = r.page.frame_locator("iframe")
    await frame.locator("#topic-list li button").first.wait_for()
    await r.pause(0.8)
    await r.point(frame.locator("#tiles"), dur=0.9, dy=0.4)
    await r.line(1)
    inner = next(f for f in r.page.frames if f.url.endswith("/api/dashboard/map"))
    await inner.evaluate("window.scrollTo({top: 250, behavior: 'smooth'})")   # bring the point cloud into view
    await r.pause(0.9)
    topics = frame.locator("#topic-list li button")
    for i in (1, 2):
        await r.click(topics.nth(i), dur=0.7)
        await r.pause(1.6)


async def a_weather(r: Recorder) -> None:
    await r.click(r.page.locator('[data-view="timelapse"]'), dur=0.7)
    await r.pause(1.0)
    await r.move(VIEW_W * 0.55, VIEW_H * 0.6, 0.9)


async def a_close(r: Recorder) -> None:
    await r.card("We can't fix the crowd.<br>But we can show you <span>who's in it.</span>")
    await r.line(2)
    await r.card("See it with <span>Candor</span>.", "candor-junction-2026.tech")


SCENES: list[Scene] = [
    Scene("hook", [
        Line("How many of you read the headline, skip the article, and go straight to the comments?"),
        Line("Before we even open the link, likes, upvotes and top comments have already shaped our opinion."),
    ], a_hook),
    Scene("problem", [
        Line("If it's about burgers, who cares if a million people liked it?"),
        Line("But when it affects your friends and your family, public perception matters most."),
    ], a_problem),
    Scene("analyze", [
        Line("Candor doesn't ban bots or flag accounts. It shows how narratives behave, and what kinds of accounts support them."),
        Line("Paste any public Reddit thread, and Candor analyzes it live."),
    ], a_home),
    Scene("feed", [
        Line("Or browse threads we've already analyzed."),
        Line("Each one shows its narrative distribution, an AI summary, and how many commenters were flagged."),
    ], a_feed),
    Scene("accounts", [
        Line("Stage one. Context dot dev pulls posts, comments and profiles in real time.",
             "Stage 1 · context.dev pulls posts, comments and profiles in real time."),
        Line("Every engaged commenter is scored from zero to one hundred, on five weighted criteria."),
        Line("Is this an established, real person? Did they write it? Are they acting in concert with others? "
             "Did they earn their upvotes? And do they do this elsewhere?"),
        Line("Click any badge to see the evidence."),
    ], a_accounts),
    Scene("narratives", [
        Line("Stage two. Gemini embeddings turn each comment into a vector, and H D B scan finds dense clusters of shared meaning.",
             "Stage 2 · Gemini embeddings turn each comment into a vector; HDBSCAN finds dense clusters of shared meaning."),
        Line("NVIDIA Nemotron names each cluster and explains it. That's the narrative."),
    ], a_narratives),
    Scene("filter", [
        Line("Raise the filter to fold away low-scoring accounts, and see what the real people are saying."),
    ], a_filter),
    Scene("dashboard", [
        Line("The dashboard scales this up: four and a half thousand posts and comments, grouped into narratives and topics.",
             "The dashboard scales this up: 4,453 posts and comments, grouped into narratives and topics."),
        Line("Every topic carries its own authenticity score."),
    ], a_dashboard),
    Scene("weather", [
        Line("Narrative weather replays the conversation over time, so you can watch narratives swell as they go viral."),
    ], a_weather, hold=0.8),
    Scene("closing", [
        Line("It's not a verdict on any person. It's the crowd behind each narrative, on transparent criteria."),
        Line("We can't fix the crowd. But we can show you who's in it."),
        Line("See it for yourself. See it with Candor."),
    ], a_close, hold=1.6),
]


# ---------------------------------------------------------------- audio
def synthesize(lines: list[Line], voice: Path, work: Path, py: str, length_scale: float) -> dict[str, tuple[Path, float]]:
    clips = {}
    for i, ln in enumerate(lines):
        wav = work / f"line{i:02d}.wav"
        subprocess.run([py, "-m", "piper", "-m", str(voice), "--length-scale", str(length_scale), "-f", str(wav)],
                       input=ln.say.encode(), check=True, capture_output=True)
        with wave.open(str(wav)) as w:
            clips[ln.say] = (wav, w.getnframes() / w.getframerate())
    return clips


def mix(placed: list[tuple[Path, float]], total: float, out: Path) -> None:
    """Lay each clip at its offset on a silent track (all Piper clips share one format)."""
    with wave.open(str(placed[0][0])) as w:
        params, rate, width = w.getparams(), w.getframerate(), w.getsampwidth() * w.getnchannels()
    buf = bytearray(int(total * rate) * width)
    for wav, at in placed:
        with wave.open(str(wav)) as w:
            data = w.readframes(w.getnframes())
        i = int(at * rate) * width
        buf[i:i + len(data)] = data[: max(0, len(buf) - i)]
    with wave.open(str(out), "wb") as w:
        w.setparams(params)
        w.writeframes(bytes(buf))


# ---------------------------------------------------------------- video
def encode(frames: list[tuple[float, Path]], t0: float, t1: float, audio: Path, out: Path) -> None:
    frames = sorted(frames)
    first = max((i for i, (ts, _) in enumerate(frames) if ts <= t0), default=0)
    seq = [(max(ts, t0) - t0, p) for ts, p in frames[first:] if ts < t1]
    lst = audio.parent / "frames.txt"                       # scratch dir, next to the narration
    with lst.open("w") as f:
        for k, (t, p) in enumerate(seq):
            nxt = seq[k + 1][0] if k + 1 < len(seq) else t1 - t0
            f.write(f"file '{p}'\nduration {max(nxt - t, 0.001):.4f}\n")
        f.write(f"file '{seq[-1][1]}'\n")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio),
        "-vf", "scale=1920:1080:flags=lanczos,fps=30,format=yuv420p",
        "-c:v", "libx264", "-preset", "slow", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
        "-t", f"{t1 - t0:.3f}", "-movflags", "+faststart", str(out)], check=True)


async def record(args, clips) -> tuple[Recorder, list[tuple[float, Path]], float]:
    frames: list[tuple[float, Path]] = []
    fdir = args.work / "frames"
    fdir.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=args.chromium)
        ctx = await browser.new_context(viewport={"width": VIEW_W, "height": VIEW_H}, device_scale_factor=SCALE)
        await ctx.add_init_script("for (const k of %s) try { localStorage.setItem('candor_tour_' + k, 'done') } catch (e) {}"
                                  % json.dumps(TOUR_KEYS))
        page = await ctx.new_page()
        rec = Recorder(page, args.base, clips)
        for path in ("/", "/demo.html", "/dashboard.html"):    # warm the HTTP cache
            await rec.goto(path)
        await rec.card("")

        cdp = await ctx.new_cdp_session(page)

        def on_frame(e: dict) -> None:
            fp = fdir / f"f{len(frames):05d}.jpg"
            fp.write_bytes(base64.b64decode(e["data"]))
            frames.append((e["metadata"]["timestamp"], fp))
            asyncio.ensure_future(cdp.send("Page.screencastFrameAck", {"sessionId": e["sessionId"]}))

        cdp.on("Page.screencastFrame", on_frame)
        await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92,
                                                "maxWidth": int(VIEW_W * SCALE), "maxHeight": int(VIEW_H * SCALE)})
        await asyncio.sleep(0.5)
        rec.t0 = time.time()
        for sc in SCENES:
            await rec.run(sc)
        t1 = time.time()
        await cdp.send("Page.stopScreencast")
        await browser.close()
    return rec, frames, t1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="https://candor-junction-2026.tech", help="site to record")
    ap.add_argument("--chromium", default=shutil.which("chromium") or shutil.which("chromium-browser"))
    ap.add_argument("--voice", default="en_US-lessac-high", help="Piper voice name")
    ap.add_argument("--voices", type=Path, default=Path.home() / ".cache" / "piper-voices")
    ap.add_argument("--length-scale", type=float, default=0.92, help="<1 speaks faster")
    ap.add_argument("--work", type=Path, default=None, help="scratch dir (default: a temp dir)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args.work = args.work or Path(tempfile.mkdtemp(prefix="candor-demo-"))
    args.work.mkdir(parents=True, exist_ok=True)
    py = sys.executable

    model = args.voices / f"{args.voice}.onnx"
    if not model.exists():
        args.voices.mkdir(parents=True, exist_ok=True)
        subprocess.run([py, "-m", "piper.download_voices", args.voice, "--download-dir", str(args.voices)], check=True)

    lines = [ln for sc in SCENES for ln in sc.lines]
    clips = synthesize(lines, model, args.work, py, args.length_scale)
    speech = sum(d for _, d in clips.values()) + GAP * len(lines)
    log.info("narration: %d lines, %.1fs of speech", len(lines), speech)

    rec, frames, t1 = asyncio.run(record(args, clips))
    total = t1 - rec.t0
    log.info("recorded %.1fs, %d frames", total, len(frames))
    if total > MAX_SECONDS:
        log.warning("demo runs %.1fs, over the %ds the tab promises; shorten SCENES", total, MAX_SECONDS)

    OUT.mkdir(parents=True, exist_ok=True)
    audio = args.work / "narration.wav"
    mix(rec.placed, total, audio)
    mp4 = OUT / "candor-demo.mp4"
    encode(frames, rec.t0, t1, audio, mp4)
    poster_at = next(c["start"] for c in rec.cues if c["text"].startswith("Stage 1")) + 4.0
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{poster_at:.2f}", "-i", str(mp4),
                    "-frames:v", "1", "-q:v", "3", str(OUT / "poster.jpg")], check=True)
    chapters = {sc.name: next(c["start"] for c in rec.cues if c["text"] == (sc.lines[0].caption or sc.lines[0].say))
                for sc in SCENES}
    (OUT / "transcript.json").write_text(json.dumps(
        {"duration": round(total, 2), "chapters": chapters, "cues": rec.cues}, indent=1) + "\n")
    log.info("wrote %s (%.1f MB)", mp4.relative_to(ROOT), mp4.stat().st_size / 1e6)


if __name__ == "__main__":
    main()
