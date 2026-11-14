"""
Score a labelled test post with scorer.py and report how well it separates the planted
narrative from organic comments.

Usage:
    python evaluate.py [test_1k.json]

"Flagged" = label is suspicious or uncertain (score < 70). Also prints the threshold sweep
so you can see where to set LABELS in scorer.py.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from statistics import mean

from scorer import score_post


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def main(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        post = json.load(f)
    comments = score_post(post)["comments"]
    truth = [c["_truth"]["is_narrative"] for c in comments]
    scores = [c["authenticity_score"] for c in comments]

    print(f"{len(comments)} comments, {sum(truth)} narrative ({sum(truth) / len(truth):.0%})\n")

    by_tier = defaultdict(list)
    for c in comments:
        by_tier[c["_truth"]["tier"]].append(c)
    print(f"{'tier':<11}{'n':>5}{'mean':>7}{'flagged':>9}   top signals")
    for tier in ("blatant", "paraphrase", "subtle", "organic"):
        cs = by_tier.get(tier, [])
        if not cs:
            continue
        flagged = sum(c["authenticity_score"] < 70 for c in cs) / len(cs)
        sigs = Counter(s["signal"] for c in cs for s in c["authenticity_signals"]).most_common(3)
        print(f"{tier:<11}{len(cs):>5}{mean(c['authenticity_score'] for c in cs):>7.1f}{flagged:>9.0%}   "
              + ", ".join(f"{k}({v})" for k, v in sigs))

    print(f"\n{'threshold':>9}  {'precision':>9}  {'recall':>6}  {'f1':>5}   (flag if score < threshold)")
    for th in (40, 50, 60, 70, 80, 90):
        tp = sum(s < th and t for s, t in zip(scores, truth))
        fp = sum(s < th and not t for s, t in zip(scores, truth))
        fn = sum(s >= th and t for s, t in zip(scores, truth))
        p, r, f1 = prf(tp, fp, fn)
        print(f"{th:>9}  {p:>9.2f}  {r:>6.2f}  {f1:>5.2f}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "test_1k.json")
