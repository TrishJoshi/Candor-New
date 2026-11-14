"""Data for the Reddit mockup (mockup/template.html, served as web/view.html).

Produces the same ``DATA`` shape mockup/build_mockup.py builds for the demo feed
({meta, threads, profiles}) for one live thread, plus a ``narratives`` block (Stage 2)
and live-only meta (analysis id, warnings, topic keywords).
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone

from candor.schemas import NOISE_ID  # narrative-buckets

LIVE_UNSCORED_NOTE = "Only the thread's most engaged commenters are scored; other badges show '?'."


def profile_view(score: dict, account: dict | None) -> dict:
    """One profile in the mockup's format (shared with mockup/build_mockup.py)."""
    a = account or {}
    user = a.get("user") or {}
    d = a.get("derived") or {}
    return {
        "score": score.get("authenticity_score"),
        "label": score.get("authenticity_label"),
        "red": score.get("red_flag_criteria", []),
        "fetch": score.get("fetch_status"),
        "history": score.get("history_visible"),
        "criteria": {k: {"name": v["criterion"], "weight": v["weight"], "assessed": v["assessed"],
                         "pct": v["score_pct"], "signals": v["signals"], "evidence": v.get("evidence", {})}
                     for k, v in (score.get("criteria") or {}).items()},
        "karma": user.get("total_karma"),
        "age_days": d.get("account_age_days"),
        "avatar": user.get("avatar_url"),
        "bio": user.get("bio"),
    }


def _iso(epoch) -> str | None:
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat() if epoch is not None else None
    except (TypeError, ValueError, OSError):
        return None


def engagement_weight(upvotes, replies: int) -> float:
    return math.log1p(max(upvotes or 0, 0)) + 0.5 * math.log1p(replies)


def narrative_buckets(all_items, result, profiles: dict) -> list[dict]:
    """Narratives with engagement-weighted share and the authenticity of their commenters."""
    comments = [c for c in all_items if getattr(c, "kind", "") == "comment"]
    n_replies = defaultdict(int)
    for c in comments:
        n_replies[getattr(c, "parent_id", None)] += 1
    narr_of = {c.comment_id: c.narrative_id for c in result.comments}
    clustered = [c for c in comments if c.comment_id in narr_of]
    weight = {c.comment_id: engagement_weight(getattr(c, "upvote_count", 0), n_replies[c.comment_id]) for c in clustered}
    total_w = sum(weight.values()) or 1.0
    groups = defaultdict(list)
    for c in clustered:
        groups[narr_of[c.comment_id]].append(c)
    meta = {n.narrative_id: n for n in result.narratives}
    topics = {t.topic_id: t.label for t in result.topics}
    out = []
    for nid, cs in groups.items():
        n = meta.get(nid)
        authors = {c.author for c in cs}
        scored = [profiles[a]["score"] for a in authors if a in profiles and profiles[a]["score"] is not None]
        flagged = [a for a in authors if a in profiles and profiles[a]["label"] in ("uncertain", "suspicious")]
        out.append({
            "id": nid,
            "label": n.label if n else "Other / short replies",
            "topic": topics.get(n.topic_id) if n else None,
            "n_comments": len(cs),
            "n_authors": len(authors),
            "share": round(len(cs) / max(len(clustered), 1), 3),
            "weighted_share": round(sum(weight[c.comment_id] for c in cs) / total_w, 3),
            "keywords": [k.word for k in n.keywords[:8]] if n else [],
            "n_scored": len(scored),
            "mean_score": round(sum(scored) / len(scored), 1) if scored else None,
            "flagged": sorted(flagged),
        })
    out.sort(key=lambda b: (b["id"] == NOISE_ID, -b["weighted_share"]))
    return out


def build_view(analysis_id: str, post: dict, all_items, result, scores: dict, accounts: dict,
               warnings: list[str], map_url: str) -> dict:
    """``all_items``: every flattened item of the thread (incl. bots/deleted, shown as-is)."""
    profiles = {p["username"]: profile_view(p, accounts.get(p["username"])) for p in scores["profiles"]}
    narr_of = {c.comment_id: c.narrative_id for c in result.comments}
    comments = [{
        "id": c.comment_id, "author": c.author, "body": c.text, "score": getattr(c, "upvote_count", None),
        "posted_at": _iso(getattr(c, "created_utc", None)), "parent": getattr(c, "parent_id", None),
        "narrative": narr_of.get(c.comment_id),
    } for c in all_items if getattr(c, "kind", "") == "comment"]
    thread = {
        "id": post["post_id"], "sub": post.get("subreddit"), "title": post.get("title"),
        "url": post.get("permalink"), "author": post.get("author"), "body": post.get("body") or "",
        "link": post.get("link_url"), "score": post.get("upvote_count"),
        "num_comments": post.get("reported_comment_count") or len(comments),
        "posted_at": _iso(post.get("created_utc")), "comments": comments,
        "narrative": narr_of.get(post["post_id"]),
    }
    return {
        "meta": {
            "mode": "live",
            "analysis_id": analysis_id,
            "topic": f"r/{post.get('subreddit')}",
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "weights": scores["weights"],
            "unassessed_pct": scores["unassessed_pct"],
            "summary": scores["summary"],
            "topic_keywords": scores.get("topic_keywords", []),
            "warnings": warnings + [LIVE_UNSCORED_NOTE],
            "map_url": map_url,
            "captured_comments": post.get("captured_comment_count"),
            "reported_comments": post.get("reported_comment_count"),
        },
        "threads": [thread],
        "profiles": profiles,
        "narratives": narrative_buckets(all_items, result, profiles),
    }
