"""
Profile authenticity scorer (Reddit) — Junction 2026

Scores every account in data/dataset2/reddit_accounts.json (schemas/reddit_dataset2.schema.json)
on five criteria, each as a percentage, then combines them with the criterion weights:

    C1 Account profile        20   Is this an established, real person?
    C2 Content originality    20   Did this person write it?
    C3 Coordination           15   Is it acting in concert with others?
    C4 Engagement legitimacy  20   Did it earn its karma / scores?
    C5 Behavioural history    25   Does the account do this elsewhere?

C4 and C5 carry more weight than the original 25/25/25/15/10 table: abnormal engagement and
behaviour patterns are harder for a regular user to produce by accident than a default avatar,
a new account or a reused phrase (C1-C3).

Same convention as scorer.py: 100 = looks authentic, 0 = looks inauthentic. Each criterion starts
at 100% and loses the points of every signal that fires (floored at 0). Overall score =
sum(weight x criterion%) / 100. inauthenticity = 100 - score.

Missing data: if a criterion can't be assessed (history hidden, too few comments), it is scored
UNASSESSED_PCT and marked assessed=false instead of being treated as suspicious — hiding history
is common on Reddit (~60% of accounts). Same idea as scorer.py's c1_unverifiable_private.

Cohort-relative checks (C1 low karma, C4 karma velocity / low-effort payoff) compare each account
with the distribution across all scored accounts, as the criteria table asks.

Every penalty lives in SCORING_TABLE / THRESHOLDS — tune there.

Two modes:
    Offline (dataset 2): cohort percentiles come from the scored accounts themselves and the
        "single issue" topic is NARRATIVE_KEYWORDS (Finnish data-centre narrative).
            python profile_authenticity_scorer.py [reddit_accounts.json] [profile_scores.json]
    Live (one analysed thread, candor/scoring.py): score_live() takes the thread's own topic
        keywords and a frozen reference cohort (reference/cohort_dataset2.json), because ~20
        commenters are too few for percentiles and the data-centre keywords don't apply.
        Rebuild the reference with:  python profile_authenticity_scorer.py --export-cohort
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

# Narrative keywords for C5 "single issue" in OFFLINE mode (dataset 2's data-centre narrative).
# Deliberately narrower than dataset 1's search keywords: "google" alone matched any mention of
# Google search, Android, etc. Live mode passes the analysed thread's own keywords instead.
NARRATIVE_KEYWORDS = ["hamina", "muhos", "kajaani", "datakeskus", "data center", "data centre",
                      "datacenter", "palvelinkeskus", "konesali"]

WEIGHTS = {"C1": 20, "C2": 20, "C3": 15, "C4": 20, "C5": 25}  # was 25/25/25/15/10
CRITERIA = {
    "C1": "Account profile — is this an established, real person?",
    "C2": "Content originality — did this person write it?",
    "C3": "Coordination — is it acting in concert with others?",
    "C4": "Engagement legitimacy — did it earn its karma?",
    "C5": "Behavioural history — does the account do this elsewhere?",
}

# --------------------------------------------------------------------------------------
# SCORING TABLE — points deducted from the criterion's 100%. Tiered signals: only the
# highest tier that matches fires (e.g. age_lt_30d OR age_lt_180d, not both).
# --------------------------------------------------------------------------------------
SCORING_TABLE = {
    # --- C1 account profile ---
    "suspended":               {"c": "C1", "points": 100, "why": "Account suspended by Reddit"},
    "age_lt_30d":              {"c": "C1", "points": 45, "why": "Account is less than 30 days old"},
    "age_lt_180d":             {"c": "C1", "points": 25, "why": "Account is less than 6 months old"},
    "karma_very_low":          {"c": "C1", "points": 20, "why": "Total karma under 100 — little track record"},
    "karma_below_cohort":      {"c": "C1", "points": 10, "why": "Karma in the bottom 10% of the comparison cohort"},
    "no_verified_email":       {"c": "C1", "points": 15, "why": "Email not verified"},
    "default_avatar":          {"c": "C1", "points": 10, "why": "Stock default avatar (Reddit's 'no photo')"},
    "default_username":        {"c": "C1", "points": 10, "why": "Auto-suggested Word_Word_1234 username, typical of throwaway / bulk-created accounts"},
    # --- C2 content originality ---
    "copies_others_many":      {"c": "C2", "points": 70, "why": "3+ comments are near copies (cosine >= 0.85) of other accounts' comments"},
    "copies_others":           {"c": "C2", "points": 40, "why": "A comment is a near copy (cosine >= 0.85) of another account's comment"},
    "self_copy_many":          {"c": "C2", "points": 45, "why": "Posted near-identical text in 3+ different threads"},
    "self_copy":               {"c": "C2", "points": 25, "why": "Posted near-identical text in different threads"},
    "slogan_heavy":            {"c": "C2", "points": 15, "why": "Over half of comments are 4 words or fewer"},
    # --- C3 coordination ---
    "co_timed_partners_many":  {"c": "C3", "points": 70, "why": "Posts within minutes of the same 3+ other narrative accounts in several other threads"},
    "co_timed_partners":       {"c": "C3", "points": 40, "why": "Posts within minutes of another narrative account in several other threads"},
    "shared_campaign_link":    {"c": "C3", "points": 25, "why": "Posts the exact same link as 3+ other narrative accounts"},
    "shared_hashtag":          {"c": "C3", "points": 20, "why": "Uses a hashtag that 3+ other narrative accounts also use"},
    "copy_ring":               {"c": "C3", "points": 30, "why": "Its copied text is shared with 2+ other accounts (copy-paste ring)"},
    # --- C4 engagement legitimacy ---
    "karma_velocity_extreme":  {"c": "C4", "points": 45, "why": "Karma per day in the top 5% of the cohort on an account under a year old (farmed/bought karma)"},
    "low_effort_high_reward":  {"c": "C4", "points": 30, "why": "30%+ of comments are short slogans scoring in the cohort's top 10%"},
    "post_karma_farming":      {"c": "C4", "points": 20, "why": "Post karma over 20x comment karma (repost-farming pattern)"},
    # --- C5 behavioural history ---
    "history_hidden":          {"c": "C5", "points": 30, "why": "Post/comment history is hidden, so its behaviour can't be checked"},
    "single_issue_strong":     {"c": "C5", "points": 45, "why": "30%+ of recent activity is about the narrative topic"},
    "single_issue":            {"c": "C5", "points": 20, "why": "10-30% of recent activity is about the narrative topic"},
    "repeat_topic_threads":    {"c": "C5", "points": 20, "why": "Commented on the topic in 5+ different threads"},
    "narrow_footprint":        {"c": "C5", "points": 20, "why": "20+ recent items in 3 or fewer subreddits"},
    "burst_posting":           {"c": "C5", "points": 25, "why": "Median gap between posts under 2 minutes"},
    "no_sleep_pattern":        {"c": "C5", "points": 15, "why": "No daily rest gap: even its quietest 6-hour window holds 12%+ of activity"},
}

THRESHOLDS = {
    "near_dup": 0.85,
    "min_chars_for_similarity": 40,   # shorter texts ("thanks!") match by accident
    "co_time_window_s": 180,          # "within minutes"
    "co_time_min_threads": 3,         # shared co-timed threads (outside the seed threads) to count a partner
    "co_time_max_thread_accounts": 3, # ignore threads where 4+ cohort accounts posted (busy community threads)
    "share_min_accounts": 3,          # link / hashtag shared by this many accounts incl. this one
    "slogan_max_words": 4,
    "low_effort_max_tokens": 8,
    "min_comments_for_history": 10,   # fewer than this -> C5 unassessed
    "no_sleep_min_share": 0.12,       # quietest 6h window still holds >= 12% of activity (uniform = 25%)
}
REFERENCE_COHORT_PATH = Path(__file__).resolve().parent / "reference" / "cohort_dataset2.json"
UNASSESSED_PCT = 90                   # neutral-ish score for criteria we can't measure
# A weighted average hides one collapsed criterion (C5 at 0% only costs 10 points), so any
# assessed criterion at or below this caps the label at "uncertain". The score is unchanged.
RED_FLAG_PCT = 20
LABELS = [(70, "likely_authentic"), (40, "uncertain"), (0, "suspicious")]
DEFAULT_NAME_RE = re.compile(r"^[A-Z][a-z]+[-_][A-Z][a-z]+[-_]?\d{1,4}$")
URL_RE = re.compile(r"https?://[^\s)\]>]+")
HASHTAG_RE = re.compile(r"(?<![\w/])#[A-Za-z]\w{2,}")
COMMON_LINK_DOMAINS = ("reddit.com", "redd.it", "imgur.com", "youtube.com", "youtu.be", "wikipedia.org", "giphy.com")


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
def ts(s: str | None) -> float | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None
    except ValueError:  # extracted pages sometimes give relative times ("3 min. ago")
        return None


def strip_quotes(text: str) -> str:
    """Drop Markdown '> quoted' lines so quoting someone isn't counted as copying them."""
    return "\n".join(l for l in (text or "").splitlines() if not l.lstrip().startswith(">"))


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w#\s]", " ", text.lower())).strip()


def pct_rank(values: list[float], v: float) -> float:
    return sum(x <= v for x in values) / len(values) if values else 0.5


def thread_id_from_url(url: str) -> str | None:
    m = re.search(r"/comments/([a-z0-9]+)", url or "")
    return m.group(1) if m else None


def label_for(score: float) -> str:
    return next(name for floor, name in LABELS if score >= floor)


# --------------------------------------------------------------------------------------
# corpus: every text we have per author (history + their dataset-1 narrative comments)
# --------------------------------------------------------------------------------------
def build_corpus(accounts: dict, ds1_path: Path | None, extra_seeds: list | None = None,
                 extra_seed_threads: list | None = None) -> tuple[list[dict], set[str]]:
    docs, seen = [], set()

    def add(author, thread, text, posted, score=None, source="history"):
        text = strip_quotes(text)
        key = (author, norm(text))
        if not text.strip() or text.strip() in ("[deleted]", "[removed]") or key in seen:
            return
        seen.add(key)
        docs.append({"author": author, "thread": thread, "text": text, "ts": ts(posted),
                     "score": score, "source": source})

    for name, a in accounts.items():
        for c in a.get("comments") or []:
            add(name, (c.get("post") or {}).get("id") or thread_id_from_url(c.get("url")), c.get("body"),
                c.get("posted_at"), c.get("score"))
        for p in a.get("posts") or []:
            add(name, p.get("id"), f"{p.get('title') or ''}\n{p.get('body') or ''}", p.get("posted_at"), p.get("score"))

    seed_threads = set()
    if ds1_path is not None and ds1_path.exists():
        for line in ds1_path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("source_domain") != "reddit.com" or not r.get("author"):
                continue
            tid = thread_id_from_url(r["source_url"])
            if r["topic_label"] == "related" and tid:
                seed_threads.add(tid)
            author = r["author"].removeprefix("u/")
            if author in accounts:
                add(author, tid, r["text"], r.get("created_time"), source="dataset1")
    # cohorts collected from their own threads (scrape_reddit_subreddits.py) carry their seeds inline
    for s in extra_seeds or []:
        if s.get("author") in accounts:
            add(s["author"], s.get("thread"), s.get("text") or "", s.get("posted_at"), source="seed")
    seed_threads |= set(extra_seed_threads or [])
    # normalise thread ids (parser ids may carry a t3_ prefix)
    for d in docs:
        if d["thread"]:
            d["thread"] = d["thread"].removeprefix("t3_")
    return docs, seed_threads


def near_duplicates(docs: list[dict]) -> list[tuple[int, int, float]]:
    """All pairs of docs with char 3-5-gram cosine >= near_dup, computed in chunks."""
    idx = [i for i, d in enumerate(docs) if len(norm(d["text"])) >= THRESHOLDS["min_chars_for_similarity"]]
    if len(idx) < 2:
        return []
    X = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), dtype=np.float32).fit_transform(
        [norm(docs[i]["text"]) for i in idx])
    pairs = []
    for start in range(0, len(idx), 1000):
        block = (X[start:start + 1000] @ X.T).tocoo()
        for r, c, v in zip(block.row, block.col, block.data):
            a, b = idx[start + r], idx[c]
            if a < b and v >= THRESHOLDS["near_dup"]:
                pairs.append((a, b, float(v)))
    return pairs


# --------------------------------------------------------------------------------------
# per-criterion signal extractors: each returns (fired_signals, assessed, evidence)
# --------------------------------------------------------------------------------------
def c1_signals(a: dict, cohort: dict, now: float) -> tuple[list[str], bool, dict]:
    if a["fetch_status"] == "suspended":
        return ["suspended"], True, {}
    u, fired = a["user"], []
    age = (now - ts(u["created_at"])) / 86400 if u.get("created_at") else None
    if age is not None:
        fired += ["age_lt_30d"] if age < 30 else ["age_lt_180d"] if age < 180 else []
    karma = u.get("total_karma") or 0
    if karma < 100:
        fired.append("karma_very_low")
    elif pct_rank(cohort["karma"], karma) <= 0.10:
        fired.append("karma_below_cohort")
    if u.get("has_verified_email") is False:
        fired.append("no_verified_email")
    avatar = u.get("avatar_url") or ""
    if not avatar or "avatar_default" in avatar:
        fired.append("default_avatar")
    if DEFAULT_NAME_RE.match(u.get("username") or ""):
        fired.append("default_username")
    return fired, True, {"account_age_days": round(age, 1) if age is not None else None, "total_karma": karma}


def c2_signals(name: str, ctx: dict) -> tuple[list[str], bool, dict]:
    mine = ctx["docs_by_author"].get(name, [])
    if not mine:
        return [], False, {"reason": "no text available"}
    fired = []
    copied = ctx["copied_from_others"].get(name, set())
    if len(copied) >= 3:
        fired.append("copies_others_many")
    elif copied:
        fired.append("copies_others")
    self_threads = ctx["self_copy_threads"].get(name, 0)
    if self_threads >= 3:
        fired.append("self_copy_many")
    elif self_threads >= 2:
        fired.append("self_copy")
    short = sum(len(norm(ctx["docs"][i]["text"]).split()) <= THRESHOLDS["slogan_max_words"] for i in mine)
    if len(mine) >= 4 and short / len(mine) > 0.5:
        fired.append("slogan_heavy")
    return fired, True, {"texts_compared": len(mine), "comments_copied_from_others": len(copied),
                         "copy_partners": sorted(ctx["copy_partners"].get(name, set()))[:10],
                         "threads_with_self_copies": self_threads}


def c3_signals(name: str, a: dict, ctx: dict) -> tuple[list[str], bool, dict]:
    fired = []
    partners = {p for p, n in ctx["co_timed"][name].items() if n >= THRESHOLDS["co_time_min_threads"]}
    if len(partners) >= 3:
        fired.append("co_timed_partners_many")
    elif partners:
        fired.append("co_timed_partners")
    if any(len(ctx["url_accounts"][u]) >= THRESHOLDS["share_min_accounts"] for u in ctx["urls_by_author"][name]):
        fired.append("shared_campaign_link")
    if any(len(ctx["tag_accounts"][t]) >= THRESHOLDS["share_min_accounts"] for t in ctx["tags_by_author"][name]):
        fired.append("shared_hashtag")
    if len(ctx["copy_partners"].get(name, set())) >= 2:
        fired.append("copy_ring")
    # co-timing and link sharing need the account's wider history
    assessed = a.get("history_visible", False) or bool(fired)
    return fired, assessed, {"co_timed_partners": sorted(partners)[:10]}


def c4_signals(a: dict, ctx: dict, now: float) -> tuple[list[str], bool, dict]:
    if a["fetch_status"] != "ok":
        return [], False, {}
    u, fired = a["user"], []
    age = max(1.0, (now - ts(u["created_at"])) / 86400) if u.get("created_at") else None
    kpd = (u.get("total_karma") or 0) / age if age else None
    if kpd is not None and age < 365 and pct_rank(ctx["cohort"]["karma_per_day"], kpd) >= 0.95:
        fired.append("karma_velocity_extreme")
    pk, ck = u.get("post_karma") or 0, u.get("comment_karma") or 0
    if pk > 1000 and pk > 20 * max(ck, 1):
        fired.append("post_karma_farming")
    comments = a.get("comments") or []
    if len(comments) >= 5:
        p90 = ctx["cohort"]["comment_score_p90"]
        cheap = [c for c in comments if len(norm(c.get("body") or "").split()) <= THRESHOLDS["low_effort_max_tokens"]
                 and (c.get("score") or 0) >= p90]
        if len(cheap) >= 3 and len(cheap) / len(comments) >= 0.3:
            fired.append("low_effort_high_reward")
    return fired, True, {"karma_per_day": round(kpd, 1) if kpd is not None else None}


def c5_signals(name: str, a: dict, ctx: dict) -> tuple[list[str], bool, dict]:
    items = (a.get("comments") or []) + (a.get("posts") or [])
    if a["fetch_status"] == "ok" and not a.get("history_visible"):
        # hiding activity blocks behavioural review, so it costs C5 points instead of a neutral score
        return ["history_hidden"], True, {"reason": "history hidden (or no public activity)"}
    if len(items) < THRESHOLDS["min_comments_for_history"]:
        return [], False, {"reason": "history too short to judge"}
    fired = []
    texts = [(i.get("body") or "") + " " + (i.get("title") or "") + " " + ((i.get("post") or {}).get("title") or "")
             for i in items]
    share, topic_threads = None, set()
    if ctx["topic"] is not None:  # single-issue checks only make sense when the cohort shares a topic
        on_topic = [ctx["topic"].matches(t) for t in texts]
        share = sum(on_topic) / len(items)
        fired += ["single_issue_strong"] if share >= 0.3 else ["single_issue"] if share >= 0.1 else []
        topic_threads = {((i.get("post") or {}).get("id") or i.get("id")) for i, hit in zip(items, on_topic) if hit}
        if len(topic_threads) >= 5:
            fired.append("repeat_topic_threads")
    subs = {i.get("subreddit") for i in items if i.get("subreddit")}
    if len(items) >= 20 and len(subs) <= 3:
        fired.append("narrow_footprint")
    times = sorted(t for t in (ts(i.get("posted_at")) for i in items) if t)
    gaps = [b - x for x, b in zip(times, times[1:])]
    if gaps and float(np.median(gaps)) < 120:
        fired.append("burst_posting")
    hours = Counter(datetime.fromtimestamp(t, timezone.utc).hour for t in times)
    if len(times) >= 50:  # quietest 6-hour window; a person's sleep window holds ~0-5% of activity
        quietest = min(sum(hours[(h + k) % 24] for k in range(6)) for h in range(24)) / len(times)
        if quietest >= THRESHOLDS["no_sleep_min_share"]:
            fired.append("no_sleep_pattern")
    return fired, True, {"share_on_topic": round(share, 3) if share is not None else None, "topic_threads": len(topic_threads),
                         "distinct_subreddits": len(subs),
                         "topic_keywords": ctx["topic"].keywords[:10] if ctx["topic"] is not None else []}


# --------------------------------------------------------------------------------------
# cross-account context (computed once for the whole cohort)
# --------------------------------------------------------------------------------------
class TopicMatcher:
    """Is a text about the narrative topic? A text matches when it contains at least
    ``min_hits`` distinct keywords (whole words / phrases, case-insensitive).

    Offline mode uses the hand-picked, very specific NARRATIVE_KEYWORDS with min_hits=1.
    Live mode uses keywords derived from the thread (broader terms), so it requires 2 hits:
    one generic word like "senate" shouldn't make an unrelated comment count as on-topic.
    """

    def __init__(self, keywords: list[str], min_hits: int = 1, substring: bool = False) -> None:
        self.keywords = [k.lower() for k in keywords if k.strip()]
        self.min_hits = min_hits
        self.substring = substring  # offline: legacy substring match ("datakeskus" in "datakeskuksen")
        self._patterns = [re.compile(r"\b" + re.escape(k) + r"\b") for k in self.keywords]

    def matches(self, text: str) -> bool:
        t = (text or "").lower()
        if self.substring:
            hits = sum(k in t for k in self.keywords)
        else:
            hits = sum(bool(p.search(t)) for p in self._patterns)
        return hits >= self.min_hits


def cohort_stats(accounts: dict, now: float) -> dict:
    """Percentile reference for C1 (karma) and C4 (karma velocity, comment score p90)."""
    ok = [a for a in accounts.values() if a["fetch_status"] == "ok"]
    karma = [a["user"].get("total_karma") or 0 for a in ok]
    kpd = [(a["user"].get("total_karma") or 0) / max(1.0, (now - ts(a["user"]["created_at"])) / 86400)
           for a in ok if a["user"].get("created_at")]
    scores = [c.get("score") or 0 for a in ok for c in a.get("comments") or []]
    return {"karma": karma, "karma_per_day": kpd,
            "comment_score_p90": float(np.percentile(scores, 90)) if scores else 0.0}


def load_reference_cohort(path: Path = REFERENCE_COHORT_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["cohort"]


def build_context(accounts: dict, ds1_path: Path | None, data: dict | None = None, *,
                  topic: TopicMatcher | None = None, cohort: dict | None = None) -> dict:
    """Cross-account context. ``topic``/``cohort`` override the offline defaults (live mode)."""
    data = data or {}
    meta = data.get("scrape_meta") or {}
    docs, seed_threads = build_corpus(accounts, ds1_path, data.get("seed_comments"), meta.get("seed_threads"))
    by_author = defaultdict(list)
    for i, d in enumerate(docs):
        by_author[d["author"]].append(i)

    copied_from_others, copy_partners = defaultdict(set), defaultdict(set)
    self_pairs_threads = defaultdict(set)
    for a, b, _ in near_duplicates(docs):
        da, db = docs[a], docs[b]
        if da["author"] != db["author"]:
            copied_from_others[da["author"]].add(a)
            copied_from_others[db["author"]].add(b)
            copy_partners[da["author"]].add(db["author"])
            copy_partners[db["author"]].add(da["author"])
        elif da["thread"] != db["thread"]:
            self_pairs_threads[da["author"]].update({da["thread"], db["thread"]})

    # co-timing in shared threads, excluding the seed narrative threads (shared by construction)
    co_timed = defaultdict(Counter)
    by_thread = defaultdict(list)
    for d in docs:
        if d["thread"] and d["ts"] and d["thread"] not in seed_threads:
            by_thread[d["thread"]].append((d["ts"], d["author"]))
    for posts in by_thread.values():
        # busy community threads: regulars overlap there naturally (seen in r/Suomi news threads)
        if len({a for _, a in posts}) > THRESHOLDS["co_time_max_thread_accounts"]:
            continue
        posts.sort()
        pairs = set()
        for i, (t1, a1) in enumerate(posts):
            for t2, a2 in posts[i + 1:]:
                if t2 - t1 > THRESHOLDS["co_time_window_s"]:
                    break
                if a1 != a2:
                    pairs.add(tuple(sorted((a1, a2))))
        for a1, a2 in pairs:
            co_timed[a1][a2] += 1
            co_timed[a2][a1] += 1

    urls_by_author, url_accounts = defaultdict(set), defaultdict(set)
    tags_by_author, tag_accounts = defaultdict(set), defaultdict(set)
    for d in docs:
        for u in URL_RE.findall(d["text"]):
            u = u.rstrip(".,;").split("?")[0].lower()
            if not any(dom in u for dom in COMMON_LINK_DOMAINS):
                urls_by_author[d["author"]].add(u)
                url_accounts[u].add(d["author"])
        for t in HASHTAG_RE.findall(d["text"]):
            tags_by_author[d["author"]].add(t.lower())
            tag_accounts[t.lower()].add(d["author"])

    now = datetime.now(timezone.utc).timestamp()
    if topic is None and meta.get("narrative_topic"):
        topic = TopicMatcher(NARRATIVE_KEYWORDS, min_hits=1, substring=True)
    return {
        "docs": docs, "docs_by_author": by_author,
        "copied_from_others": copied_from_others, "copy_partners": copy_partners,
        "self_copy_threads": {k: len(v) for k, v in self_pairs_threads.items()},
        "co_timed": co_timed,
        "urls_by_author": urls_by_author, "url_accounts": url_accounts,
        "tags_by_author": tags_by_author, "tag_accounts": tag_accounts,
        "cohort": cohort if cohort is not None else cohort_stats(accounts, now),
        "now": now,
        "topic": topic,
    }


# --------------------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------------------
def criterion_result(cid: str, fired: list[str], assessed: bool, evidence: dict) -> dict:
    pct = max(0, 100 - sum(SCORING_TABLE[s]["points"] for s in fired)) if assessed else UNASSESSED_PCT
    return {
        "criterion": CRITERIA[cid], "weight": WEIGHTS[cid], "assessed": assessed,
        "score_pct": pct, "weighted_points": round(WEIGHTS[cid] * pct / 100, 2),
        "signals": [{"signal": s, "points": -SCORING_TABLE[s]["points"], "why": SCORING_TABLE[s]["why"]} for s in fired],
        "evidence": evidence,
    }


def score_profile(name: str, a: dict, ctx: dict) -> dict:
    if a["fetch_status"] not in ("ok", "suspended"):
        return {"username": name, "authenticity_score": None, "inauthenticity_score": None,
                "authenticity_label": "no_data", "fetch_status": a["fetch_status"], "criteria": {}}
    now = ctx["now"]
    crit = {
        "C1": criterion_result("C1", *c1_signals(a, ctx["cohort"], now)),
        "C2": criterion_result("C2", *c2_signals(name, ctx)),
        "C3": criterion_result("C3", *c3_signals(name, a, ctx)),
        "C4": criterion_result("C4", *c4_signals(a, ctx, now)),
        "C5": criterion_result("C5", *c5_signals(name, a, ctx)),
    }
    score = round(sum(c["weighted_points"] for c in crit.values()), 1)
    red_flags = [k for k, c in crit.items() if c["assessed"] and c["score_pct"] <= RED_FLAG_PCT]
    label = label_for(score)
    if red_flags and label == "likely_authentic":
        label = "uncertain"
    if a["fetch_status"] == "suspended":  # Reddit already judged it; other criteria are hidden, not clean
        label = "suspicious"
    return {
        "username": name,
        "authenticity_score": score,
        "inauthenticity_score": round(100 - score, 1),
        "authenticity_label": label,
        "red_flag_criteria": red_flags,
        "criteria_assessed": [k for k, c in crit.items() if c["assessed"]],
        "history_visible": a.get("history_visible", False),
        "fetch_status": a["fetch_status"],
        "criteria": crit,
    }


def score_accounts(path: Path, ds1_path: Path = Path("data/dataset1/dataset1.jsonl")) -> dict:
    """Offline mode: score every account in a dataset-2 style file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    accounts = data["accounts"]
    ctx = build_context(accounts, ds1_path, data)
    return _summarise([score_profile(n, a, ctx) for n, a in accounts.items()])


def score_live(accounts: dict, seed_comments: list[dict], seed_thread: str,
               topic_keywords: list[str], cohort: dict | None = None) -> dict:
    """Live mode: score the commenters of one analysed thread.

    accounts:        {username: account} as built by scrape_reddit_profiles.assemble()/candor.scoring
    seed_comments:   the thread's comments [{author, thread, text, posted_at}] - compared for
                     copy-paste between commenters, and their thread excluded from co-timing
    seed_thread:     the thread id (without t3_)
    topic_keywords:  terms that describe the thread's topic (C5 single-issue check)
    cohort:          percentile reference; default = frozen dataset-2 reference cohort
    """
    data = {"seed_comments": seed_comments, "scrape_meta": {"seed_threads": [seed_thread]}}
    ctx = build_context(accounts, None, data,
                        topic=TopicMatcher(topic_keywords, min_hits=2) if topic_keywords else None,
                        cohort=cohort if cohort is not None else load_reference_cohort())
    return _summarise([score_profile(n, a, ctx) for n, a in accounts.items()])


def _summarise(profiles: list[dict]) -> dict:
    scored = [p for p in profiles if p["authenticity_score"] is not None]
    summary = {
        "profiles": len(profiles),
        "scored": len(scored),
        "label_counts": dict(Counter(p["authenticity_label"] for p in profiles)),
        "mean_score": round(float(np.mean([p["authenticity_score"] for p in scored])), 1) if scored else None,
        "mean_criterion_pct": {c: round(float(np.mean([p["criteria"][c]["score_pct"] for p in scored])), 1)
                               for c in WEIGHTS} if scored else {},
        "assessed_share": {c: round(sum(c in p["criteria_assessed"] for p in scored) / len(scored), 2)
                           for c in WEIGHTS} if scored else {},
        "signal_counts": dict(Counter(s["signal"] for p in scored for c in p["criteria"].values() for s in c["signals"]).most_common()),
    }
    return {"weights": WEIGHTS, "unassessed_pct": UNASSESSED_PCT, "summary": summary,
            "profiles": sorted(profiles, key=lambda p: (p["authenticity_score"] is None, p["authenticity_score"] or 0))}


def export_reference_cohort(src: Path = Path("data/dataset2/reddit_accounts.json"),
                            dst: Path = REFERENCE_COHORT_PATH) -> None:
    """Freeze dataset 2's cohort percentiles for live scoring (committed, so the server needs no data/)."""
    accounts = json.loads(src.read_text(encoding="utf-8"))["accounts"]
    now = datetime.now(timezone.utc).timestamp()
    stats = cohort_stats(accounts, now)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps({
        "source": str(src), "built_at": datetime.now(timezone.utc).isoformat(),
        "n_accounts": len(stats["karma"]),
        "note": "Reference distribution for live scoring: Reddit commenters from dataset 2 "
                "(Finnish data-centre threads + sampled subreddits). karma_per_day as of built_at.",
        "cohort": {"karma": sorted(stats["karma"]), "karma_per_day": sorted(round(x, 3) for x in stats["karma_per_day"]),
                   "comment_score_p90": stats["comment_score_p90"]},
    }, indent=1), encoding="utf-8")
    print(f"wrote {dst} ({len(stats['karma'])} accounts, comment score p90 {stats['comment_score_p90']})")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--export-cohort"]:
        export_reference_cohort()
        sys.exit()
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "data/dataset2/reddit_accounts.json")
    dst = Path(sys.argv[2] if len(sys.argv) > 2 else "data/dataset2/profile_scores.json")
    result = score_accounts(src)
    dst.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{'score':>5}  {'label':<17} {'user':<24} {'C1':>4} {'C2':>4} {'C3':>4} {'C4':>4} {'C5':>4}  signals")
    for p in result["profiles"]:
        if p["authenticity_score"] is None:
            continue
        cells = "".join(f"{p['criteria'][c]['score_pct']:>4}{'' if p['criteria'][c]['assessed'] else '*'}"
                        .rjust(5) for c in WEIGHTS)
        sigs = ", ".join(s["signal"] for c in p["criteria"].values() for s in c["signals"]) or "-"
        print(f"{p['authenticity_score']:>5}  {p['authenticity_label']:<17} {p['username'][:24]:<24}{cells}  {sigs}")
    print("\n* = not assessable (scored", UNASSESSED_PCT, "%)")
    print("summary:", json.dumps(result["summary"], indent=2))
