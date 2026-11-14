"""
Dataset 1 — "can it separate narratives into buckets?"

Builds a 1,000-item dataset of REAL posts/comments scraped with context.dev:
    400 related to the target topic (Google data centres in Finland)
    600 unrelated (half near-topic distractors, half other debates)

Pipeline (each stage caches to data/dataset1/, so re-runs don't spend credits twice):
    python build_dataset1.py search    # context.dev web search -> candidate URLs per query group
    python build_dataset1.py scrape    # scrape each URL, extract individual posts/comments as JSON
    python build_dataset1.py build     # clean, dedupe, filter, sample 400/600, write outputs
    python build_dataset1.py all       # all three

Setup:
    pip install context.dev
    export CONTEXT_DEV_API_KEY=ctxt_secret_...

Logged-in scraping (optional, any paid context.dev plan — "browser profiles"):
    export SCRAPE_LOGIN_USER=... SCRAPE_LOGIN_PASSWORD=...   # a dedicated research account
    python build_dataset1.py login https://www.facebook.com/
    # saves the returned prof_... id per domain in data/dataset1/profiles.json (no password on disk);
    # scrape then uses it automatically for that domain. Accounts with MFA/CAPTCHA won't work.
    # On PROFILE_SESSION_EXPIRED, run `login` again and re-run scrape (finished pages stay cached).

Outputs:
    data/dataset1/dataset1.jsonl       one item per line (text + provenance + labels)
    data/dataset1/dataset1_labels.csv  same items as a sheet with an empty `narrative_bucket`
                                       column, for hand-labelling the ground-truth buckets

Labels:
    topic_label     "related" | "unrelated" — from which query group found the page, then
                    checked against keywords (unrelated pages that mention the topic are dropped)
    query_group     which search group found it — a weak hint at the narrative, NOT ground truth
    narrative_bucket  left empty; fill by hand (see SUGGESTED_BUCKETS) to score the clustering
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import re
import sys
import time
from pathlib import Path

OUT = Path("data/dataset1")
SEED = 42
TARGETS = {"related": 400, "unrelated": 600}

# --------------------------------------------------------------------------------------
# CONFIG — edit topic, queries and sources here
# --------------------------------------------------------------------------------------
TOPIC = "Google data centres in Finland (Hamina, Muhos, Kajaani)"

# Social media only — search, scrape and build all ignore every other domain.
# Each "search pass" runs every query restricted to those domains, so Facebook and Reddit
# get their own result budget instead of competing with each other.
SEARCH_PASSES = {
    "facebook": ["facebook.com"],
    "reddit": ["reddit.com"],
}
SOCIAL_DOMAINS = [d for ds in SEARCH_PASSES.values() for d in ds]
LANGUAGES = ["en", "fi"]

# Groups are deliberately stance/angle-flavoured so search surfaces different narratives.
RELATED_QUERIES = {
    "jobs_economy":   ["Google data center Finland jobs investment", "Google datakeskus Muhos työpaikat",
                       "Google Kajaani datakeskus investointi"],
    "energy_prices":  ["Google data center Finland electricity price", "datakeskukset sähkön hinta Suomi Google",
                       "Google Hamina wind power electricity consumption"],
    "heat_reuse":     ["Google Hamina data center district heating waste heat", "Google datakeskus hukkalämpö kaukolämpö Hamina"],
    "environment":    ["Google data center Finland environment water nature", "Google Muhos datakeskus luonto vastustus"],
    "sovereignty_tax":["Google Finland data center taxes subsidies big tech", "Google datakeskus veroedut Suomi"],
    "general":        ["Google data center Finland", "Google datakeskus Suomi", "Google Hamina data center"],
}
TOPIC_KEYWORDS = [
    "google", "hamina", "muhos", "kajaani", "data center", "data centre", "datacenter", "datakeskus",
    "palvelinkeskus", "konesali",
]

# Unrelated = half "hard negatives" (close in vocabulary/theme) + half general debates.
UNRELATED_QUERIES = {
    "near_other_dc":      ["Microsoft data center Espoo district heating", "data center Ireland electricity grid debate"],
    "near_fi_energy":     ["Finland electricity price winter discussion", "Olkiluoto 3 nuclear opinion Finland"],
    "near_ai_tech":       ["AI energy consumption debate", "big tech layoffs discussion"],
    "other_housing":      ["Helsinki housing prices rent discussion"],
    "other_transport":    ["electric cars vs hybrids debate", "remote work return to office debate"],
    "other_culture":      ["Finland education system opinion", "Eurovision Finland reaction"],
}

SUGGESTED_BUCKETS = [  # for hand-labelling; edit freely
    "jobs_and_regional_economy", "electricity_prices_and_grid", "green_heat_reuse_positive",
    "environment_land_water_concerns", "big_tech_distrust_tax_sovereignty", "neutral_info_or_question", "other",
]

SEARCH_RESULTS_PER_QUERY = 50     # 1 credit per 10 results
MAX_ITEMS_PER_PAGE = 40           # stops one huge thread dominating the dataset
# Pages to scrape per label (incl. already-scraped ones). ~7 usable items/page, so this gives
# ~2x headroom over 400/600 after filtering. Raise if `build` reports a shortfall.
PAGE_BUDGET = {"related": 120, "unrelated": 160}
MIN_CHARS, MIN_WORDS = 25, 4      # drops "lol", "+1", emoji-only, etc.
BOT_AUTHORS = {"automoderator", "[deleted]", "[removed]", "remindmebot", "sneakpeekbot"}

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "page_title": {"type": "string"},
        "items": {
            "type": "array",
            "description": "The main post/article and EVERY user comment or reply on the page, one entry each",
            "items": {
                "type": "object",
                "properties": {
                    "item_type": {"type": "string", "enum": ["post", "comment", "reply", "article"]},
                    "author": {"type": ["string", "null"]},
                    "text": {"type": "string", "description": "Verbatim text, untranslated, no summarising"},
                    "created_time": {"type": ["string", "null"], "description": "ISO 8601 if determinable"},
                    "reply_to_author": {"type": ["string", "null"]},
                },
                "required": ["item_type", "author", "text", "created_time", "reply_to_author"],
            },
        },
    },
    "required": ["page_title", "items"],
}
EXTRACT_INSTRUCTIONS = (
    "List the main post or article and every user comment/reply visible on the page as separate items. "
    "Copy text verbatim in its original language. For articles, put the article body (max ~1500 chars) as "
    "one 'article' item. Skip navigation, ads, cookie banners, 'related posts' and sidebar content."
)


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
def client():
    from context.dev import ContextDev
    # our own limiter/retry handles 429s; long timeout because JSON extraction takes ~50s/page
    return ContextDev(api_key=os.environ["CONTEXT_DEV_API_KEY"], max_retries=1, timeout=240)


def as_dict(obj):
    for m in ("to_dict", "model_dump"):
        if hasattr(obj, m):
            return getattr(obj, m)()
    return obj


def key(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def mentions_topic(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in TOPIC_KEYWORDS)


def domain_of(url: str) -> str:
    return re.sub(r"^(www|m|old|mobile)\.", "", re.sub(r"^https?://([^/]+).*", r"\1", url).lower())


def is_social(url: str) -> bool:
    d = domain_of(url)
    return any(d == s or d.endswith("." + s) for s in SOCIAL_DOMAINS)


PROFILES_FILE = OUT / "profiles.json"


def load_profiles() -> dict[str, str]:
    return json.loads(PROFILES_FILE.read_text()) if PROFILES_FILE.exists() else {}


def api_scrape(c, body: dict) -> dict:
    """Raw REST call so profile fields go over the wire exactly as documented (camelCase)."""
    return c.post("/web/scrape", body=body, cast_to=object)  # object -> parsed JSON dict


# --------------------------------------------------------------------------------------
# login: create a context.dev browser profile for one site
# --------------------------------------------------------------------------------------
def stage_login(url: str) -> None:
    user, pw = os.environ.get("SCRAPE_LOGIN_USER"), os.environ.get("SCRAPE_LOGIN_PASSWORD")
    if not (user and pw):
        sys.exit("set SCRAPE_LOGIN_USER and SCRAPE_LOGIN_PASSWORD first")
    res = api_scrape(client(), {
        "url": url,
        "formats": {"markdown": True},
        "sharedParams": {"profile": {"type": "new", "identifier": user, "password": pw,
                                     "description": f"dataset1 scraper - {domain_of(url)}"}},
        "timeoutOpts": {"milliseconds": 120000},
    })
    pid = (res.get("profile") or {}).get("id")
    if not pid:
        sys.exit(f"login failed: {json.dumps(res)[:500]}")
    OUT.mkdir(parents=True, exist_ok=True)
    profiles = load_profiles()
    profiles[domain_of(url)] = pid
    PROFILES_FILE.write_text(json.dumps(profiles, indent=2))
    print(f"profile {pid} saved for {domain_of(url)}")


# --------------------------------------------------------------------------------------
# stage 1: search
# --------------------------------------------------------------------------------------
def stage_search() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "urls.jsonl"
    done = {(r["query"], r["scope"]) for r in read_jsonl(path)}
    c = client()
    jobs = [("related", g, q) for g, qs in RELATED_QUERIES.items() for q in qs]
    jobs += [("unrelated", g, q) for g, qs in UNRELATED_QUERIES.items() for q in qs]
    with path.open("a", encoding="utf-8") as f:
        for label, group, q in jobs:
            for scope, domains in SEARCH_PASSES.items():
                if (q, scope) in done:
                    continue
                kw = {"query": q, "num_results": SEARCH_RESULTS_PER_QUERY, "languages": LANGUAGES,
                      "include_domains": domains}
                try:
                    res = as_dict(c.web.search(**kw))
                except Exception as e:  # keep going; one bad query shouldn't kill the run
                    print(f"  ! search failed [{q}] ({scope}): {e}")
                    continue
                for r in res.get("results", []):
                    url = r.get("url") or r.get("link")
                    if url:
                        f.write(json.dumps({"url": url, "title": r.get("title"), "relevance": r.get("relevance"),
                                            "label": label, "group": group, "query": q, "scope": scope}) + "\n")
                print(f"  {label:<9} {group:<16} {scope:<10} {len(res.get('results', [])):>3}  {q}")
    urls = {r["url"] for r in read_jsonl(path)}
    print(f"search done: {len(urls)} unique URLs in {path}")


# --------------------------------------------------------------------------------------
# stage 2: scrape
# --------------------------------------------------------------------------------------
def stage_scrape(limit: int | None = None) -> None:
    pages = OUT / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    seen, todo = set(), []
    for r in read_jsonl(OUT / "urls.jsonl"):  # first query that found a URL owns its label
        if r["url"] not in seen and r.get("relevance") != "low" and is_social(r["url"]):
            seen.add(r["url"])
            todo.append(r)
    todo = prioritise([r for r in todo if not (pages / f"{key(r['url'])}.json").exists()], pages)[:limit]
    print(f"scraping {len(todo)} pages (cached ones skipped, budget {PAGE_BUDGET})")
    c = client()
    profiles, dead = load_profiles(), set()
    from concurrent.futures import ThreadPoolExecutor
    # one request at a time per logged-in profile (PROFILE_BUSY), so only parallelise without profiles
    workers = 1 if profiles else int(os.environ.get("SCRAPE_WORKERS", "10"))
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(lambda a: _scrape_one(c, pages, profiles, dead, a[0], len(todo), a[1]), enumerate(todo, 1)))


def prioritise(todo: list[dict], pages: Path) -> list[dict]:
    """Keep only PAGE_BUDGET pages per label (minus ones already scraped). Within a label,
    round-robin across query groups so every narrative angle is represented, and inside each
    group take high-relevance Reddit first (Reddit threads yield the most comments per page)."""
    from collections import defaultdict
    from itertools import zip_longest
    done = defaultdict(int)
    for p in pages.glob("*.json"):
        rec = json.loads(p.read_text(encoding="utf-8"))
        if is_social(rec["url"]):
            done[rec["label"]] += 1
    out = []
    for label, budget in PAGE_BUDGET.items():
        groups = defaultdict(list)
        for r in todo:
            if r["label"] == label:
                groups[r["group"]].append(r)
        for g in groups.values():
            g.sort(key=lambda r: (r.get("relevance") != "high", domain_of(r["url"]) != "reddit.com"))
        interleaved = [r for row in zip_longest(*groups.values()) for r in row if r]
        out += interleaved[:max(0, budget - done[label])]
    return out


class RateLimiter:
    """Spaces request starts so we stay under the key's per-minute scrape limit."""
    def __init__(self, per_minute: int):
        import threading
        self.gap, self.next, self.lock = 60.0 / per_minute, 0.0, threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next)
            self.next = start + self.gap
        time.sleep(max(0.0, start - now))


# context.dev plan: 100 req/min, 500 concurrent. 90 leaves headroom for retries. The limit is per API key,
# so running a batch scrape script while the server is live shares (and can exceed) the same budget.
LIMITER = RateLimiter(int(os.environ.get("SCRAPE_PER_MINUTE", "90")))


def _scrape_one(c, pages: Path, profiles: dict, dead: set, i: int, total: int, r: dict) -> None:
    dom = domain_of(r["url"])
    if dom in dead:
        return  # session expired earlier this run; leave uncached so a re-run retries it
    body = {"url": r["url"], "formats": {"json": True},
            "jsonParams": {"schema": EXTRACT_SCHEMA, "instructions": EXTRACT_INSTRUCTIONS}}
    if dom in profiles:
        body["sharedParams"] = {"profile": {"type": "existing", "profileId": profiles[dom]}}
    try:
        for attempt in range(4):  # on 429, wait out the minute window and retry (429s aren't billed)
            LIMITER.wait()
            try:
                page = api_scrape(c, body)
                break
            except Exception as e:
                if "RATE_LIMITED" not in str(e) or attempt == 3:
                    raise
                time.sleep(60)
        j = page.get("json") or {}
        if not j.get("success"):
            raise RuntimeError(j.get("error_code") or j.get("message") or "extraction failed")
        data = j["data"]
    except Exception as e:
        print(f"  [{i}/{total}] ! {r['url']}: {e}")
        if any(code in str(e) for code in ("PROFILE_SESSION_EXPIRED", "PROFILE_NOT_FOUND",
                                           "PROFILE_REAUTHENTICATION_REQUIRED", "PROFILE_INTERACTION_REQUIRED")):
            print(f"  ! {dom} login no longer valid — run `login` again; skipping {dom} for now")
            dead.add(dom)
        return  # not cached, so a re-run retries it
    (pages / f"{key(r['url'])}.json").write_text(
        json.dumps({**r, "page": data}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  [{i}/{total}] {len(data.get('items', [])):>3} items  {r['url'][:90]}", flush=True)


# --------------------------------------------------------------------------------------
# stage 3: build
# --------------------------------------------------------------------------------------
def stage_build() -> None:
    rng = random.Random(SEED)
    pools: dict[str, list[dict]] = {"related": [], "unrelated": []}
    seen_text: set[str] = set()
    dropped = {"short": 0, "dup": 0, "unrelated_mentions_topic": 0, "page_cap": 0}

    for p in sorted((OUT / "pages").glob("*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        if not is_social(rec["url"]):
            dropped["not_social_media"] = dropped.get("not_social_media", 0) + 1
            continue
        items = rec["page"].get("items", [])
        kept = []
        for idx, it in enumerate(items):
            text = re.sub(r"\s+", " ", (it.get("text") or "")).strip()
            if (it.get("author") or "").strip().lower() in BOT_AUTHORS:
                dropped["bot"] = dropped.get("bot", 0) + 1
                continue
            if len(text) < MIN_CHARS or len(text.split()) < MIN_WORDS:
                dropped["short"] += 1
                continue
            norm = re.sub(r"\W+", " ", text.lower()).strip()
            if norm in seen_text:
                dropped["dup"] += 1
                continue
            hit = mentions_topic(text)
            if rec["label"] == "unrelated" and hit:
                dropped["unrelated_mentions_topic"] += 1
                continue
            seen_text.add(norm)
            kept.append({
                "item_id": key(rec["url"] + str(idx) + text[:50]),
                "text": text,
                "item_type": it.get("item_type"),
                "author": it.get("author"),
                "created_time": it.get("created_time"),
                "reply_to_author": it.get("reply_to_author"),
                "source_url": rec["url"],
                "source_domain": re.sub(r"^www\.", "", re.sub(r"^https?://([^/]+).*", r"\1", rec["url"])),
                "page_title": rec["page"].get("page_title"),
                "topic_label": rec["label"],
                "query_group": rec["group"],
                "query": rec["query"],
                # related-page comments often don't repeat the keywords ("great for jobs") but are
                # still on-topic in context; this flag helps you spot-check the ones that drifted
                "mentions_topic_keyword": hit,
                "narrative_bucket": None,
            })
        if len(kept) > MAX_ITEMS_PER_PAGE:
            dropped["page_cap"] += len(kept) - MAX_ITEMS_PER_PAGE
            kept = rng.sample(kept, MAX_ITEMS_PER_PAGE)
        pools[rec["label"]].extend(kept)

    final = []
    for label, n in TARGETS.items():
        pool = pools[label]
        if label == "related":  # prefer items that clearly mention the topic, then fill with thread replies
            pool.sort(key=lambda x: (not x["mentions_topic_keyword"], rng.random()))
            pick = pool[:n]
        else:
            pick = rng.sample(pool, min(n, len(pool)))
        if len(pick) < n:
            print(f"  ! only {len(pick)}/{n} {label} items — add queries/sources and re-run search+scrape")
        final.extend(pick)
    rng.shuffle(final)

    with (OUT / "dataset1.jsonl").open("w", encoding="utf-8") as f:
        for it in final:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    cols = ["item_id", "topic_label", "query_group", "narrative_bucket", "text", "source_domain", "source_url"]
    with (OUT / "dataset1_labels.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(final)

    from collections import Counter
    print(f"\nwrote {len(final)} items -> {OUT/'dataset1.jsonl'} and dataset1_labels.csv")
    print("dropped:", dropped)
    print("by label:", dict(Counter(i["topic_label"] for i in final)))
    print("by group:", dict(Counter(i["query_group"] for i in final)))
    print("by domain (top 10):", Counter(i["source_domain"] for i in final).most_common(10))
    print("suggested buckets for labelling:", ", ".join(SUGGESTED_BUCKETS))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "login":
        stage_login(sys.argv[2])
        sys.exit()
    lim = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if cmd in ("search", "all"):
        stage_search()
    if cmd in ("scrape", "all"):
        stage_scrape(lim)
    if cmd in ("build", "all"):
        stage_build()
