# Stage 1 (context.dev scraper): backend integration

How a user's Reddit link gets turned into scraped data inside the live Candor API.
Covers the request path, which context.dev calls run, in what order, how they are
rate-limited, cached and failed, and what they hand to the scorer and the narrative engine.

---

## 1. Request path

```
browser (analyze.html)
  │ POST /api/analyze {"url"}            candor/api.py
  ▼
validate + normalise URL ──400 if not a reddit /comments/ link
  │ job_id returned immediately; job queued on EXECUTOR (ThreadPoolExecutor, 8 workers)
  ▼
candor.pipeline.analyze(url, progress)   runs in a worker thread
  │
  ├─ A. scrape thread        candor/scrape.py :: scrape_thread(url)        1 context.dev call
  ├─ B. pick authors         top ~20 unique authors by engagement weight   no calls
  ├─ C. scrape profiles  ┐   candor/scrape.py :: scrape_profiles(names)    ≤20 calls, parallel
  │                      ├── run concurrently (C needs only author names, D needs only comment text)
  ├─ D. narrative buckets┘   narrative-buckets SpatialEngine.process(...)  Gemini, no context.dev
  ├─ E. score accounts       profile_authenticity_scorer, cohort stats frozen from dataset2
  └─ F. aggregate → report.json
  ▼
GET /api/jobs/{id} polled by the browser every ~1.5 s → status / progress / message / report
```

The scraper is only ever called from inside the worker thread. The request handler
never touches context.dev, so `POST /api/analyze` returns in milliseconds.

---

## 2. URL handling (in the request handler)

Already in `api.py::_valid_reddit_url`. Add a normaliser before the job is created:

1. Strip whitespace, query string and fragment.
2. Force host to `www.reddit.com` (accept `old.`, `new.`, `m.`, `np.` variants).
3. Extract the post id from `/comments/<id>/` → `t3_<id>`.
4. `redd.it/<id>` short links: build `https://www.reddit.com/comments/<id>/` directly (no redirect fetch needed).
5. Store `job["post_id"]`; the canonical URL is what gets scraped.

Reject (400) anything without a post id. Comment permalinks (`/comments/<id>/<slug>/<comment_id>/`)
are cut back to the post URL, so the whole thread is analysed.

---

## 3. The context.dev calls

All calls go through **one shared client and one shared rate limiter**, created at
module import in `candor/scrape.py`:

```python
from build_dataset1 import LIMITER, api_scrape, client   # LIMITER = SCRAPE_PER_MINUTE (default 90)
CLIENT = client()                                         # ContextDev(max_retries=1, timeout=240)
```

`LIMITER` is process-global, so it caps the sum of all concurrent jobs, not each job.
This is why uvicorn must stay on `--workers 1` (already the case in `deploy/candor.service`).

### A. Thread scrape (always live, never cached across jobs)

Reuse the logic in `scrape_reddit_posts.py`, moved into importable functions:

| | |
|---|---|
| Request | `POST /web/scrape` `{"url": canonical_url, "formats": {"html": true}, "sharedParams": {"actions": [{"type":"scroll","direction":"down","amount":"viewport","maxScrolls": 8}]}}` |
| Why HTML, not JSON extraction | Reddit's `<shreddit-post>` / `<shreddit-comment>` attributes give exact ids, authors, scores, timestamps and parent ids. LLM JSON extraction is slower (~50 s) and invents fields. context.dev's `reddit-post` prebuilt parser returns no comments. |
| Parse | `parse_thread(html, sub)` → post dict with nested `comments[].replies[]` (RedditPostExport shape) |
| Cost / time | ~2 credits, ~10–30 s |
| Retries | `RATE_LIMITED` → sleep 60 s, up to 3×; `WEBSITE_BLOCKED` → sleep 10 s, up to 3× (same as `fetch_html`) |
| Progress | `progress(0.05, "Scraping post")` before, `progress(0.20, "Captured N of M comments")` after |

`maxScrolls` can rise to 12–15 for big threads at the cost of time. Collapsed
"more replies" are never expanded, so `captured_comment_count < reported_comment_count`
is normal; when captured is below 50% of reported, add a warning to `overall.warnings`.

**Thread failures end the job** (`status="error"`) with a user-facing message:
- no `<shreddit-post>` in the HTML → "Reddit didn't return this post (deleted, private or quarantined?)"
- 0 comments captured → "This post has no visible comments to analyse"
- retries exhausted → "Reddit is blocking the scraper right now, try again in a minute"

### B. Author selection (no calls)

Flatten the comment tree, then per comment compute
`engagement_weight = log(1+upvotes) + 0.5·log(1+n_replies)`. Drop `[deleted]`,
`AutoModerator` and other known bots. Sum weight per author, keep the top
`MAX_AUTHORS` (20; fallback 10 if timing is tight). Every comment is still kept for
clustering and stats; only profile scraping is capped.

### C. Profile scrapes (parallel, cached by username)

Reuse `scrape_reddit_profiles.fetch` + `derive`, but returning dicts rather than writing CLI output:

| | |
|---|---|
| Request | `POST /web/scrape` `{"url": "https://www.reddit.com/user/<name>/overview/?sort=new", "formats": {"parse": true}, "parseParams": {"type":"prebuilt","parser":"reddit-user","limit": 200}}` |
| Cost / time | ~1 credit, ~1–3 s each |
| Concurrency | `ThreadPoolExecutor(5)` per job; real throughput set by the shared `LIMITER` |
| Cache | `data/cache/profiles/<username>.json`, raw parser response + `_fetched_at`. Reuse if younger than `PROFILE_CACHE_TTL` (24 h). Absolute path, so it doesn't depend on CWD. Write via temp file + `os.replace` so two jobs scraping the same user don't corrupt it. |
| Failure mapping | `NOT_FOUND` → `fetch_status="not_found"`, `WEBSITE_ACCESS_ERROR` → `"blocked"`, suspended user → `"suspended"`, anything else → `"error"`. **Never fails the job**; the author is reported as unassessed. Don't cache `"error"`, so a retry gets another attempt. |
| Progress | `0.25 → 0.70`, one tick per finished profile: `"Scraping profiles (7/20)"` |

Output of this step is the same `accounts` dict that `scrape_reddit_profiles.assemble()`
builds (`username, fetch_status, user, comments, posts, history_visible, derived`),
which is exactly what `profile_authenticity_scorer.score_profile` consumes.

### Job deadline

A job has a 150 s budget (`JOB_DEADLINE_S`). If profile scraping hasn't finished by
then, cancel the outstanding futures, mark those authors `fetch_status="timeout"`
(unassessed), add a warning, and carry on to scoring. Better a partial report than a spinner.

---

## 4. Hand-off to the next stages

| Consumer | Gets | Adapter |
|---|---|---|
| Narrative engine (D) | The thread dict wrapped in a list: `[post]` | `narrative-buckets/candor/ingest.flatten_reddit_posts([post])` → `list[Comment]` with `kind, post_id, parent_id, depth, upvote_count, created_utc` |
| Account scorer (E) | `accounts` dict from step C | `build_context(accounts, ds1_path, data)` but with the `cohort` block replaced by stats frozen from `data/dataset2/profile_scores.json`, then `score_profile(name, a, ctx)` per author |
| Report (F) | thread meta (`post_id, title, subreddit, permalink, upvote_count, reported/captured counts`) | copied into `report.post` |

D needs only the thread, and C needs only the author list, so start D in its own
thread right after A finishes and join both before E.

---

## 5. Code layout

```
candor/
  api.py        request handler, job store (exists)
  scrape.py     NEW: normalise_url, scrape_thread, pick_authors, scrape_profiles
                     (logic lifted from scrape_reddit_posts.py / scrape_reddit_profiles.py,
                      no CLI, no relative paths, no prints; progress via callback)
  pipeline.py   NEW: analyze(url, progress) orchestrating A–F
```

**Package name clash:** `narrative-buckets/` also has a top-level package called `candor`
(with its own `pipeline.py`). Both can't be imported as `candor` in one process.
Before wiring in D, rename the narrative one (e.g. `narrative-buckets/candor` →
`narrative/`, imported as `narrative.spatial_engine`) or vendor its modules under
`candor/narrative/`.

---

## 6. Configuration and secrets

| Env var | Used by | Default |
|---|---|---|
| `CONTEXT_DEV_API_KEY` | scrape.py | required |
| `SCRAPE_PER_MINUTE` | shared `LIMITER` | 90 |
| `MAX_AUTHORS` | step B | 20 |
| `PROFILE_CACHE_TTL` | step C | 86400 s |
| `JOB_DEADLINE_S` | pipeline | 150 |
| `GEMINI_API_KEY`, `NVIDIA_API_KEY` | narrative engine | required / optional |

On the VPS these come from `/opt/candor/app/.env` via systemd `EnvironmentFile`
(already configured). The API process does not read `.env` itself, so locally run
`set -a; source .env; set +a` before `uvicorn`. `.env` is never committed.

---

## 7. Budget per analysis

| | context.dev calls | credits | wall time |
|---|---|---|---|
| Thread | 1 | ~2 | 10–30 s |
| Profiles (20, cold cache) | 20 | ~20 | 15–40 s at 90/min |
| Profiles (warm cache) | 0 | 0 | ~0 s |
| **Total, cold** | **21** | **~22** | **~30–70 s** scraping + clustering/scoring |

At 90 scrapes/min, about 4 cold analyses per minute fit before jobs start queuing
behind the limiter. Queued jobs show `"Queued"` then progress normally.
