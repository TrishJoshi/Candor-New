"""Stage 1b (live): scrape the Reddit profiles of a thread's most engaged commenters.

Uses context.dev's prebuilt ``reddit-user`` parser (~1 credit, ~1-3 s per profile), the same
call scrape_reddit_profiles.py makes for dataset 2, so live accounts have the exact shape the
scorer was built on. All calls share build_dataset1.LIMITER with the thread scrape.

    pick_authors(items, max_authors)                 -> [username, ...]
    scrape_profiles(names, progress, deadline_s)     -> {username: account}

Profiles are cached in data/cache/profiles/<username>.json for PROFILE_CACHE_TTL seconds
(default 24 h). Only the parsed result is cached (not the page HTML). A failed or timed-out
profile never fails the job: the author is reported as unassessed.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from build_dataset1 import LIMITER, api_scrape, client

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "profiles"
CACHE_TTL_S = float(os.environ.get("PROFILE_CACHE_TTL", 24 * 3600))
HISTORY_LIMIT = 200  # items of history per profile; same as dataset 2 (scorer thresholds assume it)
BOTS = {"automoderator", "remindmebot", "sneakpeekbot", "[deleted]", "[removed]", "deleted", "removed"}
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")


def engagement_weight(upvotes: int | None, replies: int) -> float:
    return math.log1p(max(upvotes or 0, 0)) + 0.5 * math.log1p(replies)


def pick_authors(items: Iterable, max_authors: int = 20) -> list[str]:
    """Most engaged commenters first (summed engagement weight of their comments).

    ``items`` are flattened Comment objects (kind/parent_id/upvote_count extras from ingest).
    """
    items = list(items)
    n_replies: dict[str, int] = defaultdict(int)
    for c in items:
        n_replies[getattr(c, "parent_id", None)] += 1
    weight: dict[str, float] = defaultdict(float)
    for c in items:
        name = (c.author or "").removeprefix("u/")
        if name.lower() in BOTS or not _USERNAME_RE.match(name):
            continue
        weight[name] += engagement_weight(getattr(c, "upvote_count", 0), n_replies[c.comment_id]) + 0.01
    return [n for n, _ in sorted(weight.items(), key=lambda kv: -kv[1])][:max_authors]


# ------------------------------------------------------------------------------- fetching
def _cache_path(username: str) -> Path:
    return CACHE / f"{username.lower()}.json"


def _read_cache(username: str) -> dict | None:
    p = _cache_path(username)
    try:
        res = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    age = time.time() - res.get("_fetched_ts", 0)
    return res if age < CACHE_TTL_S else None


def _write_cache(username: str, res: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    # temp file + os.replace: two jobs fetching the same user can't leave a half-written file
    fd, tmp = tempfile.mkstemp(dir=CACHE, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    os.replace(tmp, _cache_path(username))


def fetch_profile(username: str, c=None) -> dict:
    """Parsed reddit-user response {success, data|error_code}, from cache when fresh."""
    if (cached := _read_cache(username)) is not None:
        return cached
    c = c or client()
    body = {"url": f"https://www.reddit.com/user/{username}/overview/?sort=new", "formats": {"parse": True},
            "parseParams": {"type": "prebuilt", "parser": "reddit-user", "limit": HISTORY_LIMIT}}
    parsed: dict
    for attempt in range(3):
        LIMITER.wait()
        try:
            parsed = api_scrape(c, body).get("parsed") or {"success": False, "error_code": "error"}
            break
        except Exception as e:  # context.dev raises with the error code in the message
            msg = str(e)
            if "RATE_LIMITED" in msg and attempt < 2:
                time.sleep(30)
                continue
            code = "not_found" if "NOT_FOUND" in msg else "blocked" if "WEBSITE_ACCESS_ERROR" in msg else "error"
            parsed = {"success": False, "error_code": code, "message": msg[:300]}
            break
    res = {"parsed": parsed, "_username": username, "_fetched_ts": time.time(),
           "_fetched_at": datetime.now(timezone.utc).isoformat()}
    code = (parsed.get("error_code") or "").lower()  # API sends NOT_FOUND, our own mapping not_found
    if parsed.get("success") or code in ("not_found", "blocked", "website_access_error"):
        _write_cache(username, res)  # transient errors aren't cached, so a retry gets another go
    return res


def to_account(username: str, res: dict, now: datetime | None = None) -> dict:
    """Same account shape as scrape_reddit_profiles.assemble() (what the scorer consumes)."""
    from scrape_reddit_profiles import derive  # reuse the dataset-2 feature code

    parsed = res.get("parsed") or {}
    d = parsed.get("data") or {}
    if not parsed.get("success") or not d.get("user"):
        code = (parsed.get("error_code") or "error").lower()
        status = {"website_access_error": "blocked"}.get(code, code)
        return {"username": username,
                "fetch_status": status if status in ("not_found", "blocked", "timeout") else "error"}
    user, comments, posts = d["user"], d.get("comments") or [], d.get("posts") or []
    return {
        "username": username,
        "fetch_status": "suspended" if user.get("is_suspended") else "ok",
        "user": user,
        "history_visible": bool(comments or posts),
        "comments": comments,
        "posts": posts,
        "derived": derive(user, comments, posts, now or datetime.now(timezone.utc)),
    }


def scrape_profiles(names: list[str], progress: Callable[[int, int], None] = lambda done, total: None,
                    deadline_s: float = 120.0, workers: int = 5) -> dict[str, dict]:
    """Fetch ``names`` in parallel. Profiles still running at the deadline become ``timeout``."""
    accounts: dict[str, dict] = {}
    if not names:
        return accounts
    c = client()
    ex = ThreadPoolExecutor(workers)
    futures = {ex.submit(fetch_profile, n, c): n for n in names}
    done_count = 0
    start = time.monotonic()
    pending = set(futures)
    while pending:
        remaining = deadline_s - (time.monotonic() - start)
        if remaining <= 0:
            break
        finished, pending = wait(pending, timeout=min(remaining, 2.0), return_when="FIRST_COMPLETED")
        for f in finished:
            name = futures[f]
            try:
                accounts[name] = to_account(name, f.result())
            except Exception as e:  # never fail the job over one profile
                logger.warning("profile %s failed: %s", name, e)
                accounts[name] = {"username": name, "fetch_status": "error"}
            done_count += 1
            progress(done_count, len(names))
    for f in pending:
        f.cancel()
        accounts[futures[f]] = {"username": futures[f], "fetch_status": "timeout"}
    ex.shutdown(wait=False, cancel_futures=True)
    return {n: accounts[n] for n in names}
