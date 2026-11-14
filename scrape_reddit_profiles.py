"""
Dataset 2 (part): scrape the Reddit profile + recent activity of every commenter in the
narrative (related) part of dataset 1, via context.dev's pre-built `reddit-user` parser
(no login, ~1 credit and ~1 s per profile). Output follows schemas/reddit_dataset2.schema.json.

Usage:
    python scrape_reddit_profiles.py [--all] [--limit 200] [--min 200]

    --all     also include commenters on unrelated items (default: related only)
    --limit   posts+comments of history to fetch per user (1-1000)
    --min     if fewer related authors exist, top up with unrelated authors to reach this many

Outputs:
    data/dataset2/reddit_users/<username>.json   raw parser response (cache; re-runs skip these)
    data/dataset2/reddit_accounts.json           all accounts + derived C1/C5 features
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from build_dataset1 import LIMITER, TOPIC, TOPIC_KEYWORDS, api_scrape, client

DS1 = Path("data/dataset1/dataset1.jsonl")
OUT = Path("data/dataset2")
RAW = OUT / "reddit_users"
DEFAULT_NAME_RE = re.compile(r"^[A-Z][a-z]+[-_][A-Z][a-z]+[-_]?\d{1,4}$")  # e.g. Fun_Fortune_8291


def pick_authors(include_all: bool, minimum: int) -> list[str]:
    rows = [json.loads(l) for l in DS1.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r["source_domain"] == "reddit.com" and r.get("author")]
    clean = lambda a: a.strip().removeprefix("u/").removeprefix("/u/")
    ok = lambda a: a and not a.startswith("[") and a.lower() not in {"automoderator", "deleted", "removed"}
    related = list(dict.fromkeys(clean(r["author"]) for r in rows if r["topic_label"] == "related" and ok(clean(r["author"]))))
    others = list(dict.fromkeys(clean(r["author"]) for r in rows if r["topic_label"] != "related" and ok(clean(r["author"]))))
    others = [a for a in others if a not in set(related)]
    if include_all:
        return related + others
    return related + others[:max(0, minimum - len(related))]


def fetch(c, username: str, limit: int) -> None:
    path = RAW / f"{username}.json"
    if path.exists():
        return
    body = {"url": f"https://www.reddit.com/user/{username}/overview/?sort=new", "formats": {"parse": True},
            "parseParams": {"type": "prebuilt", "parser": "reddit-user", "limit": limit}}
    for attempt in range(4):
        LIMITER.wait()
        try:
            res = api_scrape(c, body)
            break
        except Exception as e:
            msg = str(e)
            if "RATE_LIMITED" in msg and attempt < 3:
                time.sleep(60)
                continue
            status = "not_found" if "NOT_FOUND" in msg else "blocked" if "WEBSITE_ACCESS_ERROR" in msg else "error"
            res = {"parsed": {"success": False, "error_code": status, "message": msg[:500]}}
            break
    res["_username"], res["_fetched_at"] = username, datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    p = res.get("parsed") or {}
    d = p.get("data") or {}
    print(f"  {username:<26} {'ok' if p.get('success') else p.get('error_code')}  "
          f"comments={len(d.get('comments') or [])} posts={len(d.get('posts') or [])}", flush=True)


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t.lower())).strip()


def derive(user: dict, comments: list, posts: list, now: datetime) -> dict:
    ts = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    created = ts(user["created_at"]) if user.get("created_at") else None
    items = [(ts(x["posted_at"]), x) for x in comments + posts if x.get("posted_at")]
    items.sort(key=lambda t: t[0])
    times = [t for t, _ in items]
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    subs = Counter(x.get("subreddit") for _, x in items if x.get("subreddit"))
    texts = [(x.get("body") or x.get("title") or "", (x.get("post") or {}).get("id") or x.get("id")) for _, x in items]
    on_topic = [any(k in t.lower() for k in TOPIC_KEYWORDS) for t, _ in texts]
    hours = [0] * 24
    for t in times:
        hours[t.hour] += 1

    dup_pairs = 0
    long_texts = [(norm(t), tid) for t, tid in texts if len(norm(t)) >= 40]
    if len(long_texts) >= 2:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        sims = cosine_similarity(TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5)).fit_transform([t for t, _ in long_texts]))
        for i in range(len(long_texts)):
            for j in range(i + 1, len(long_texts)):
                if sims[i, j] >= 0.85 and long_texts[i][1] != long_texts[j][1]:
                    dup_pairs += 1

    avatar = user.get("avatar_url") or ""
    return {
        "account_age_days": round((now - created).total_seconds() / 86400, 1) if created else None,
        "has_default_avatar": (not avatar) or "avatar_default" in avatar,
        "username_is_default_pattern": bool(DEFAULT_NAME_RE.match(user.get("username") or "")),
        "n_items": len(items),
        "first_activity_at": times[0].isoformat() if times else None,
        "distinct_subreddits": len(subs),
        "top_subreddits": [s for s, _ in subs.most_common(5)],
        "share_on_topic": round(sum(on_topic) / len(on_topic), 3) if on_topic else None,
        "near_duplicate_own_comments": dup_pairs,
        "median_gap_seconds": median(gaps) if gaps else None,
        "active_hours_utc": hours,
    }


def assemble() -> dict:
    now = datetime.now(timezone.utc)
    accounts = {}
    for p in sorted(RAW.glob("*.json")):
        res = json.loads(p.read_text(encoding="utf-8"))
        name, parsed = res["_username"], res.get("parsed") or {}
        d = parsed.get("data") or {}
        if not parsed.get("success") or not d.get("user"):
            code = (parsed.get("error_code") or "error").lower()
            status = {"website_access_error": "blocked"}.get(code, code)
            accounts[name] = {"username": name,
                              "fetch_status": status if status in ("not_found", "blocked") else "error"}
            continue
        user, comments, posts = d["user"], d.get("comments") or [], d.get("posts") or []
        accounts[name] = {
            "username": name,
            "fetch_status": "suspended" if user.get("is_suspended") else "ok",
            "user": user,
            "trophies": d.get("trophies"),
            "moderated_communities": d.get("moderated_communities"),
            "history_visible": bool(comments or posts),
            "comments": comments,
            "posts": posts,
            "derived": derive(user, comments, posts, now),
        }
    return {"scrape_meta": {"scraped_at": now.isoformat(), "narrative_topic": TOPIC,
                            "parser_limits": {"reddit-user": ARGS.limit}},
            "accounts": accounts}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--min", type=int, default=200)
    ARGS = ap.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    authors = pick_authors(ARGS.all, ARGS.min)
    todo = [a for a in authors if not (RAW / f"{a}.json").exists()]
    print(f"{len(authors)} authors ({len(authors) - len(todo)} cached), fetching {len(todo)}")
    c = client()
    with ThreadPoolExecutor(5) as ex:
        list(ex.map(lambda a: fetch(c, a, ARGS.limit), todo))

    data = assemble()
    (OUT / "reddit_accounts.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    acc = data["accounts"].values()
    ok = [a for a in acc if a["fetch_status"] == "ok"]
    print(f"\nwrote {len(data['accounts'])} accounts -> {OUT/'reddit_accounts.json'}")
    print("status:", dict(Counter(a["fetch_status"] for a in acc)))
    print("history visible:", sum(a["history_visible"] for a in ok), "/", len(ok))
    ages = sorted(a["derived"]["account_age_days"] for a in ok if a["derived"]["account_age_days"] is not None)
    if ages:
        print(f"account age days: median {ages[len(ages)//2]:.0f}, under 180 days: {sum(x < 180 for x in ages)}")
    print("default-pattern usernames:", sum(a["derived"]["username_is_default_pattern"] for a in ok))
