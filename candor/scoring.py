"""Stage 3 (live): score the commenters of one analysed thread.

Bridges the live pipeline and profile_authenticity_scorer.score_live():
  * derives the thread's topic keywords (C5 single-issue check) with class-based TF-IDF:
    the thread's text against the commenters' wider histories as background, so the
    keywords are what's distinctive about *this* thread, not generic Reddit words;
  * passes the thread's comments as seed text (copy-paste between commenters, C2/C3).
"""
from __future__ import annotations

import re
from typing import Sequence

from candor.representation import class_tfidf  # narrative-buckets, via candor.__path__

import profile_authenticity_scorer as scorer

N_TOPIC_KEYWORDS = 15
_MIN_KEYWORD_LEN = 4  # short tokens ("gop", "ai") match too much by accident in whole-word search
# A topic keyword must be rare in the commenters' *other* activity. Without this, a broad word
# like "trump" (8.6% of r/politics regulars' history in a test thread) plus any second keyword
# made routine politics comments count as on-topic, and 7/20 ordinary regulars got
# repeat_topic_threads with only 3-6% of their activity on the topic.
MAX_BACKGROUND_DF = 0.02


def topic_keywords(thread_texts: Sequence[str], background_texts: Sequence[str],
                   n: int = N_TOPIC_KEYWORDS) -> list[str]:
    """Terms most distinctive of the thread compared with the commenters' other activity."""
    classes = {0: [t for t in thread_texts if t.strip()]}
    if background_texts:
        classes[1] = [t for t in background_texts if t.strip()]
    kw = class_tfidf(classes, top_n=n * 4).get(0, [])
    bg = [t.lower() for t in classes.get(1, [])]
    out: list[str] = []
    for word, _ in kw:
        if len(word) < _MIN_KEYWORD_LEN or not re.search(r"[a-z]", word):
            continue
        if bg:
            pat = re.compile(r"\b" + re.escape(word) + r"\b")
            if sum(bool(pat.search(t)) for t in bg) / len(bg) > MAX_BACKGROUND_DF:
                continue
        if any(word in o or o in word for o in out):  # "majority" vs "majority leader"
            continue
        out.append(word)
        if len(out) == n:
            break
    return out


def score_thread(post: dict, items: Sequence, accounts: dict[str, dict]) -> dict:
    """Score ``accounts`` (from candor.profiles) in the context of one thread.

    post:  base-format thread from candor.scrape (post_id, title, body, ...)
    items: the thread's flattened Comment objects (bots/deleted already removed)
    """
    thread_id = post["post_id"].removeprefix("t3_")
    thread_texts = [f"{post.get('title') or ''}\n{post.get('body') or ''}"] + [c.text for c in items]
    background = [
        (x.get("body") or "") + " " + (x.get("title") or "")
        for a in accounts.values() if a.get("fetch_status") == "ok"
        for x in (a.get("comments") or []) + (a.get("posts") or [])
        # the thread itself appears in commenters' histories; keep it out of the background
        if ((x.get("post") or {}).get("id") or x.get("id") or "").removeprefix("t3_") != thread_id
    ]
    keywords = topic_keywords(thread_texts, background)
    seeds = [{"author": c.author, "thread": thread_id, "text": c.text,
              "posted_at": _iso(getattr(c, "created_utc", None))} for c in items]
    result = scorer.score_live(accounts, seeds, thread_id, keywords)
    result["topic_keywords"] = keywords
    return result


def _iso(epoch) -> str | None:
    if epoch is None:
        return None
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None
