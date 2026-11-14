"""
Track 3 MVP — Comment-level authenticity scoring (Junction 2026)

Phase 1 (this file, runs now):  score every comment 0-100 from the Facebook JSON output.
Phase 2 (when Track 2 lands):   aggregate_clusters() rolls comment scores up per cluster_id
                                (median), so nothing here has to change.

Usage:
    python scorer.py [Facebook_JSON_Output.json] [scored_output.json]

Score convention: 100 = looks authentic, 0 = looks inauthentic.
Every penalty lives in SCORING_TABLE below — swap in the team's scoring table there.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from statistics import median, pstdev
from urllib.parse import urlparse

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# --------------------------------------------------------------------------------------
# SCORING TABLE — one row per signal. Points are deducted from a base of 100.
# --------------------------------------------------------------------------------------
SCORING_TABLE = {
    # --- C1: account signals (proxy for account_age_days, which the FB scrape doesn't give) ---
    "new_numeric_profile_id":   {"points": 25, "why": "Profile uses a 6155…/6156…/6157… numeric ID (newer-generation account)"},
    "vanity_has_recent_year":   {"points": 10, "why": "Vanity username ends in a recent year (e.g. .2024), often a fresh account"},
    # C1 replacement for private individuals: their account can't be inspected, so instead of
    # the full C1 checks they get this single, smaller flat deduction for being unverifiable.
    "c1_unverifiable_private":  {"points": 8,  "why": "Private individual — account (C1) can't be assessed, small flat deduction instead"},
    # --- content signals ---
    "near_duplicate_text":      {"points": 35, "why": "Text is a near copy of another author's comment (char n-gram cosine >= 0.80)"},
    "similar_text":             {"points": 15, "why": "Text closely resembles another author's comment (cosine 0.55-0.80)"},
    "hashtag_stack":            {"points": 15, "why": "3+ hashtags — campaign-style copy rather than conversation"},
    "slogan_only":              {"points": 10, "why": "Slogan / hashtag-only reply with no personal content (<= 4 words)"},
    "media_only":               {"points": 10, "why": "No text, image only — can't be read, mildly suspicious in swarms"},
    # --- behaviour signals (fire once we have full comment sets / repeat authors) ---
    "burst_posting":            {"points": 25, "why": "Same author posted repeatedly with < 2 min average gap"},
    "repeat_commenter":         {"points": 5,  "why": "Same author commented 3+ times on one post"},
}

THRESHOLDS = {
    "near_dup": 0.80,
    "similar": 0.55,
    "hashtag_stack": 3,
    "slogan_max_words": 4,
    "burst_mean_gap_s": 120,
    "repeat_min": 3,
}

LABELS = [(70, "likely_authentic"), (40, "uncertain"), (0, "suspicious")]

NEW_ID_PREFIXES = ("6155", "6156", "6157", "6158")
HASHTAG_RE = re.compile(r"#\w+")
RECENT_YEAR_RE = re.compile(r"[._-]?(202[3-9])$")


# --------------------------------------------------------------------------------------
# Input handling
# --------------------------------------------------------------------------------------
def flatten_comments(comments: list[dict], parent_id: str | None = None) -> list[dict]:
    """Flatten nested replies so replies are scored like top-level comments."""
    out = []
    for c in comments:
        c = dict(c)
        c["parent_comment_id"] = parent_id
        replies = c.pop("replies", []) or []
        out.append(c)
        out.extend(flatten_comments(replies, c["comment_id"]))
    return out


def parse_ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def normalise(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w#\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------------------
# Signal extractors — each returns a list of signal keys that fired
# --------------------------------------------------------------------------------------
PRIVATE_PROFILE_TYPES = {"pfbid", "unresolved"}  # account details hidden from the scrape


def classify_profile(profile_url: str) -> str:
    """Profile URL shape: vanity | numeric_id | pfbid | unresolved."""
    parts = urlparse(profile_url).path.strip("/").split("/")
    if "posts" in parts:  # scraper returned the post URL instead of the author's profile
        return "unresolved"
    if parts and parts[0] == "people":
        last = parts[-1] if len(parts) >= 3 else ""
        return "numeric_id" if last.isdigit() else "pfbid"
    return "vanity" if parts and parts[0] else "unresolved"


def is_private_individual(author: dict, profile_type: str) -> bool:
    """Use the scraper's explicit flag when present; otherwise infer from the profile URL.
    pfbid = Facebook hides the real ID; unresolved = no profile link at all. Either way
    we cannot inspect the account, so C1 can't be applied."""
    if "is_private" in author:
        return bool(author["is_private"])
    return profile_type in PRIVATE_PROFILE_TYPES


def account_signals(profile_url: str, private: bool) -> list[str]:
    """C1. Private individuals get one small flat deduction instead of the full checks."""
    if private:
        return ["c1_unverifiable_private"]
    parts = urlparse(profile_url).path.strip("/").split("/")
    if parts[0] == "people":
        return ["new_numeric_profile_id"] if parts[-1].startswith(NEW_ID_PREFIXES) else []
    return ["vanity_has_recent_year"] if RECENT_YEAR_RE.search(parts[0]) else []


def content_signals(text: str, has_media: bool) -> list[str]:
    fired = []
    if not text.strip():
        return ["media_only"] if has_media else []
    if len(HASHTAG_RE.findall(text)) >= THRESHOLDS["hashtag_stack"]:
        fired.append("hashtag_stack")
    words = HASHTAG_RE.sub(" #tag ", text).split()
    if len(words) <= THRESHOLDS["slogan_max_words"]:
        fired.append("slogan_only")
    return fired


def similarity_signals(comments: list[dict]) -> dict[str, tuple[list[str], float, str | None]]:
    """Char n-gram TF-IDF cosine between comments of *different* authors."""
    idx = [i for i, c in enumerate(comments) if normalise(c.get("message", ""))]
    result = {c["comment_id"]: ([], 0.0, None) for c in comments}
    if len(idx) < 2:
        return result
    texts = [normalise(comments[i]["message"]) for i in idx]
    tfidf = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5)).fit_transform(texts)
    sims = cosine_similarity(tfidf)
    for a, i in enumerate(idx):
        best, best_id = 0.0, None
        for b, j in enumerate(idx):
            if a == b or comments[i]["author"]["id"] == comments[j]["author"]["id"]:
                continue
            if sims[a, b] > best:
                best, best_id = float(sims[a, b]), comments[j]["comment_id"]
        fired = []
        if best >= THRESHOLDS["near_dup"]:
            fired.append("near_duplicate_text")
        elif best >= THRESHOLDS["similar"]:
            fired.append("similar_text")
        result[comments[i]["comment_id"]] = (fired, round(best, 3), best_id)
    return result


def behaviour_signals(comments: list[dict]) -> dict[str, list[str]]:
    """Velocity math from the tech doc: timing variance for authors with multiple comments."""
    by_author = defaultdict(list)
    for c in comments:
        by_author[c["author"]["id"]].append(parse_ts(c["created_time"]))
    fired = {}
    for author, times in by_author.items():
        sig = []
        if len(times) >= 2:
            times.sort()
            gaps = [b - a for a, b in zip(times, times[1:])]
            if sum(gaps) / len(gaps) < THRESHOLDS["burst_mean_gap_s"]:
                sig.append("burst_posting")
        if len(times) >= THRESHOLDS["repeat_min"]:
            sig.append("repeat_commenter")
        fired[author] = sig
    return fired


# --------------------------------------------------------------------------------------
# Phase 1: comment-level scoring
# --------------------------------------------------------------------------------------
def label_for(score: float) -> str:
    return next(name for floor, name in LABELS if score >= floor)


def score_post(post: dict) -> dict:
    comments = flatten_comments(post.get("comments", []))
    sim = similarity_signals(comments)
    beh = behaviour_signals(comments)
    post_ts = parse_ts(post["created_time"]) if post.get("created_time") else None

    scored = []
    for c in comments:
        profile_url = c["author"].get("profile_url", "")
        profile_type = classify_profile(profile_url)
        private = is_private_individual(c["author"], profile_type)
        acc_sig = account_signals(profile_url, private)
        con_sig = content_signals(c.get("message", ""), bool(c.get("attachments")))
        sim_sig, sim_score, sim_match = sim[c["comment_id"]]
        beh_sig = beh.get(c["author"]["id"], [])
        fired = acc_sig + con_sig + sim_sig + beh_sig

        score = max(0, 100 - sum(SCORING_TABLE[s]["points"] for s in fired))

        data_flags = []
        if profile_type == "unresolved":
            data_flags.append("profile_url_unresolved")
        if post_ts and parse_ts(c["created_time"]) < post_ts:
            data_flags.append("comment_before_post")

        scored.append({
            **c,
            "cluster_id": c.get("cluster_id"),  # filled by Track 2 later
            "authenticity_score": score,
            "authenticity_label": label_for(score),
            "authenticity_signals": [
                {"signal": s, "points": -SCORING_TABLE[s]["points"], "why": SCORING_TABLE[s]["why"]}
                for s in fired
            ],
            "max_similarity": {"score": sim_score, "matched_comment_id": sim_match},
            "profile_type": profile_type,
            "is_private_individual": private,
            "c1_assessed": not private,
            "data_quality_flags": data_flags,
        })

    minutes = {c["created_time"][14:16] for c in comments}
    post_flags = []
    if len(comments) > 3 and len(minutes) <= 2:
        post_flags.append("coarse_timestamps: comment times look derived from relative 'Xh ago' labels; timing signals unreliable")
    if post.get("comment_count") and post["comment_count"] > len(comments):
        post_flags.append(f"partial_scrape: {len(comments)} of {post['comment_count']} comments present")

    scores = [c["authenticity_score"] for c in scored]
    return {
        **{k: v for k, v in post.items() if k != "comments"},
        "comments": scored,
        "track3_summary": {
            "comments_scored": len(scored),
            "median_score": median(scores) if scores else None,
            "label_counts": {lbl: sum(c["authenticity_label"] == lbl for c in scored) for _, lbl in LABELS},
            "data_quality_flags": post_flags,
        },
    }


# --------------------------------------------------------------------------------------
# Phase 2: cluster roll-up (call after Track 2 has filled cluster_id)
# --------------------------------------------------------------------------------------
def aggregate_clusters(scored_comments: list[dict]) -> list[dict]:
    """NarrativeCluster-shaped output. cluster_score = median comment score (per tech doc)."""
    groups = defaultdict(list)
    for c in scored_comments:
        groups[c.get("cluster_id")].append(c)
    clusters = []
    for cid, members in groups.items():
        s = [m["authenticity_score"] for m in members]
        clusters.append({
            "cluster_id": cid,  # None = not yet clustered; -1 = HDBSCAN noise
            "label": None,      # filled by Track 4
            "cluster_score": median(s),
            "score_spread": round(pstdev(s), 1) if len(s) > 1 else 0.0,
            "n_comments": len(members),
            "n_authors": len({m["author"]["id"] for m in members}),
            "share_suspicious": round(sum(m["authenticity_label"] == "suspicious" for m in members) / len(members), 2),
            "comments": [m["comment_id"] for m in members],
        })
    return sorted(clusters, key=lambda c: c["cluster_score"])


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "Facebook_JSON_Output.json"
    dst = sys.argv[2] if len(sys.argv) > 2 else "scored_output.json"
    with open(src, encoding="utf-8") as f:
        post = json.load(f)
    result = score_post(post)
    result["clusters"] = aggregate_clusters(result["comments"])
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"{'score':>5}  {'label':<17} {'author':<18} signals")
    for c in sorted(result["comments"], key=lambda c: c["authenticity_score"]):
        sigs = ", ".join(s["signal"] for s in c["authenticity_signals"]) or "-"
        print(f"{c['authenticity_score']:>5}  {c['authenticity_label']:<17} {c['author']['name'][:18]:<18} {sigs}")
    print("\nsummary:", json.dumps(result["track3_summary"], indent=2))
