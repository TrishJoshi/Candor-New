"""Live analysis of one Reddit post: scrape (Stage 1) -> spatial engine (Stage 2) -> report.

    analyze(url, progress) -> dict   (called by candor.api in a worker thread)

Writes, per run:
    web/reports/<post>-<stamp>.html   the Narrative Map, same page as narrative-buckets/out/stage2_report_*.html
    data/live/<post>-<stamp>/post.json     base-format scrape (input for Stage 3 account scoring)
    data/live/<post>-<stamp>/stage2.json   ProcessingResult

Run without the API:  python -m candor.live <reddit_url>
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "web" / "reports"
RUNS = ROOT / "data" / "live"
BOTS = {"automoderator", "remindmebot", "sneakpeekbot", "[deleted]", "[removed]"}
MIN_ITEMS = 8  # below this there is nothing to cluster
# Numba's default threading layer aborts the whole process ("Concurrent access has been detected")
# when two threads run UMAP/HDBSCAN at once, e.g. two concurrent jobs or a job during warmup.
# Only the numba steps are serialised; embedding and LLM labelling still overlap across jobs.
NUMBA_LOCK = threading.Lock()


def _locked(fn):
    def run(*args, **kwargs):
        with NUMBA_LOCK:
            return fn(*args, **kwargs)
    return run

logger = logging.getLogger(__name__)


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

from candor.ingest import flatten_reddit_posts  # noqa: E402  (narrative-buckets, via candor.__path__)
from candor.schemas import NOISE_ID  # noqa: E402
from candor.scrape import scrape_thread  # noqa: E402
from candor.spatial_engine import SpatialEngine, SpatialEngineConfig  # noqa: E402
from candor.visualize import write_report  # noqa: E402


def engine_config(n: int) -> SpatialEngineConfig:
    """Stage 2 defaults are tuned for ~4k comments; one thread has ~35-300.
    Verified on two live threads (34 and 36 items): min_cluster_size 3 + eom -> 2-3 narratives, 15-20% noise."""
    return SpatialEngineConfig(
        hdbscan_min_cluster_size=max(3, round(n / 25)),
        hdbscan_selection_method="eom",
        umap_n_neighbors=min(15, max(2, n // 3)),
    )


def engagement_weight(upvotes: int | None, replies: int) -> float:
    return math.log1p(max(upvotes or 0, 0)) + 0.5 * math.log1p(replies)


def summarise(post: dict, result) -> dict:
    """Narrative buckets as JSON for the job response (Stage 3 adds account scores to these)."""
    comments = [c for c in result.comments if getattr(c, "kind", "") == "comment"]
    n_replies = defaultdict(int)
    for c in comments:
        n_replies[getattr(c, "parent_id", None)] += 1
    weight = {c.comment_id: engagement_weight(getattr(c, "upvote_count", 0), n_replies[c.comment_id]) for c in comments}
    total_w = sum(weight.values()) or 1.0
    groups = defaultdict(list)
    for c in comments:
        groups[c.narrative_id].append(c)
    labels = {n.narrative_id: n for n in result.narratives}
    topics = {t.topic_id: t.label for t in result.topics}
    buckets = []
    for nid, cs in groups.items():
        n = labels.get(nid)
        top = sorted(cs, key=lambda c: -weight[c.comment_id])[:6]
        buckets.append({
            "id": nid,
            "label": n.label if n else "Other / short replies",
            "topic": topics.get(n.topic_id) if n else None,
            "n_comments": len(cs),
            "share": round(len(cs) / len(comments), 3),
            "weighted_share": round(sum(weight[c.comment_id] for c in cs) / total_w, 3),
            "keywords": [k.word for k in n.keywords[:8]] if n else [],
            "top_comments": [{"id": c.comment_id, "author": c.author, "body": c.text[:500],
                              "upvotes": getattr(c, "upvote_count", None), "n_replies": n_replies[c.comment_id],
                              "engagement_weight": round(weight[c.comment_id], 3)} for c in top],
        })
    buckets.sort(key=lambda b: (b["id"] == NOISE_ID, -b["weighted_share"]))
    return {
        "post": {k: post.get(k) for k in ("post_id", "title", "subreddit", "author", "upvote_count", "permalink",
                                          "reported_comment_count", "captured_comment_count")},
        "buckets": buckets,
        "n_narratives": len(result.narratives),
        "n_noise": sum(c.narrative_id == NOISE_ID for c in comments),
        "warnings": [w for w in [
            None if post.get("scores_from_html") else "Comment scores unavailable; weights use replies only",
            f"Captured {post.get('captured_comment_count')} of {post.get('reported_comment_count')} comments "
            "(Reddit renders only the top of large threads)"
            if (post.get("reported_comment_count") or 0) > (post.get("captured_comment_count") or 0) else None,
        ] if w],
    }


def analyze(url: str, progress: Callable[[float, str], None] = lambda p, m: None) -> dict:
    t0 = time.time()
    progress(0.05, "Scraping the thread with context.dev (~40-60 s)")
    post = scrape_thread(url)
    t_scrape = time.time() - t0

    items = [c for c in flatten_reddit_posts([post])
             if c.author.lower() not in BOTS and c.text.strip() not in ("", "[deleted]", "[removed]")]
    if len(items) < MIN_ITEMS:
        raise ValueError(f"Only {len(items)} readable comments on this post; need at least {MIN_ITEMS}.")

    progress(0.55, f"Embedding {len(items)} comments")
    cfg = engine_config(len(items))
    engine = SpatialEngine(cfg)
    engine._reduce = _locked(engine._reduce)  # UMAP
    engine._cluster_narratives = _locked(engine._cluster_narratives)  # HDBSCAN
    embeddings = engine.embed(items)
    progress(0.65, "Clustering narratives and labelling them")
    result = engine.process(items, embeddings=embeddings)

    progress(0.92, "Building the narrative map")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{post['post_id']}-{stamp}"
    REPORTS.mkdir(parents=True, exist_ok=True)
    run_dir = RUNS / name
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "post.json").write_text(json.dumps([post], ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "stage2.json").write_text(result.model_dump_json(indent=1), encoding="utf-8")
    model = getattr(engine.labeler, "model", None)
    _locked(write_report)(result, embeddings, REPORTS / f"{name}.html", cfg,  # 2D UMAP inside
                 source=f"r/{post['subreddit']} · {post['title'][:90]}",
                 labels_source=f"LLM ({model})" if model else "c-TF-IDF keywords")

    report = summarise(post, result)
    report["report_url"] = f"/reports/{name}.html"
    report["timing_s"] = {"scrape": round(t_scrape, 1), "total": round(time.time() - t0, 1)}
    return report


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
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    out = analyze(sys.argv[1], progress=lambda p, m: print(f"[{p:>4.0%}] {m}", flush=True))
    print(json.dumps({k: v for k, v in out.items() if k != "buckets"}, indent=1, ensure_ascii=False))
    for b in out["buckets"]:
        print(f"  {b['weighted_share']:>6.1%} weighted  {b['n_comments']:>3} comments  {b['label']}")
