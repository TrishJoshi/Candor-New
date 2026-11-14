"""Build reddit_mockup.html: a Reddit-style feed where every user carries their authenticity + C1-C5 scores.

Usage: python3 mockup/build_mockup.py
Reads data/dataset2/reddit_accounts.json + profile_scores.json, injects them into mockup/template.html.
The served pages (web/view.html, web/demo.html) come from `python -m candor.examples`.
"""
import json, re, collections
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data" / "dataset2"
accounts = json.load(open(D / "reddit_accounts.json"))
scores = json.load(open(D / "profile_scores.json"))
acc = accounts["accounts"]
sc = {p["username"]: p for p in scores["profiles"]}

TOPIC = re.compile(r"google|data ?cent|datakesk|hamina|muhos|kajaani", re.I)
N_TOPIC_THREADS = 10

threads = collections.defaultdict(lambda: {"comments": {}, "post": None, "title": None, "sub": None, "url": None})
for u, a in acc.items():
    for c in a.get("comments", []):
        t = threads[c["post"]["id"]]
        t["title"], t["sub"], t["url"] = c["post"]["title"], c["subreddit"], c["post"]["url"]
        t["comments"][c["id"]] = c
    for p in a.get("posts", []):
        t = threads[p["id"]]
        t["title"], t["sub"], t["url"], t["post"] = p["title"], p["subreddit"], p["url"], p

def n_authors(t):
    return len({c["author"] for c in t["comments"].values()} | ({t["post"]["author"]} if t["post"] else set()))

topic = [(k, t) for k, t in threads.items() if TOPIC.search(t["title"] or "")]
topic.sort(key=lambda kv: (-n_authors(kv[1]), -len(kv[1]["comments"])))
chosen = [k for k, _ in topic[:N_TOPIC_THREADS]]

# Make sure flagged ("uncertain"/"suspicious") authors with visible history show up in the feed.
for u, p in sc.items():
    if p["authenticity_label"] in ("uncertain", "suspicious") and u in acc:
        a = acc[u]
        own = [x["id"] for x in a.get("posts", []) if TOPIC.search(x["title"])][:2]
        cnt = collections.Counter(c["post"]["id"] for c in a.get("comments", []) if TOPIC.search(c["post"]["title"]))
        for pid in own + [pid for pid, _ in cnt.most_common(2)]:
            if pid not in chosen:
                chosen.append(pid)

def date(s):
    return s

out_threads = []
authors = set()
for pid in chosen:
    t = threads[pid]
    p = t["post"]
    post = {
        "id": pid, "sub": t["sub"], "title": t["title"], "url": t["url"],
        "author": p["author"] if p else None,
        "body": (p.get("body") or "") if p else "",
        "link": p.get("link_url") if p else None,
        "score": p.get("score") if p else None,
        "num_comments": p.get("comment_count") if p else None,
        "posted_at": p.get("posted_at") if p else None,
    }
    if p:
        authors.add(p["author"])
    cs = []
    for c in sorted(t["comments"].values(), key=lambda c: c["posted_at"]):
        authors.add(c["author"])
        cs.append({"id": c["id"], "author": c["author"], "body": c["body"], "score": c["score"],
                   "posted_at": c["posted_at"], "parent": c.get("parent_id")})
    if post["posted_at"] is None and cs:
        post["posted_at"] = cs[0]["posted_at"]
    post["comments"] = cs
    out_threads.append(post)

out_threads.sort(key=lambda t: t["posted_at"] or "", reverse=True)

profiles = {}
for u, p in sc.items():
    a = acc.get(u, {})
    user = a.get("user") or {}
    d = a.get("derived") or {}
    profiles[u] = {
        "score": p.get("authenticity_score"),
        "label": p.get("authenticity_label"),
        "red": p.get("red_flag_criteria", []),
        "fetch": p.get("fetch_status"),
        "history": p.get("history_visible"),
        "criteria": {k: {"name": v["criterion"], "weight": v["weight"], "assessed": v["assessed"],
                         "pct": v["score_pct"], "signals": v["signals"], "evidence": v.get("evidence", {})}
                     for k, v in (p.get("criteria") or {}).items()},
        "karma": user.get("total_karma"),
        "age_days": d.get("account_age_days"),
        "avatar": user.get("avatar_url"),
        "bio": user.get("bio"),
    }

data = {
    "meta": {"topic": accounts["scrape_meta"]["narrative_topic"], "scraped_at": accounts["scrape_meta"]["scraped_at"],
             "weights": scores["weights"], "unassessed_pct": scores["unassessed_pct"], "summary": scores["summary"]},
    "threads": out_threads,
    "profiles": profiles,
}
tpl = (Path(__file__).parent / "template.html").read_text()
html = tpl.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
(Path(__file__).parent / "reddit_mockup.html").write_text(html)
# web/view.html and web/demo.html (the demo feed of bundled live analyses) are written by
# `python -m candor.examples`; this script only builds the original dataset-2 mockup.
print(f"{len(out_threads)} threads, {sum(len(t['comments']) for t in out_threads)} comments, {len(authors)} authors")
