"""Live-mode tests for profile_authenticity_scorer (no network, synthetic accounts)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import profile_authenticity_scorer as S

NOW = datetime.now(timezone.utc)
COHORT = {"karma": [10, 100, 1000, 5000, 20000, 80000], "karma_per_day": [0.1, 1, 5, 20, 50, 100],
          "comment_score_p90": 20.0}


def iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def account(name, *, age_days=2000, karma=5000, comments=None, history=True):
    comments = comments if comments is not None else [
        {"id": f"{name}_{i}", "body": f"ordinary remark number {i} about cooking pasta", "score": 3,
         "posted_at": iso(i * 1.3), "subreddit": f"sub{i % 7}", "post": {"id": f"p{name}{i}"}}
        for i in range(30)]
    return {"username": name, "fetch_status": "ok", "history_visible": history,
            "user": {"username": name, "created_at": iso(age_days), "total_karma": karma,
                     "post_karma": 10, "comment_karma": karma, "has_verified_email": True,
                     "avatar_url": "https://styles.redditmedia.com/custom.png"},
            "comments": comments if history else [], "posts": []}


def test_topic_matcher_needs_two_hits_and_whole_words():
    m = S.TopicMatcher(["filibuster", "schumer", "senate"], min_hits=2)
    assert m.matches("Schumer should end the filibuster")
    assert not m.matches("the senate is in session")          # one generic hit only
    assert not m.matches("Senatematters schumerish")           # no partial-word matches
    legacy = S.TopicMatcher(["datakeskus"], min_hits=1, substring=True)
    assert legacy.matches("Haminan datakeskuksen")  is False   # legacy behaviour kept: exact substring
    assert legacy.matches("uusi datakeskus Haminaan")


def test_live_flags_single_issue_and_copy_ring_but_not_regulars():
    topic = ["filibuster", "schumer", "senate", "majority leader"]
    shill_comments = [
        {"id": f"s{i}", "body": f"Schumer must go now, the filibuster and the senate are broken {i}",
         "score": 2, "posted_at": iso(i * 0.5), "subreddit": "politics", "post": {"id": f"t{i}"}}
        for i in range(20)]
    accounts = {"regular": account("regular"),
                "shill": account("shill", age_days=12, karma=40, comments=shill_comments)}
    pasted = "Everyone needs to call their senators today and demand Schumer step down as leader."
    seeds = [{"author": a, "thread": "abc123", "text": pasted, "posted_at": iso(0.01)}
             for a in ("shill", "regular")] + [
            {"author": "regular", "thread": "abc123", "text": "I think he has done fine honestly", "posted_at": iso(0.02)}]
    # regular also pasted the same text -> both get the copy signal; only the shill is single-issue/new
    out = S.score_live(accounts, seeds, "abc123", topic, cohort=COHORT)
    by = {p["username"]: p for p in out["profiles"]}
    sig = lambda u: {s["signal"] for c in by[u]["criteria"].values() for s in c["signals"]}
    assert {"single_issue_strong", "age_lt_30d", "karma_very_low", "copies_others"} <= sig("shill")
    assert "single_issue" not in sig("regular") and "single_issue_strong" not in sig("regular")
    assert by["shill"]["authenticity_score"] < by["regular"]["authenticity_score"]
    assert by["shill"]["criteria"]["C5"]["evidence"]["topic_keywords"][:2] == ["filibuster", "schumer"]


def test_live_uses_reference_cohort_not_batch():
    # alone, a 1500-karma account is the batch's bottom 10%; against the reference it is not
    out = S.score_live({"mid": account("mid", karma=1500)}, [], "x", ["a", "b"], cohort=COHORT)
    signals = {s["signal"] for c in out["profiles"][0]["criteria"].values() for s in c["signals"]}
    assert "karma_below_cohort" not in signals


def test_default_reference_cohort_file_loads():
    c = S.load_reference_cohort()
    assert len(c["karma"]) > 100 and c["comment_score_p90"] > 0


def test_hidden_history_and_failed_fetch():
    accounts = {"hidden": account("hidden", history=False),
                "gone": {"username": "gone", "fetch_status": "not_found"}}
    by = {p["username"]: p for p in S.score_live(accounts, [], "x", ["a", "b"], cohort=COHORT)["profiles"]}
    assert by["gone"]["authenticity_label"] == "no_data"
    assert any(s["signal"] == "history_hidden" for s in by["hidden"]["criteria"]["C5"]["signals"])
