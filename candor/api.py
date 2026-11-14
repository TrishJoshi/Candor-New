"""Candor API.

POST /api/analyze {"url"}         -> {"id"}       start (or join) an analysis of a Reddit post
GET  /api/analyses/{id}           -> status, progress, message, error, summary
GET  /api/analyses/{id}/view      -> Reddit-mockup data (web/view.html renders it)
GET  /api/analyses/{id}/map       -> interactive narrative map (HTML)
GET  /api/history                 -> this visitor's past analyses (cookie ``candor_vid``)
GET  /api/health                  -> {"ok": true}

Run locally:  uvicorn candor.api:app --reload --port 8000   -> http://localhost:8000/
(the API also serves web/ so the pages work locally without nginx; on the VPS nginx serves web/)

State lives in SQLite + data/analyses/ (candor/store.py), so finished analyses and visitor
history survive restarts. Still run a single worker: the scrape rate limiter and the numba
lock are per-process.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from candor import store
from candor.urls import normalize_reddit_url
from candor.view import distribution

logger = logging.getLogger("candor.api")

# Max concurrent analyses. Throughput is capped by the shared scrape limiter (SCRAPE_PER_MINUTE).
EXECUTOR = ThreadPoolExecutor(max_workers=int(os.environ.get("ANALYSIS_WORKERS", "8")))
MAX_ACTIVE_PER_IP = int(os.environ.get("MAX_ACTIVE_PER_IP", "3"))  # protects context.dev credits
COOKIE = "candor_vid"
COOKIE_MAX_AGE = 365 * 24 * 3600


class AnalyzeRequest(BaseModel):
    url: str


# ------------------------------------------------------------------------------- helpers
def _visitor(request: Request, response: Response) -> str:
    """The visitor id from the cookie, minting (and setting) one if absent."""
    vid = request.cookies.get(COOKIE)
    if not vid or len(vid) > 64 or not vid.replace("-", "").replace("_", "").isalnum():
        vid = secrets.token_urlsafe(16)
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    # refreshed on every call, so the history lasts a year from the last visit
    response.set_cookie(COOKIE, vid, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax", secure=secure, path="/")
    return vid


def _client_ip(request: Request) -> str:
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def _run(analysis_id: str, url: str) -> None:
    last = [0.0]

    def progress(pct: float, message: str) -> None:
        now = time.monotonic()
        if now - last[0] >= 0.5 or pct >= 0.99:  # throttle DB writes
            last[0] = now
            store.update(analysis_id, progress=pct, message=message)

    try:
        store.update(analysis_id, status="running", message="Starting")
        from candor.live import AnalysisError, analyze  # import errors surface as job errors
        try:
            summary = analyze(analysis_id, url, progress=progress)
        except AnalysisError as e:
            store.update(analysis_id, status="error", error=str(e), finished_at=time.time())
            return
        store.update(analysis_id, status="done", progress=1.0, message="Done",
                     summary_json=json.dumps(summary), finished_at=time.time())
    except Exception as exc:
        logger.exception("analysis %s failed", analysis_id)
        store.update(analysis_id, status="error", finished_at=time.time(),
                     error=f"Something went wrong while analysing this post ({type(exc).__name__}). Please try again.")


# -------------------------------------------------------------------------------- startup
@asynccontextmanager
async def _lifespan(app: FastAPI):
    _startup()
    yield


def _startup() -> None:
    n = store.mark_interrupted()
    if n:
        logger.warning("marked %d interrupted analyses as failed", n)

    def warm() -> None:  # import the pipeline and JIT-compile UMAP/HDBSCAN before the first job
        try:
            from candor.live import warmup
            warmup()
        except Exception as exc:  # reported again by the first job if it is a real problem
            logger.warning("warmup failed: %s: %s", type(exc).__name__, exc)

    threading.Thread(target=warm, daemon=True).start()


app = FastAPI(title="Candor API", lifespan=_lifespan)


# --------------------------------------------------------------------------------- routes
@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/analyze")
def analyze_post(req: AnalyzeRequest, request: Request, response: Response) -> dict:
    norm = normalize_reddit_url(req.url)
    if not norm:
        raise HTTPException(400, "Please paste a Reddit post link (…reddit.com/r/<sub>/comments/…)")
    canonical, post_id = norm
    vid = _visitor(request, response)
    if existing := store.find_active(post_id):  # someone is already analysing this post: join it
        store.link_visitor(vid, existing)
        return {"id": existing, "joined": True}
    ip = _client_ip(request)
    if store.count_active_for_ip(ip) >= MAX_ACTIVE_PER_IP:
        raise HTTPException(429, f"You already have {MAX_ACTIVE_PER_IP} analyses running. Please wait for one to finish.")
    aid = store.create(req.url.strip(), canonical, post_id, vid, ip)
    EXECUTOR.submit(_run, aid, canonical)
    return {"id": aid, "joined": False}


@app.get("/api/analyses/{analysis_id}")
def get_analysis(analysis_id: str) -> dict:
    a = store.get(analysis_id) if analysis_id.isalnum() else None
    if a is None:
        raise HTTPException(404, "Unknown analysis")
    return a


@app.get("/api/analyses/{analysis_id}/view")
def get_view(analysis_id: str) -> FileResponse:
    p = store.read_file(analysis_id, "view.json")
    if p is None:
        raise HTTPException(404, "Not ready")
    return FileResponse(p, media_type="application/json")


@app.get("/api/analyses/{analysis_id}/map")
def get_map(analysis_id: str) -> FileResponse:
    p = store.read_file(analysis_id, "map.html")
    if p is None:
        raise HTTPException(404, "Not ready")
    return FileResponse(p, media_type="text/html")


@app.get("/api/history")
def get_history(request: Request, response: Response) -> dict:
    vid = _visitor(request, response)
    return {"analyses": [_with_distribution(a) for a in store.history(vid)]}


def _with_distribution(a: dict) -> dict:
    """Analyses stored before summaries carried the narrative distribution: derive it from view.json."""
    s = a.get("summary")
    if a.get("status") == "done" and isinstance(s, dict) and "narratives" not in s:
        p = store.read_file(a["id"], "view.json")
        try:
            s["narratives"] = distribution(json.loads(p.read_text(encoding="utf-8")).get("narratives") or []) if p else []
        except (ValueError, KeyError, TypeError):
            s["narratives"] = []
    return a


# Mounted last so the /api routes above take priority.
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "web", html=True), name="web")
