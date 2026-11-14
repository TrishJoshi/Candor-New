"""Candor API: background job + polling.

POST /api/analyze {"url": "..."}  -> {"job_id": "..."}
GET  /api/jobs/{job_id}           -> {"status", "progress", "message", "report", "error"}
GET  /api/health                  -> {"ok": true}

Run locally:  uvicorn candor.api:app --reload --port 8000   -> http://localhost:8000/
(the API also serves web/ so the pages work locally without nginx; on the VPS nginx serves web/ first)
Single worker only: jobs live in process memory.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="Candor API")

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
# Max concurrent analyses. Throughput is capped by the shared scrape limiter (SCRAPE_PER_MINUTE),
# so more workers only help until the limiter is kept busy; 8 is plenty at 90 scrapes/min.
EXECUTOR = ThreadPoolExecutor(max_workers=int(os.environ.get("ANALYSIS_WORKERS", "8")))


class AnalyzeRequest(BaseModel):
    url: str


def _update(job_id: str, **fields) -> None:
    with JOBS_LOCK:
        JOBS[job_id].update(fields)


def _run_pipeline(job_id: str, url: str) -> None:
    def progress(pct: float, message: str) -> None:
        _update(job_id, progress=round(pct, 2), message=message)

    try:
        _update(job_id, status="running")
        if os.environ.get("CANDOR_STUB") == "1":  # frontend/deploy testing without API keys
            analyze = _stub_analyze
        else:  # import errors (missing deps/keys) must surface as job errors, not fall back silently
            from candor.live import analyze
        report = analyze(url, progress=progress)
        _update(job_id, status="done", progress=1.0, message="Done", report=report)
    except Exception as exc:  # surface any pipeline failure to the client
        _update(job_id, status="error", error=f"{type(exc).__name__}: {exc}")


def _stub_analyze(url: str, progress) -> dict:
    """Placeholder so the frontend and deploy can be tested before the pipeline lands."""
    for i, step in enumerate(["Scraping post", "Scraping profiles", "Scoring accounts", "Clustering"]):
        progress(i / 4, step)
        time.sleep(1)
    return {"post": {"url": url, "title": "(stub report)"}, "comments": [], "buckets": [],
            "overall": {"weighted_authenticity": None, "n_accounts_scored": 0,
                        "n_unassessed": 0, "warnings": ["stub pipeline"]}}


def _valid_reddit_url(url: str) -> bool:
    p = urlparse(url.strip())
    host = (p.hostname or "").lower()
    return p.scheme in ("http", "https") and (host == "reddit.com" or host.endswith(".reddit.com") or host == "redd.it") \
        and ("/comments/" in p.path or host == "redd.it")


@app.on_event("startup")
def _warmup() -> None:
    """Import the pipeline and JIT-compile UMAP/HDBSCAN in the background so the first job isn't slow."""
    def run() -> None:
        try:
            from candor.live import warmup
            warmup()
        except Exception as exc:  # reported again by the first job if it is a real problem
            print(f"warmup failed: {type(exc).__name__}: {exc}", flush=True)
    if os.environ.get("CANDOR_STUB") != "1":
        threading.Thread(target=run, daemon=True).start()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/analyze")
def analyze_post(req: AnalyzeRequest) -> dict:
    if not _valid_reddit_url(req.url):
        raise HTTPException(400, "Please paste a Reddit post link (…reddit.com/r/<sub>/comments/…)")
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "queued", "progress": 0.0, "message": "Queued",
                        "report": None, "error": None, "url": req.url, "created": time.time()}
    EXECUTOR.submit(_run_pipeline, job_id, req.url.strip())
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown job")
        return dict(job)


# Mounted last so the /api routes above take priority.
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "web", html=True), name="web")
