# Candor

Candor analyses the comments under a Reddit post. It answers two questions:

1. **What are the main narratives?** Comments are grouped into narrative "buckets", and each bucket's share of the conversation is weighted by engagement (upvotes and replies).
2. **How credible are the people pushing them?** Each commenter's account is scored for authenticity (criteria C1–C5).

Built for Junction 2026. Live at **https://candor-junction-2026.tech**. The site has three tabs:

- **Analyze** (`web/index.html`): paste any public Reddit link and the full pipeline runs live on it. Your past analyses are listed below the form (remembered with a cookie).
- **Demo** (`web/demo.html`): a simulated Reddit feed of threads analysed in advance (static data).
- **Dashboard** (`web/dashboard.html`): the narrative map of 4,453 posts and comments from four subreddits.

A finished analysis opens in the same Reddit mockup as the demo (`web/view.html?id=<analysis>`): every comment shows its author's authenticity badge and its narrative, and the sidebar ranks the thread's narratives by engagement and by how credible their commenters are.

---

## How it is hosted

Everything runs on **one Hostinger VPS** (Ubuntu 24.04). nginx serves the static pages and forwards API calls to a Python app on the same machine:

```
Browser
  │
  ▼
nginx :443 (HTTPS, certbot; :80 redirects)   deploy/nginx-candor.conf
  ├── /            static files from /opt/candor/app/web   (index, demo, view, dashboard)
  └── /api/*   →   uvicorn 127.0.0.1:8000                  (FastAPI app candor.api:app)
                    run by systemd as user "candor"         (deploy/candor.service)
                    reads API keys from /opt/candor/app/.env
```

| On the server | Path |
|---|---|
| App code | `/opt/candor/app`: a git clone of github.com/TrishJoshi/Candor-New, owned by `candor` |
| Python virtualenv | `/opt/candor/venv` |
| Secrets | `/opt/candor/app/.env` (mode 600, sent over SSH by `deploy.sh --env`, never committed) |
| Results | `/opt/candor/app/data/candor.db` (SQLite index) and `data/analyses/<id>/` (git-ignored) |
| systemd unit | `/etc/systemd/system/candor.service` |
| nginx site | `/etc/nginx/sites-available/candor` |
| Firewall | ufw: only OpenSSH and Nginx Full open |

Because the pages and the API share one origin, no CORS setup is needed. HTTPS is set up with certbot for candor-junction-2026.tech (and srv2159154.hstgr.cloud); certbot edits the nginx site in place, so the repo copy is only for rebuilding a server.

### Deploying

The server runs whatever is on `main` in GitHub. Commit and push, then:

```bash
./deploy/deploy.sh          # git pull on the server, pip install, restart, health check
./deploy/deploy.sh --env    # also (re)send API keys from the local .env files first
```

[deploy/deploy.sh](deploy/deploy.sh) refuses to run with uncommitted or unpushed changes, so the server never runs code that isn't in GitHub. Don't edit files on the server by hand: the next pull would conflict. `data/` and `.env` are git-ignored, so deploys never touch results or secrets. nginx and certbot are not touched either.

A fresh server: clone the repo to `/opt/candor/app`, add `.env`, then run [deploy/setup_vps.sh](deploy/setup_vps.sh) (installs Python, nginx, git, ufw, the `candor` user, the virtualenv and the systemd service; it never overwrites an existing nginx site).

Useful commands on the server:

```bash
systemctl status candor          # is the API running?
journalctl -u candor -f          # API logs (pipeline errors show up here)
nginx -t && systemctl reload nginx
```

### Running locally

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn candor.api:app --reload --port 8000     # → http://localhost:8000/
```

Locally, FastAPI also serves `web/`, so you don't need nginx. The live pipeline loads both `.env` files itself.

- Run the pipeline without the web app: `python -m candor.live <reddit_url>` (prints the `view.html?id=` to open).
- Tests: `python -m pytest` (app, offline) and `cd narrative-buckets && python -m pytest` (Stage 2 engine).

---

## Live analysis: request flow

```
index.html ──POST /api/analyze {url}──▶ candor/api.py
                                         normalises the URL (candor/urls.py), sets the visitor cookie,
                                         creates the analysis in SQLite, returns its id right away
view.html?id ──GET /api/analyses/{id} (poll)──▶ status, progress, message, summary
             ──GET /api/analyses/{id}/view──▶ Reddit-mockup data, once done
index.html ──GET /api/history──▶ this visitor's analyses (cookie candor_vid)

The job runs candor/live.py::analyze() in a worker thread:
  1. scrape thread     candor/scrape.py      1 context.dev call (JSON extraction + rendered HTML), ~40-50 s
  2. flatten + filter  narrative-buckets     drop bots and deleted comments; at least 8 comments needed
  3. in parallel:
     a. profiles       candor/profiles.py    the 20 most engaged commenters, context.dev reddit-user parser
                                             (~1 credit each, cached 24 h in data/cache/profiles/)
     b. narratives     narrative-buckets     Gemini embeddings -> UMAP -> HDBSCAN -> NVIDIA labels
  4. score accounts    candor/scoring.py     profile_authenticity_scorer.score_live (C1-C5)
  5. outputs           candor/view.py        view.json (mockup) + map.html (narrative map)
```

A typical thread takes about 1 minute (measured: 55 s and 68 s) and ~22 context.dev credits on a cold profile cache.

Every run is stored for later use in `data/analyses/<id>/`: `post.json` (scraped thread), `accounts.json` (profiles), `stage2.json` (narratives), `scores.json`, `view.json`, `map.html`. `data/candor.db` indexes the runs and which visitor ran them.

| Endpoint | |
|---|---|
| `POST /api/analyze {url}` | Start an analysis, or join one already running for the same post. 400 for non-Reddit links, 429 if this IP already has 3 running. |
| `GET /api/analyses/{id}` | Status, progress, message, error, summary |
| `GET /api/analyses/{id}/view` | Mockup data (404 until done) |
| `GET /api/analyses/{id}/map` | Narrative map HTML |
| `GET /api/history` | The visitor's analyses, newest first |
| `GET /api/health` | `{"ok": true}` |

Anyone with an analysis link can open it (ids are random); the history list only shows the visitor's own runs.

**Operating notes**

- Run uvicorn with `--workers 1`: the context.dev rate limiter and the numba lock are per process. Analyses themselves survive restarts; ones running during a restart are marked failed and can be re-run from their page.
- Up to 8 analyses run at once (`ANALYSIS_WORKERS`). The context.dev rate limiter (`SCRAPE_PER_MINUTE`) is shared across all jobs.
- UMAP and HDBSCAN are warmed up at startup and run one at a time (`NUMBA_LOCK`), because they crash when two threads run them at once.

### Account scoring in live mode

`profile_authenticity_scorer.py` was written for dataset 2 (a Finnish data-centre narrative). `score_live()` adapts it to any thread, and the offline dataset-2 scores are unchanged (all 210 accounts identical):

- **Topic (C5 "single issue")**: instead of the hard-coded data-centre keywords, the thread's own keywords: class-based TF-IDF of the thread against the commenters' other activity, dropping any word that appears in more than 2% of that activity (otherwise a word like "trump" made every politics regular look single-issue). A comment counts as on-topic if it contains 2 of the keywords.
- **Comparison cohort (C1 low karma, C4 karma velocity / cheap upvotes)**: a frozen reference of 206 dataset-2 accounts (`reference/cohort_dataset2.json`, rebuilt with `python profile_authenticity_scorer.py --export-cohort`), because 20 commenters are too few for percentiles.
- **Copying and coordination (C2, C3)**: the thread's comments are seed texts, so copy-paste between commenters in the thread is detected, and the thread itself isn't counted as co-timed activity.

## Current status

| Part | Status |
|---|---|
| Analyze tab, live Reddit view, visitor history, Demo, Dashboard tabs | Done |
| Live thread scraping (context.dev) | Done (only the first ~35 top-level comments; Reddit doesn't render the rest) |
| Live narrative buckets and engagement weighting | Done |
| Live account credibility scoring (top 20 commenters) | Done |
| Stored results (`data/analyses`, `data/candor.db`) | Done |
| Git-based deploy | Done |
| HTTPS + domain | Done (certbot) |
| Per-IP limit on `/api/analyze` | 3 concurrent analyses per IP; no daily cap yet |
| Dashboard from live analyses | Not yet: the Dashboard shows the batch narrative map only |

---|---|
| Landing page, demo feed, analyze page | Done |
| VPS deploy scripts (nginx, systemd, ufw, push script) | Done |
| Live thread scraping (context.dev) | Done (only the first ~35 top-level comments; Reddit doesn't render the rest) |
| Live narrative buckets and engagement weighting | Done |
| **Live account credibility scoring** | **Not wired in yet.** `profile_authenticity_scorer.py` works offline on dataset2. The live pipeline doesn't scrape commenters' profiles or add `account_score` to buckets yet. [candor/SCRAPING.md](candor/SCRAPING.md) describes the planned design. |
| HTTPS (certbot) | Not set up |
| Per-IP rate limit on `/api/analyze` | Not set up |

---

## Files

### Web app and deployment

| Path | What |
|---|---|
| [candor/api.py](candor/api.py) | FastAPI app (endpoints above); serves `web/` locally |
| [candor/live.py](candor/live.py) | Live pipeline `analyze(id, url, progress)` |
| [candor/scrape.py](candor/scrape.py) | Scrapes one Reddit thread with context.dev into the base post format |
| [candor/profiles.py](candor/profiles.py) | Picks the most engaged commenters and scrapes their profiles (cached) |
| [candor/scoring.py](candor/scoring.py) | Thread topic keywords + live account scoring |
| [candor/view.py](candor/view.py) | Builds the mockup data (threads, profiles, narratives) |
| [candor/store.py](candor/store.py) | SQLite index + per-analysis files |
| [candor/urls.py](candor/urls.py) | Reddit URL validation and normalisation |
| [candor/SCRAPING.md](candor/SCRAPING.md) | Design doc for the live scraping stage, including the planned profile scraping and scoring |
| [candor/\_\_init\_\_.py](candor/__init__.py) | Adds `narrative-buckets/candor` to the `candor` package's import path, so both codebases import as `candor.*` |
| [web/index.html](web/index.html) | Analyze tab: paste a link, your past analyses |
| [web/view.html](web/view.html) | A live analysis in the Reddit mockup (`?id=`); the template without data |
| [web/demo.html](web/demo.html) | Simulated Reddit feed (template + dataset-2 data) |
| [web/dashboard.html](web/dashboard.html) | Dashboard tab; embeds `web/dashboard/narrative-map.html` |
| [web/analyze.html](web/analyze.html) | Redirects old links to the Analyze tab |
| [mockup/template.html](mockup/template.html) | The Reddit mockup used by demo.html and view.html. `mockup/build_mockup.py` regenerates both (demo from `data/dataset2`). |
| `web/logos/` | Partner logos (context.dev, Hostinger, NVIDIA) |
| [deploy/](deploy/) | `deploy.sh`, `setup_vps.sh`, `candor.service`, `nginx-candor.conf` (see above) |
| [requirements.txt](requirements.txt) | Server dependencies (FastAPI, context.dev, UMAP, HDBSCAN, Gemini/NVIDIA clients). The local MiniLM/torch model is deliberately left out. |
| [tests/](tests/) | Offline tests: API/storage, URL handling, live scoring |

### Narrative buckets (Stage 2)

| Path | What |
|---|---|
| [narrative-buckets/](narrative-buckets/) | Narrative clustering engine. See its [README](narrative-buckets/README.md) and `AGENT.md`. |
| `narrative-buckets/candor/spatial_engine.py` | Embed → UMAP → HDBSCAN narratives → topic grouping → labels |
| `narrative-buckets/candor/ingest.py` | Flattens nested Reddit posts into comment records |
| `narrative-buckets/candor/embedders.py`, `labeling.py`, `representation.py` | Gemini or local embeddings, LLM or keyword labels, c-TF-IDF |
| `narrative-buckets/candor/visualize.py` | Writes the interactive narrative-map HTML |
| `narrative-buckets/candor/pipeline.py` | Batch command-line tool (not used by the live API); produced the Dashboard map |
| `narrative-buckets/tests/` | Offline tests |
| `narrative-buckets/out/` | Batch outputs (reports, embedding caches) |

### Account credibility scoring (Stage 3)

| Path | What |
|---|---|
| [profile_authenticity_scorer.py](profile_authenticity_scorer.py) | Scores Reddit accounts 0–100 on C1 profile (20), C2 originality (20), C3 coordination (15), C4 engagement legitimacy (20), C5 behavioural history (25). 100 = looks authentic. Offline (dataset 2) and live (`score_live`) modes. |
| [reference/cohort_dataset2.json](reference/cohort_dataset2.json) | Frozen comparison cohort for live scoring |
| [score_overview.py](score_overview.py) | Summary of the profile scores |
| [scorer.py](scorer.py), [evaluate.py](evaluate.py), [generate_test_data.py](generate_test_data.py) | Earlier comment-level scorer (Facebook format), its evaluation and synthetic test data |

### Scraping and datasets

| Path | What |
|---|---|
| [build_dataset1.py](build_dataset1.py) | Shared context.dev client and rate limiter (`LIMITER`, `SCRAPE_PER_MINUTE`), dataset 1 builder |
| [scrape_reddit_posts.py](scrape_reddit_posts.py), [scrape_reddit_profiles.py](scrape_reddit_profiles.py), [scrape_reddit_subreddits.py](scrape_reddit_subreddits.py) | Batch Reddit scrapers |
| [context_scraper.py](context_scraper.py) | Original context.dev scraper (Facebook / X) |
| [schemas/](schemas/) | JSON schemas for the post export and dataset 2 |
| [mockup/](mockup/) | Template and build script for the demo feed |
| `data/dataset2/` | Reddit accounts and their profile scores (input to the demo feed) |
| `data/reddit-dataset-posts/` | Scraped Reddit posts |
| `data/analyses/`, `data/candor.db` | Every live analysis (see above) |
| `data/cache/profiles/` | Scraped commenter profiles, reused for 24 h |
| `data/dataset1/`, `data/authenticity/` | Earlier labelled datasets |

### Environment variables

| Variable | Used by | Needed |
|---|---|---|
| `CONTEXT_DEV_API_KEY` | scraping | yes |
| `GEMINI_API_KEY` | embeddings | yes |
| `NVIDIA_API_KEY`, `NVIDIA_BASE_URL`, `NVIDIA_MODEL` | narrative labels | optional (keyword labels without them) |
| `ANALYSIS_WORKERS` | API: concurrent jobs | optional, default 8 |
| `MAX_ACTIVE_PER_IP` | API: concurrent analyses per IP | optional, default 3 |
| `MAX_AUTHORS` | profiles scraped and scored per thread | optional, default 20 |
| `PROFILE_DEADLINE_S`, `PROFILE_CACHE_TTL` | profile scraping time budget / cache age | optional, 120 s / 24 h |
| `SCRAPE_PER_MINUTE` | shared context.dev rate limit | optional, default 90 |

Keys live in `.env` (root) and `narrative-buckets/.env`. Both files are gitignored.
