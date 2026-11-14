"""
Fetch N posts (with threaded comments + scores) from each subreddit and export them in the
RedditPostExport format (schemas/reddit_post_export.schema.json).

Reddit blocks direct API/.json access, and context.dev's reddit-post parser returns no comments,
so this renders the pages through context.dev and reads the <shreddit-post>/<shreddit-comment>
elements, whose attributes carry exact ids, authors, scores, timestamps and parent ids.

    1. /r/<sub>/<sort>/ listing, scrolled  -> post ids + permalinks        (~2 credits/sub)
    2. each thread page, scrolled           -> post body + comment tree     (~2 credits/post)

Usage:
    python scrape_reddit_posts.py [--n 50] [--sort hot] [--subs AskReddit politics ...]

Outputs:
    data/reddit-dataset-posts/raw/<sub>_listing.json     cached listing (post ids/permalinks)
    data/reddit-dataset-posts/raw/threads/<post_id>.json cached parsed thread (re-runs skip these)
    data/reddit-dataset-posts/reddit_posts.json          array of RedditPostExport objects

Limits: Reddit only renders the comments visible after scrolling (collapsed "more replies" are
not expanded), so large threads are partial. Each post records how many comments Reddit reports
vs how many were captured.
"""
from __future__ import annotations

import argparse
import html as htmllib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from build_dataset1 import LIMITER, api_scrape, client

SUBREDDITS = ["AskReddit", "politics", "technology", "EmploymentLaw"]
OUT = Path("data/reddit-dataset-posts")
RAW = OUT / "raw"


# ---------------------------------------------------------------------------- fetching
def fetch_html(c, url: str, scrolls: int) -> str:
    body = {"url": url, "formats": {"html": True},
            "sharedParams": {"actions": [{"type": "scroll", "direction": "down", "amount": "viewport",
                                          "maxScrolls": scrolls}]}}
    for attempt in range(4):
        LIMITER.wait()
        try:
            res = api_scrape(c, body)
        except Exception as e:
            if "RATE_LIMITED" in str(e) and attempt < 3:
                time.sleep(60)
                continue
            raise
        h = res.get("html") or {}
        if h.get("success") and h.get("data"):
            return h["data"]
        if h.get("error_code") == "WEBSITE_BLOCKED" and attempt < 3:
            time.sleep(10)
            continue
        raise RuntimeError(h.get("error_code") or h.get("message") or "no html")
    raise RuntimeError("gave up")


# ---------------------------------------------------------------------------- parsing
def attrs(tag: str) -> dict:
    return {k: htmllib.unescape(v) for k, v in re.findall(r'([\w:-]+)="([^"]*)"', tag)}


def ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("+0000", "+00:00")).timestamp()
    except ValueError:
        return None


def to_int(s) -> int:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


class _Text(HTMLParser):
    BLOCK = {"p", "li", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, a):
        if tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, d):
        self.parts.append(re.sub(r"\s+", " ", d))


def html_text(fragment: str) -> str:
    p = _Text()
    p.feed(fragment)
    lines = [l.strip() for l in "".join(p.parts).split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def div_by_id(page: str, el_id: str) -> str | None:
    """Inner HTML of <div id="el_id">, matching nested divs."""
    m = re.search(r'<div\b[^>]*\bid="%s"[^>]*>' % re.escape(el_id), page)
    if not m:
        return None
    depth, i = 1, m.end()
    for t in re.finditer(r"<(/?)div\b", page[i:]):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            return page[i:i + t.start()]
    return page[i:]


def parse_listing(page: str) -> list[dict]:
    out, seen = [], set()
    for tag in re.findall(r"<shreddit-post\b[^>]*>", page):
        a = attrs(tag)
        if a.get("id", "").startswith("t3_") and a["id"] not in seen and a.get("permalink"):
            seen.add(a["id"])
            out.append({"post_id": a["id"], "permalink": "https://www.reddit.com" + a["permalink"],
                        "title": a.get("post-title"), "promoted": "promoted" in a or a.get("is-promoted") == "true"})
    return out


def parse_thread(page: str, sub: str) -> dict:
    m = re.search(r"<shreddit-post\b[^>]*>", page)
    if not m:
        raise RuntimeError("no <shreddit-post> on page")
    p = attrs(m.group(0))
    pid = p["id"]
    body_html = div_by_id(page, f"{pid}-post-rtjson-content")
    if body_html is None:  # some self-posts use a plain text-body slot
        tb = re.search(r'<div[^>]*slot="text-body"[^>]*>(.*?)</div>\s*</div>', page, re.S)
        body_html = tb.group(1) if tb else ""
    comments, order = {}, []
    for tag in re.findall(r"<shreddit-comment\b[^>]*>", page):
        a = attrs(tag)
        cid = a.get("thingid")
        if not cid or cid in comments:
            continue
        b = div_by_id(page, f"{cid}-post-rtjson-content")
        deleted = "deleted" in (a.get("is-comment-deleted") or "") or a.get("author") in (None, "", "[deleted]")
        comments[cid] = {
            "comment_id": cid,
            "author": a.get("author") or "[deleted]",
            "body": html_text(b) if b is not None else ("[deleted]" if deleted else ""),
            "upvote_count": to_int(a.get("score")),
            "created_utc": ts(a.get("created")),
            "replies": [],
            "_parent": a.get("parentid"),
            "_depth": to_int(a.get("depth")),
        }
        order.append(cid)
    roots = []
    for cid in order:
        c = comments[cid]
        parent = c.pop("_parent")
        c.pop("_depth")
        if parent and parent in comments:
            comments[parent]["replies"].append(c)
        else:
            roots.append(c)
    for c in comments.values():
        if c["created_utc"] is None:
            c.pop("created_utc")
    post = {
        "post_id": pid,
        "title": p.get("post-title") or "",
        "author": p.get("author") or "[deleted]",
        "subreddit": p.get("subreddit-name") or sub,
        "body": html_text(body_html or ""),
        "upvote_count": to_int(p.get("score")),
        "created_utc": ts(p.get("created-timestamp")),
        "comments": roots,
        # extra provenance (allowed: schema doesn't forbid additional properties)
        "permalink": "https://www.reddit.com" + p.get("permalink", ""),
        "post_type": p.get("post-type"),
        "link_url": p.get("content-href") if p.get("post-type") != "text" else None,
        "upvote_ratio": float(p["upvote-ratio"]) if p.get("upvote-ratio") else None,
        "reported_comment_count": to_int(p.get("comment-count")),
        "captured_comment_count": len(comments),
    }
    if post["created_utc"] is None:
        post.pop("created_utc")
    return post


# ---------------------------------------------------------------------------- main
def get_thread(c, sub: str, item: dict, scrolls: int) -> dict | None:
    path = RAW / "threads" / f"{item['post_id']}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        post = parse_thread(fetch_html(c, item["permalink"], scrolls), sub)
    except Exception as e:
        print(f"  ! {sub} {item['post_id']}: {str(e)[:200]}", flush=True)
        return None
    path.write_text(json.dumps(post, ensure_ascii=False), encoding="utf-8")
    print(f"  r/{sub:<14} {item['post_id']}  comments {post['captured_comment_count']}/{post['reported_comment_count']}", flush=True)
    return post


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50, help="posts per subreddit")
    ap.add_argument("--sort", default="hot", choices=["hot", "new", "top", "rising"])
    ap.add_argument("--subs", nargs="+", default=SUBREDDITS)
    ap.add_argument("--thread-scrolls", type=int, default=8)
    args = ap.parse_args()

    (RAW / "threads").mkdir(parents=True, exist_ok=True)
    c = client()
    all_posts = []
    for sub in args.subs:
        lp = RAW / f"{sub}_{args.sort}_listing.json"
        if lp.exists():
            listing = json.loads(lp.read_text(encoding="utf-8"))
        else:
            scrolls, listing = 15, []
            while scrolls <= 45:
                listing = parse_listing(fetch_html(c, f"https://www.reddit.com/r/{sub}/{args.sort}/", scrolls))
                listing = [x for x in listing if not x["promoted"]]
                print(f"r/{sub}: {len(listing)} posts in listing ({scrolls} scrolls)", flush=True)
                if len(listing) >= args.n:
                    break
                scrolls += 15
            lp.write_text(json.dumps(listing, ensure_ascii=False, indent=1), encoding="utf-8")
        listing = listing[:args.n]
        with ThreadPoolExecutor(5) as ex:
            posts = list(ex.map(lambda it: get_thread(c, sub, it, args.thread_scrolls), listing))
        posts = [p for p in posts if p]
        print(f"r/{sub}: {len(posts)}/{len(listing)} posts fetched", flush=True)
        all_posts += posts

    (OUT / "reddit_posts.json").write_text(json.dumps(all_posts, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nwrote {len(all_posts)} posts, "
          f"{sum(p['captured_comment_count'] for p in all_posts)} comments -> {OUT / 'reddit_posts.json'}")
