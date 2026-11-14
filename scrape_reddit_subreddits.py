"""
Dataset 2 (bot-heavy cohort): collect >= TARGET users from each subreddit in SUBREDDITS,
fetch their Reddit profiles, and write an accounts file in schemas/reddit_dataset2.schema.json
format that profile_authenticity_scorer.py can score.

How users are found (context.dev's reddit-subreddit/reddit-search parsers are refused by Reddit
and reddit-post returns no comments, so this uses JSON extraction on rendered pages):
    1. /r/<sub>/new/ listing  -> post authors + thread links         (~6 credits)
    2. most-commented threads -> commenters, until TARGET users        (~5 credits/thread)
    3. reddit-user parser     -> profile + up to 200 recent items      (~1 credit/user)

Usage:
    python scrape_reddit_subreddits.py [--target 60] [--subs AskReddit politics ...]

Outputs (all cached; re-runs skip finished steps):
    data/dataset2/subreddits/listings/<sub>.json
    data/dataset2/subreddits/threads/<thread_id>.json
    data/dataset2/subreddits/reddit_users/<username>.json
    data/dataset2/subreddits/reddit_accounts.json      <- score this
"""
from __future__ import annotations

import argparse
import json
import re
import types
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import scrape_reddit_profiles as profiles
from build_dataset1 import EXTRACT_INSTRUCTIONS, EXTRACT_SCHEMA, LIMITER, api_scrape, client

SUBREDDITS = ["AskReddit", "politics", "technology", "EmploymentLaw"]
OUT = Path("data/dataset2/subreddits")
SKIP_AUTHORS = {"automoderator", "[deleted]", "[removed]", "deleted", "removed", "reddit", "moderator"}
MAX_THREADS_PER_SUB = 10

LISTING_SCHEMA = {"type": "object", "properties": {"posts": {"type": "array", "items": {"type": "object", "properties": {
    "title": {"type": "string"},
    "author": {"type": ["string", "null"], "description": "username without u/"},
    "permalink": {"type": "string", "description": "full https://www.reddit.com/r/.../comments/... URL"},
    "comment_count": {"type": ["integer", "null"]},
    "posted_at": {"type": ["string", "null"]}},
    "required": ["title", "author", "permalink", "comment_count", "posted_at"]}}}, "required": ["posts"]}


def clean(author: str | None) -> str | None:
    a = (author or "").strip().removeprefix("u/").removeprefix("/u/")
    return a if a and a.lower() not in SKIP_AUTHORS and re.fullmatch(r"[A-Za-z0-9_-]{3,20}", a) else None


def scrape_json(url: str, schema: dict, instructions: str, scroll: int = 0) -> dict:
    body = {"url": url, "formats": {"json": True}, "jsonParams": {"schema": schema, "instructions": instructions}}
    if scroll:
        body["sharedParams"] = {"actions": [{"type": "scroll", "direction": "down", "amount": "viewport", "maxScrolls": scroll}]}
    LIMITER.wait()
    res = api_scrape(client(), body)
    j = res.get("json") or {}
    if not j.get("success"):
        raise RuntimeError(j.get("error_code") or j.get("message") or "extraction failed")
    return j["data"]


def cached(path: Path, fn):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = fn()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def thread_id(url: str) -> str | None:
    m = re.search(r"/comments/([a-z0-9]+)", url or "")
    return m.group(1) if m else None


def collect_users(sub: str, target: int) -> tuple[list[str], list[dict], list[str]]:
    """Returns (usernames, seed comments for the corpus, thread ids sampled)."""
    listing = cached(OUT / "listings" / f"{sub}.json", lambda: scrape_json(
        f"https://www.reddit.com/r/{sub}/new/", LISTING_SCHEMA,
        "List every post in this subreddit listing with its author and full permalink.", scroll=3))
    posts = listing.get("posts") or []
    users = list(dict.fromkeys(a for a in (clean(p.get("author")) for p in posts) if a))
    seeds, threads = [], []
    for p in posts:  # post titles are seed texts too
        if clean(p.get("author")) and thread_id(p.get("permalink")):
            seeds.append({"author": clean(p["author"]), "thread": thread_id(p["permalink"]),
                          "text": p.get("title") or "", "posted_at": p.get("posted_at")})
    ranked = sorted((p for p in posts if thread_id(p.get("permalink"))), key=lambda p: -(p.get("comment_count") or 0))
    for p in ranked[:MAX_THREADS_PER_SUB]:
        if len(users) >= target:
            break
        tid = thread_id(p["permalink"])
        try:
            page = cached(OUT / "threads" / f"{tid}.json", lambda: scrape_json(
                p["permalink"], EXTRACT_SCHEMA, EXTRACT_INSTRUCTIONS, scroll=4))
        except Exception as e:
            print(f"  ! {sub} thread {tid}: {e}")
            continue
        threads.append(tid)
        for it in page.get("items") or []:
            a = clean(it.get("author"))
            if a:
                seeds.append({"author": a, "thread": tid, "text": it.get("text") or "", "posted_at": it.get("created_time")})
                if a not in users:
                    users.append(a)
        print(f"  r/{sub}: {len(users)} users after {len(threads)} threads", flush=True)
    return users, seeds, threads


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=60, help="users to collect per subreddit (50 usable + margin)")
    ap.add_argument("--subs", nargs="+", default=SUBREDDITS)
    ap.add_argument("--limit", type=int, default=200, help="history items per profile")
    args = ap.parse_args()

    by_sub, all_seeds, seed_threads = {}, [], []
    for sub in args.subs:
        users, seeds, threads = collect_users(sub, args.target)
        by_sub[sub] = users
        all_seeds += seeds
        seed_threads += threads
        print(f"r/{sub}: {len(users)} users")

    # profiles: reuse the dataset-2 profile fetcher, pointed at this cohort's own cache dir
    profiles.RAW = OUT / "reddit_users"
    profiles.RAW.mkdir(parents=True, exist_ok=True)
    profiles.ARGS = types.SimpleNamespace(limit=args.limit)
    todo = list(dict.fromkeys(u for us in by_sub.values() for u in us))
    todo = [u for u in todo if not (profiles.RAW / f"{u}.json").exists()]
    print(f"fetching {len(todo)} profiles")
    c = client()
    with ThreadPoolExecutor(5) as ex:
        list(ex.map(lambda u: profiles.fetch(c, u, args.limit), todo))

    data = profiles.assemble()
    sources = defaultdict(list)
    for sub, users in by_sub.items():
        for u in users:
            sources[u].append(sub)
    for name, acc in data["accounts"].items():
        acc["source_subreddits"] = sources.get(name, [])
    data["accounts"] = {n: a for n, a in data["accounts"].items() if n in sources}
    data["scrape_meta"].update({"narrative_topic": None, "cohort": "bot-heavy subreddits",
                                "source_subreddits": args.subs, "seed_threads": seed_threads})
    data["seed_comments"] = all_seeds
    (OUT / "reddit_accounts.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    from collections import Counter
    acc = data["accounts"].values()
    print(f"\nwrote {len(data['accounts'])} accounts -> {OUT/'reddit_accounts.json'}")
    for sub in args.subs:
        sub_acc = [a for a in acc if sub in a["source_subreddits"]]
        print(f"  r/{sub:<14} {len(sub_acc):>3} accounts  status={dict(Counter(a['fetch_status'] for a in sub_acc))}")
