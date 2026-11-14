"""
Overview of profile_authenticity_scorer.py output.

Prints and saves (as Markdown next to the scores file):
    1. % of authentic and inauthentic accounts
    2. Criterion distribution (C1-C5) for the 50 most authentic vs the 50 least authentic
    3. Top 50 authentic and top 50 inauthentic accounts with their C1-C5 scores

Usage:
    python score_overview.py [profile_scores.json] [--top 50] [--accounts reddit_accounts.json]

--accounts adds a source-subreddit column / breakdown when the accounts file has
`source_subreddits` (the scrape_reddit_subreddits.py cohort).

Labels: authentic = likely_authentic; inauthentic = suspicious; uncertain is reported separately
(and also as part of "not authentic").
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from collections import Counter
from pathlib import Path

CRIT = ["C1", "C2", "C3", "C4", "C5"]


def pct(n: int, d: int) -> str:
    return f"{n / d:.1%}" if d else "-"


def cell(p: dict, c: str) -> str:
    x = p["criteria"][c]
    return f"{x['score_pct']}{'' if x['assessed'] else '*'}"


def dist_row(name: str, group: list[dict]) -> str:
    out = []
    for c in CRIT:
        v = [p["criteria"][c]["score_pct"] for p in group]
        out.append(f"{st.mean(v):.0f} / {st.median(v):.0f} / {min(v)}" if v else "-")
    return f"| {name} | " + " | ".join(out) + " |"


def table(group: list[dict], sources: dict) -> list[str]:
    src = bool(sources)
    head = "| # | User |" + (" Subreddit |" if src else "") + " Score | Label | C1 | C2 | C3 | C4 | C5 | Signals |"
    sep = "|---|---|" + ("---|" if src else "") + "---|---|---|---|---|---|---|---|"
    rows = [head, sep]
    for i, p in enumerate(group, 1):
        sigs = ", ".join(s["signal"] for c in p["criteria"].values() for s in c["signals"]) or "-"
        sub = f" {', '.join(sources.get(p['username'], [])) or '-'} |" if src else ""
        rows.append(f"| {i} | {p['username']} |{sub} {p['authenticity_score']} | {p['authenticity_label']} | "
                    + " | ".join(cell(p, c) for c in CRIT) + f" | {sigs} |")
    return rows


def overview(scores_path: Path, top: int, accounts_path: Path | None) -> str:
    r = json.loads(scores_path.read_text(encoding="utf-8"))
    allp = r["profiles"]
    P = [p for p in allp if p["authenticity_score"] is not None]
    sources = {}
    if accounts_path and accounts_path.exists():
        acc = json.loads(accounts_path.read_text(encoding="utf-8"))["accounts"]
        sources = {n: a.get("source_subreddits") or [] for n, a in acc.items() if a.get("source_subreddits")}

    n, lab = len(allp), Counter(p["authenticity_label"] for p in allp)
    auth, unc, sus, nod = (lab[k] for k in ("likely_authentic", "uncertain", "suspicious", "no_data"))
    scores = sorted(p["authenticity_score"] for p in P)
    L = [f"# Authenticity overview — {scores_path}", "",
         f"Weights: {r['weights']} · unassessed criteria scored {r.get('unassessed_pct')}% (marked *)", "",
         "## 1. Authentic vs inauthentic", "",
         "| | Accounts | % of all | % of scored |", "|---|---|---|---|",
         f"| **Authentic** (likely_authentic) | {auth} | {pct(auth, n)} | {pct(auth, len(P))} |",
         f"| **Inauthentic** (suspicious) | {sus} | {pct(sus, n)} | {pct(sus, len(P))} |",
         f"| Uncertain | {unc} | {pct(unc, n)} | {pct(unc, len(P))} |",
         f"| Not authentic (uncertain + suspicious) | {unc + sus} | {pct(unc + sus, n)} | {pct(unc + sus, len(P))} |",
         f"| No data (deleted / not found) | {nod} | {pct(nod, n)} | - |",
         f"| **Total** | {n} | | |", "",
         f"Score: min {scores[0]} · p10 {scores[len(scores) // 10]} · median {st.median(scores)} · max {scores[-1]}", ""]

    if sources:
        L += ["### By subreddit", "", "| Subreddit | Accounts | Authentic | Uncertain | Inauthentic | Mean score |",
              "|---|---|---|---|---|---|"]
        for sub in sorted({s for v in sources.values() for s in v}):
            ps = [p for p in allp if sub in sources.get(p["username"], [])]
            c = Counter(p["authenticity_label"] for p in ps)
            sc = [p["authenticity_score"] for p in ps if p["authenticity_score"] is not None]
            L.append(f"| r/{sub} | {len(ps)} | {pct(c['likely_authentic'], len(ps))} | {pct(c['uncertain'], len(ps))} | "
                     f"{pct(c['suspicious'], len(ps))} | {st.mean(sc):.1f} |" if sc else f"| r/{sub} | 0 | | | | |")
        L.append("")

    most = sorted(P, key=lambda p: (-p["authenticity_score"], -len(p.get("criteria_assessed", []))))[:top]
    least = sorted(P, key=lambda p: p["authenticity_score"])[:top]
    L += [f"## 2. C1-C5 distribution: top {top} authentic vs top {top} inauthentic", "",
          "Each cell: mean / median / min criterion %.", "",
          "| Group | C1 | C2 | C3 | C4 | C5 |", "|---|---|---|---|---|---|",
          dist_row(f"Top {top} authentic", most), dist_row(f"Top {top} inauthentic", least), dist_row("All scored", P), "",
          f"## 3. Top {top} inauthentic (lowest scores)", ""] + table(least, sources) + [
          "", f"## 4. Top {top} authentic (highest scores; ties ranked by criteria assessed)", ""] + table(most, sources) + [
          "", "`*` = criterion could not be assessed and was scored at the neutral default."]
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("scores", nargs="?", default="data/dataset2/profile_scores.json")
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--accounts", default=None)
    a = ap.parse_args()
    sp = Path(a.scores)
    accounts = Path(a.accounts) if a.accounts else sp.with_name("reddit_accounts.json")
    md = overview(sp, a.top, accounts)
    out = sp.with_name(sp.stem.replace("profile_scores", "overview") + ".md") if "profile_scores" in sp.stem else sp.with_suffix(".overview.md")
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\n(saved to {out})")
