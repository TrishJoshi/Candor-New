"""The Dashboard tab's narrative map (web/dashboard/narrative-map.html), with authenticity.

    python -m candor.dashboard fetch   # scrape every author's profile (~1 credit each; resumable)
    python -m candor.dashboard score   # score the authors, thread by thread -> data/dashboard/author_scores.json
    python -m candor.dashboard summarize  # AI summaries of every narrative and topic -> data/dashboard/summaries.json
    python -m candor.dashboard build   # write web/dashboard/narrative-map.html
    python -m candor.dashboard all     # fetch, then score (the long batch; run detached on the server)

Inputs (data/dashboard/, not in git): stage2_result.json and embeddings_gemini.npz, the Stage 2
run over reddit_posts.json (4,453 posts and comments from four subreddits).

Authors are scored the way a live analysis scores a thread's commenters (candor.scoring.score_thread):
each author in the context of the thread they commented in, so the single-issue (C5) and
copy-paste (C2/C3) checks see that thread. An author active in several threads is scored in the
thread where they have the most comments. Profiles are cached in data/cache/profiles/ for good
(PROFILE_CACHE_TTL is raised here), so `score` and `build` cost no credits.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault("PROFILE_CACHE_TTL", str(10 * 365 * 24 * 3600))  # before candor.profiles reads it
os.environ.setdefault("SCRAPE_PER_MINUTE", "60")  # leave headroom on the shared key for live analyses

from candor import live  # noqa: E402,F401  (loads the .env files)
from candor.profiles import fetch_profile, to_account  # noqa: E402
from candor.schemas import ProcessingResult  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "dashboard"
SCORES = DIR / "author_scores.json"
OUT = ROOT / "web" / "dashboard" / "narrative-map.html"
BOTS = live.BOTS
logger = logging.getLogger(__name__)


def load_result() -> ProcessingResult:
    return ProcessingResult.model_validate_json((DIR / "stage2_result.json").read_text(encoding="utf-8"))


def authors_by_engagement(result: ProcessingResult) -> list[str]:
    """Every real author, most engaged first (so a partial run covers the most engagement)."""
    eng: Counter = Counter()
    for c in result.comments:
        if c.author and c.author.lower() not in BOTS:
            eng[c.author] += math.log1p(max(getattr(c, "upvote_count", 0) or 0, 0)) + 1
    return [a for a, _ in eng.most_common()]


def fetch(workers: int = 6) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from build_dataset1 import client

    names = authors_by_engagement(load_result())
    c = client()
    t0, done, status = time.time(), 0, Counter()
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fetch_profile, n, c): n for n in names}
        for f in as_completed(futs):
            done += 1
            try:
                p = f.result().get("parsed") or {}
                status["ok" if p.get("success") else (p.get("error_code") or "error").lower()] += 1
            except Exception as e:  # keep going; a rerun retries what wasn't cached
                status["exception"] += 1
                logger.warning("%s: %s", futs[f], e)
            if done % 50 == 0 or done == len(names):
                logger.info("%d/%d profiles (%.0f s) %s", done, len(names), time.time() - t0, dict(status))


def score() -> None:
    from candor.scoring import score_thread
    from candor.profiles import _read_cache

    result = load_result()
    by_thread: dict[str, list] = defaultdict(list)
    for c in result.comments:
        by_thread[c.post_id or c.comment_id].append(c)
    # each author is scored once, in the thread where they wrote the most
    home: dict[str, str] = {}
    counts: Counter = Counter((c.author, c.post_id or c.comment_id) for c in result.comments)
    for (a, t), _ in sorted(counts.items(), key=lambda kv: -kv[1]):
        home.setdefault(a, t)
    out: dict[str, dict] = {}
    missing = 0
    for tid, items in by_thread.items():
        names = sorted({c.author for c in items if c.author and c.author.lower() not in BOTS and home.get(c.author) == tid})
        accounts = {}
        for n in names:
            res = _read_cache(n)
            if res is None:
                missing += 1
                continue
            accounts[n] = to_account(n, res)
        if not accounts:
            continue
        post = next((c for c in items if getattr(c, "kind", "") == "post"), None)
        post_d = {"post_id": tid, "title": getattr(post, "title", None) or (post.text[:200] if post else ""),
                  "body": post.text if post else ""}
        texts = [c for c in items if c.author and c.author.lower() not in BOTS]
        s = score_thread(post_d, texts, accounts)
        for p in s["profiles"]:
            out[p["username"]] = {"score": p.get("authenticity_score"), "label": p.get("authenticity_label"),
                                  "fetch": p.get("fetch_status")}
    DIR.mkdir(parents=True, exist_ok=True)
    SCORES.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    labels = Counter(v["label"] for v in out.values())
    logger.info("scored %d authors (%d not fetched yet): %s", len(out), missing, dict(labels))


SUMMARIES = DIR / "summaries.json"


def summarize() -> None:
    from candor.labeling import add_summaries, default_labeler

    result = load_result()
    n = add_summaries(result, default_labeler())
    SUMMARIES.write_text(json.dumps({
        "narratives": {n.narrative_id: n.summary for n in result.narratives},
        "topics": {t.topic_id: t.summary for t in result.topics},
    }, ensure_ascii=False, indent=0), encoding="utf-8")
    logger.info("wrote %d summaries to %s", n, SUMMARIES)


def _apply_summaries(result: ProcessingResult) -> None:
    if not SUMMARIES.exists():
        return
    s = json.loads(SUMMARIES.read_text(encoding="utf-8"))
    for n in result.narratives:
        n.summary = s["narratives"].get(str(n.narrative_id))
    for t in result.topics:
        t.summary = s["topics"].get(str(t.topic_id))


def build() -> None:
    import numpy as np

    from candor.spatial_engine import SpatialEngineConfig
    from candor.visualize import write_report

    result = load_result()
    _apply_summaries(result)
    emb = np.load(DIR / "embeddings_gemini.npz")["embeddings"]
    authors = None
    if SCORES.exists():
        authors = {n: v for n, v in json.loads(SCORES.read_text(encoding="utf-8")).items()
                   if v.get("label") != "no_data"}
    cfg = SpatialEngineConfig(embedding_backend="gemini", hdbscan_min_cluster_size=10, hdbscan_selection_method="leaf")
    write_report(result, emb, OUT, cfg, source="reddit_posts.json",
                 labels_source=f"LLM ({os.environ.get('NVIDIA_MODEL', 'nemotron-3.5-super-vl')})", authors=authors)
    logger.info("wrote %s (%s authors scored)", OUT, len(authors) if authors else "no")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    def run_all() -> None:
        fetch()
        fetch()  # second pass retries transient errors (they aren't cached)
        score()

    {"fetch": fetch, "score": score, "summarize": summarize, "build": build, "all": run_all}.get(cmd, lambda: sys.exit(__doc__))()
