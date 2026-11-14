"""Live analysis of one Reddit post: the whole pipeline for a pasted link.

    analyze(analysis_id, url, progress) -> summary dict     (run by candor.api in a worker thread)

  1. scrape thread       candor/scrape.py     1 context.dev call (JSON extraction + rendered HTML)
  2. flatten + filter    narrative-buckets    drop bots and deleted comments
  3. in parallel:
     a. profiles         candor/profiles.py   top MAX_AUTHORS commenters, context.dev reddit-user parser
     b. narratives       narrative-buckets    Gemini embeddings -> UMAP -> HDBSCAN -> NVIDIA labels
  4. score accounts      candor/scoring.py    profile_authenticity_scorer, live mode
  5. outputs             candor/view.py       Reddit mockup data + narrative map
Every artifact is stored in data/analyses/<id>/ (see candor/store.py).

Run without the API:  python -m candor.live <reddit_url>
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
BOTS = {"automoderator", "remindmebot", "sneakpeekbot", "[deleted]", "[removed]"}
MIN_ITEMS = 8  # below this there is nothing to cluster
MAX_AUTHORS = int(os.environ.get("MAX_AUTHORS", "20"))
PROFILE_DEADLINE_S = float(os.environ.get("PROFILE_DEADLINE_S", "120"))
# Numba's default threading layer aborts the whole process ("Concurrent access has been detected")
# when two threads run UMAP/HDBSCAN at once, e.g. two concurrent jobs or a job during warmup.
# Only the numba steps are serialised; embedding and LLM labelling still overlap across jobs.
NUMBA_LOCK = threading.Lock()

logger = logging.getLogger(__name__)


class AnalysisError(Exception):
    """A failure whose message is safe and useful to show to the visitor."""


def _locked(fn):
    def run(*args, **kwargs):
        with NUMBA_LOCK:
            return fn(*args, **kwargs)
    return run


def _load_env() -> None:
    """Both .env files (context.dev key at the root; Gemini/NVIDIA keys in narrative-buckets).
    Real environment variables (systemd EnvironmentFile) win."""
    for p in (ROOT / ".env", ROOT / "narrative-buckets" / ".env"):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.removeprefix("export ").split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_env()

from candor import store  # noqa: E402
from candor.ingest import flatten_reddit_posts  # noqa: E402  (narrative-buckets, via candor.__path__)
from candor.profiles import pick_authors, scrape_profiles  # noqa: E402
from candor.schemas import NOISE_ID  # noqa: E402
from candor.scoring import score_thread  # noqa: E402
from candor.scrape import scrape_thread  # noqa: E402
from candor.spatial_engine import SpatialEngine, SpatialEngineConfig  # noqa: E402
from candor.view import build_view  # noqa: E402
from candor.visualize import write_report  # noqa: E402


def engine_config(n: int) -> SpatialEngineConfig:
    """Stage 2 defaults are tuned for ~4k comments; one thread has ~35-300.
    Verified on two live threads (34 and 36 items): min_cluster_size 3 + eom -> 2-3 narratives, 15-20% noise."""
    return SpatialEngineConfig(
        hdbscan_min_cluster_size=max(3, round(n / 25)),
        hdbscan_selection_method="eom",
        umap_n_neighbors=min(15, max(2, n // 3)),
    )


class _Progress:
    """Progress for steps that run in parallel: never goes backwards, joins their messages."""

    def __init__(self, cb: Callable[[float, str], None]) -> None:
        self.cb, self.pct, self.parts, self.lock = cb, 0.0, {}, threading.Lock()

    def __call__(self, pct: float, message: str, part: str = "main") -> None:
        with self.lock:
            self.pct = max(self.pct, min(pct, 0.99))
            if part == "main":
                self.parts = {}
            else:
                self.parts.pop("main", None)  # parallel steps replace the previous step's message
            self.parts[part] = message
            self.cb(round(self.pct, 3), " · ".join(self.parts.values()))


def analyze(analysis_id: str, url: str, progress: Callable[[float, str], None] = lambda p, m: None) -> dict:
    t0 = time.time()
    prog = _Progress(progress)

    # 1. thread ---------------------------------------------------------------------------
    prog(0.03, "Scraping the thread with context.dev (~30-60 s)")
    try:
        post = scrape_thread(url)
    except Exception as e:
        logger.exception("thread scrape failed")
        if "rate limit" in str(e).lower() or "RATE_LIMITED" in str(e):
            raise AnalysisError("Reddit scraping is rate-limited right now. Please try again in a minute.") from e
        raise AnalysisError("We couldn't load this Reddit post (deleted, private or quarantined?).") from e
    store.write_json(analysis_id, "post.json", post)
    store.update(analysis_id, title=post.get("title"), subreddit=post.get("subreddit"))
    t_scrape = time.time() - t0

    # 2. items -----------------------------------------------------------------------------
    all_items = flatten_reddit_posts([post])
    items = [c for c in all_items
             if c.author.lower() not in BOTS and c.text.strip() not in ("", "[deleted]", "[removed]")]
    if len(items) < MIN_ITEMS:
        raise AnalysisError(f"Only {len(items)} readable comments on this post; we need at least {MIN_ITEMS}.")
    authors = pick_authors(items, MAX_AUTHORS)

    # 3. profiles (background) || narratives (this thread) -----------------------------------
    prog(0.30, f"Scraping {len(authors)} commenter profiles", "profiles")
    with ThreadPoolExecutor(1) as ex:
        fut = ex.submit(scrape_profiles, authors,
                        lambda d, n: prog(0.30 + 0.45 * d / max(n, 1), f"Profiles {d}/{n}", "profiles"),
                        PROFILE_DEADLINE_S)
        prog(0.32, f"Embedding {len(items)} comments (Gemini)", "narratives")
        cfg = engine_config(len(items))
        engine = SpatialEngine(cfg)
        engine._reduce = _locked(engine._reduce)  # UMAP
        engine._cluster_narratives = _locked(engine._cluster_narratives)  # HDBSCAN
        embeddings = engine.embed(items)
        prog(0.40, "Clustering and naming narratives (NVIDIA)", "narratives")
        result = engine.process(items, embeddings=embeddings)
        prog(0.45, f"{len(result.narratives)} narratives found", "narratives")
        store.write_json(analysis_id, "stage2.json", result.model_dump())
        accounts = fut.result()
    store.write_json(analysis_id, "accounts.json", accounts)

    # 4. scores ------------------------------------------------------------------------------
    prog(0.80, "Scoring account authenticity")
    scores = score_thread(post, items, accounts)
    store.write_json(analysis_id, "scores.json", scores)

    # 5. outputs -----------------------------------------------------------------------------
    prog(0.90, "Building the Reddit view and narrative map")
    model = getattr(engine.labeler, "model", None)
    _locked(write_report)(result, embeddings, store.run_dir(analysis_id) / "map.html", cfg,  # 2D UMAP inside
                          source=f"r/{post.get('subreddit')} · {(post.get('title') or '')[:90]}",
                          labels_source=f"LLM ({model})" if model else "c-TF-IDF keywords")
    warnings = _warnings(post, accounts, model)
    view = build_view(analysis_id, post, all_items, result, scores, accounts, warnings,
                      map_url=f"/api/analyses/{analysis_id}/map")
    store.write_json(analysis_id, "view.json", view)

    s = scores["summary"]
    top = next((n for n in view["narratives"] if n["id"] != NOISE_ID), None)
    return {
        "n_comments": sum(getattr(c, "kind", "") == "comment" for c in all_items),
        "n_narratives": len(result.narratives),
        "top_narrative": top["label"] if top else None,
        "n_scored": s.get("scored", 0),
        "mean_score": s.get("mean_score"),
        "label_counts": s.get("label_counts", {}),
        "timing_s": {"scrape": round(t_scrape, 1), "total": round(time.time() - t0, 1)},
    }


def _warnings(post: dict, accounts: dict, model: str | None) -> list[str]:
    w = []
    if not post.get("scores_from_html"):
        w.append("Comment scores unavailable; engagement weights use replies only.")
    rep, cap = post.get("reported_comment_count") or 0, post.get("captured_comment_count") or 0
    if rep > cap:
        w.append(f"Captured {cap} of {rep} comments (Reddit only renders the top of large threads).")
    missing = [n for n, a in accounts.items() if a.get("fetch_status") in ("timeout", "error")]
    if missing:
        w.append(f"{len(missing)} commenter profile(s) couldn't be fetched in time and are unscored.")
    if not model:
        w.append("Narrative names are keywords (LLM unavailable).")
    return w


def warmup() -> None:
    """Numba JIT-compiles UMAP/HDBSCAN on first use (~10-20 s); do it at startup, not in a user's job."""
    import numpy as np
    import hdbscan
    import umap
    x = np.random.default_rng(0).normal(size=(40, 16)).astype("float32")
    with NUMBA_LOCK:
        hdbscan.HDBSCAN(min_cluster_size=3).fit(umap.UMAP(n_neighbors=5, n_components=5, random_state=42).fit_transform(x))
        umap.UMAP(n_neighbors=5, n_components=2, random_state=42).fit_transform(x)  # report's 2D projection


if __name__ == "__main__":
    import json
    import sys

    from candor.urls import normalize_reddit_url

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    norm = normalize_reddit_url(sys.argv[1])
    if not norm:
        sys.exit("not a Reddit post URL")
    aid = store.create(sys.argv[1], norm[0], norm[1], visitor_id="cli", client_ip=None)
    out = analyze(aid, norm[0], progress=lambda p, m: print(f"[{p:>4.0%}] {m}", flush=True))
    store.update(aid, status="done", progress=1.0, message="Done", summary_json=json.dumps(out), finished_at=time.time())
    print(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"\nview: /view.html?id={aid}   files: {store.run_dir(aid)}")
