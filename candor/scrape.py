"""Stage 1 (live): scrape one Reddit thread with context.dev into the base post format.

The base format is what scrape_reddit_posts.py writes and narrative-buckets' ingest reads
(``reddit_posts.json``): ``{post_id, title, body, author, subreddit, upvote_count, created_utc,
comments: [{comment_id, author, body, upvote_count, created_utc, replies: [...]}], ...}``.

One context.dev call asks for two formats:
  * ``json``: the base format itself is the extraction schema (nested comments -> replies).
    Verified 2026-11-14: real t1_/t3_ ids and verbatim text. A flat schema with parent_id
    stopped after ~5 comments, so the nested schema is used.
  * ``html``: the rendered page. Its <shreddit-comment> attributes carry the exact score,
    author and timestamp. The JSON extractor never returns comment scores (Reddit's visible
    text has none), and it sometimes reuses an id for a deleted comment's placeholder text,
    so the HTML is used to repair both by comment id.
If JSON extraction fails, the HTML alone is parsed into the same format (parse_thread).

Limit: Reddit renders ~35 top comments before "more replies"; we get those, not the whole thread.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime

from build_dataset1 import LIMITER, api_scrape, client
from scrape_reddit_posts import parse_thread

logger = logging.getLogger(__name__)

SCROLLS = 6
PLACEHOLDERS = {"comment deleted by user": "[deleted]", "comment removed by moderator": "[removed]",
                "deleted": "[deleted]", "removed": "[removed]", "[deleted]": "[deleted]", "[removed]": "[removed]"}

_COMMENT = {
    "comment_id": {"type": "string", "description": "t1_ id of the comment, from its permalink or element id"},
    "author": {"type": "string", "description": "username without u/; '[deleted]' if deleted"},
    "body": {"type": "string", "description": "exact comment text"},
    "upvote_count": {"type": ["integer", "null"]},
    "created_utc": {"type": ["string", "null"], "description": "ISO 8601 timestamp"},
}


def _node(depth: int) -> dict:
    props = dict(_COMMENT)
    if depth > 0:
        props["replies"] = {"type": "array", "items": _node(depth - 1)}
    return {"type": "object", "properties": props, "required": list(_COMMENT)}


BASE_SCHEMA = {
    "type": "object",
    "properties": {
        "post_id": {"type": "string", "description": "t3_ id of the post, e.g. t3_1xvsfrn"},
        "title": {"type": "string"},
        "body": {"type": "string", "description": "self-text of the post, empty if none"},
        "author": {"type": "string", "description": "username without u/"},
        "subreddit": {"type": "string", "description": "without r/"},
        "upvote_count": {"type": ["integer", "null"]},
        "created_utc": {"type": ["string", "null"], "description": "ISO 8601 timestamp"},
        "reported_comment_count": {"type": ["integer", "null"], "description": "comment count shown on the post"},
        "comments": {"type": "array", "items": _node(3)},  # top level + 3 reply levels
    },
    "required": ["post_id", "title", "comments"],
}
INSTRUCTIONS = (
    "This is a Reddit thread. Extract the post and EVERY visible comment and reply, nesting replies under "
    "their parent. Copy text verbatim, do not summarise or translate. Ids: use the thing ids (t3_ for the "
    "post, t1_ for comments) from permalinks or element ids. Convert relative times to absolute ISO 8601."
)


def _epoch(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _walk(nodes):
    for n in nodes or []:
        yield n
        yield from _walk(n.get("replies"))


def _post_id_from_url(url: str) -> str | None:
    m = re.search(r"/comments/([a-z0-9]+)", url)
    return f"t3_{m.group(1)}" if m else None


def fetch(url: str) -> dict:
    """Raw context.dev response with ``json`` and ``html`` (retries 429s like the batch scrapers)."""
    body = {"url": url, "formats": {"json": True, "html": True},
            "jsonParams": {"schema": BASE_SCHEMA, "instructions": INSTRUCTIONS},
            "sharedParams": {"actions": [{"type": "scroll", "direction": "down", "amount": "viewport",
                                          "maxScrolls": SCROLLS}]}}
    c = client()
    for attempt in range(3):
        LIMITER.wait()
        try:
            return api_scrape(c, body)
        except Exception as e:
            if "RATE_LIMITED" in str(e) and attempt < 2:
                time.sleep(30)
                continue
            raise
    raise RuntimeError("context.dev rate limit")


def to_base(data: dict | None, html: str | None, url: str) -> dict:
    """Normalise the JSON extraction into the base format, repaired with the HTML parse."""
    sub_hint = (re.search(r"/r/([^/]+)", url) or [None, ""])[1]
    hpost = None
    if html:
        try:
            hpost = parse_thread(html, sub_hint)
        except Exception as e:  # page without <shreddit-post>: keep the JSON result
            logger.warning("HTML parse failed: %s", e)
    if not data or not data.get("comments"):
        if hpost is None:
            raise RuntimeError("context.dev returned neither comments nor a parsable page")
        logger.info("JSON extraction empty; using HTML parse")
        return hpost

    H = {n["comment_id"]: n for n in _walk(hpost["comments"])} if hpost else {}
    seen: set[str] = set()

    def fix(nodes):
        out = []
        for n in nodes or []:
            cid = n.get("comment_id") or ""
            if not cid.startswith("t1_") or cid in seen:  # drop invented / duplicated ids
                continue
            seen.add(cid)
            body = (n.get("body") or "").strip()
            body = PLACEHOLDERS.get(body.lower(), body)
            author = (n.get("author") or "[deleted]").removeprefix("u/")
            score, created = n.get("upvote_count"), _epoch(n.get("created_utc"))
            if h := H.get(cid):  # HTML attributes are exact
                author, score = h["author"], h["upvote_count"]
                created = h.get("created_utc", created)
                if h["body"] and h["body"] != "[deleted]":
                    body = h["body"]
            out.append({"comment_id": cid, "author": author, "body": body, "upvote_count": score,
                        "created_utc": created, "replies": fix(n.get("replies"))})
        return out

    hp = hpost or {}
    post_id = data.get("post_id") or hp.get("post_id") or _post_id_from_url(url)
    if not post_id.startswith("t3_"):
        post_id = "t3_" + post_id
    comments = fix(data.get("comments"))
    return {
        "post_id": post_id,
        "title": hp.get("title") or data.get("title") or "",
        "body": hp.get("body") or data.get("body") or "",
        "author": hp.get("author") or (data.get("author") or "[deleted]").removeprefix("u/"),
        "subreddit": hp.get("subreddit") or (data.get("subreddit") or sub_hint).removeprefix("r/"),
        "upvote_count": hp.get("upvote_count", data.get("upvote_count")),
        "created_utc": hp.get("created_utc") or _epoch(data.get("created_utc")),
        "comments": comments,
        "permalink": hp.get("permalink") or url,
        "reported_comment_count": hp.get("reported_comment_count") or data.get("reported_comment_count"),
        "captured_comment_count": sum(1 for _ in _walk(comments)),
        "scores_from_html": bool(H),
    }


def scrape_thread(url: str) -> dict:
    res = fetch(url)
    j, h = res.get("json") or {}, res.get("html") or {}
    if not j.get("success"):
        logger.warning("JSON extraction failed: %s", j.get("error_code") or j.get("message"))
    return to_base(j.get("data") if j.get("success") else None, h.get("data") if h.get("success") else None, url)
