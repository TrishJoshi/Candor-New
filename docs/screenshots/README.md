# Candor screenshots

Every feature of https://candor-junction-2026.tech, captured from the live site (desktop at 1440×900, 2× pixel density; mobile at 390×844, 3×). Files ending in `-full` are full-page captures. The example thread used throughout is the r/trump post "You're not alone" (20 commenters scored, 7 flagged).

## Analyze tab

| File | Shows |
|---|---|
| `01-analyze.png` | Landing / Analyze tab: demo feed or paste a Reddit link |
| `02-analyze-paste-link.png` | A Reddit link pasted, ready to analyse |
| `03-analyze-full.png` | Whole page, including the "Powered by" partners |

## Demo feed

| File | Shows |
|---|---|
| `04-demo-feed.png` | Feed of real threads analysed by the live pipeline |
| `05-demo-feed-full.png` | Whole feed |
| `06-demo-card-health.png` | One card: narrative distribution, AI summary, authenticity health |

## A result (Reddit view with Candor's layer)

| File | Shows |
|---|---|
| `07-result-thread.png` | The thread with narratives and authenticity badges |
| `08-result-full.png` | Whole result page |
| `09-narrative-distribution-pill.png` | Top narratives' share of engagement |
| `10-narratives-sidebar.png` | Narratives ranked by engagement, with AI summaries and avg authenticity |
| `11-authenticity-shield-card.png` | Account breakdown (likely authentic / uncertain / suspicious) and C1–C5 averages |
| `12-thread-health.png` | Compact "20 scored · 7 flagged" health line |
| `13-mini-dashboard.png` | Mini dashboard: which kinds of accounts push each narrative |
| `14-account-score-popover.png` | A flagged account's score and its five criteria |
| `15-account-score-popover-authentic.png` | An authentic account's score |
| `16-narrative-highlight.png` | Clicking a narrative chip highlights its comments |
| `17-tooltip.png` | Every number explains itself on hover |
| `18-labels-off.png` | Authenticity labels toggled off (plain Reddit) |
| `19-breakdown-and-method.png`, `20-…-full.png` | "Breakdown & method": how each number is computed |
| `21-thread-narrative-map.png`, `22-…-full.png` | The thread's narrative map (dots sized by upvotes, faded by author authenticity) |

## Dashboard (4,453 posts and comments, four subreddits)

| File | Shows |
|---|---|
| `23-dashboard.png` | Dashboard tab |
| `24-dashboard-map-overview.png` | Narrative map: topics list, headline numbers, map |
| `25-dashboard-tiles.png` | Headline numbers |
| `26-dashboard-topic-selected.png` | A topic (Trump Administration) selected |
| `27-dashboard-zoom-to-topic.png` | Zoomed to that topic, narratives labelled |
| `28-dashboard-word-clouds.png` | Word clouds and representative comments |
| `29-dashboard-subreddit-politics.png` | Filtered to r/politics |
| `30-dashboard-inauthentic-narratives.png` | Narratives ranked by engagement from inauthentic-looking accounts |
| `31-dashboard-timeline.png` | Narrative activity over time |
| `33-dashboard-with-noise.png` | Unclustered comments ("noise") shown |
| `34-dashboard-map-full.png` | Whole map page |
| `35-narrative-weather-frame1..4.png` | "Narrative weather" time-lapse, four moments |
| `36-narrative-weather-authenticity.png` | Time-lapse coloured by authenticity |
| `37-dashboard-narrative-weather-tab.png` | Time-lapse inside the Dashboard tab |

## Mobile (`mobile/`)

`m01-analyze`, `m02-demo`, `m03-result`, `m04-mini-dashboard`, `m05-dashboard`.

## Tutorial (`tour/`)

Every step of the built-in guided tour: `analyze-step01..07`, `demo-step01..05`, `result-step01..10`, `dashboard-step01..03`.

## Regenerating

```bash
node docs/screenshots/capture/capture.mjs docs/screenshots/            # everything
node docs/screenshots/capture/capture.mjs docs/screenshots/ "dash"     # only sections matching a regex: tour|analyze|demo|result|dash|mobile
```

Needs Node 22+ (built-in WebSocket) and `chromium` on the PATH; no npm packages. It drives headless Chromium over the DevTools protocol against the live site.
