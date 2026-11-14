# Candor

Visualizing how online narratives emerge, grow, split and decay, to detect
Coordinated Inauthentic Behavior. See `AGENT.md` for architecture and dev conventions.

## Setup

```bash
python -m venv venv
venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu   # optional, CPU-only
venv/bin/pip install -r requirements.txt
cp .env.example .env   # GEMINI_API_KEY for embeddings, NVIDIA_API_KEY for LLM labels
```

## Stage 2 — spatial engine

```bash
venv/bin/python -m candor.pipeline reddit_posts.json --out-dir out/
```

Input is either the scraper's nested Reddit dump (posts with `comments` /
`replies` trees) or a flat JSON list of `{comment_id, author, text, ...}`.
Outputs in `--out-dir`:

| File | What |
|---|---|
| `stage2_result.json` | `ProcessingResult`: every post/comment with `narrative_id/label`, `topic_id/label` (`-1` / `"Noise"` if unclustered), plus per-narrative and per-topic summaries with c-TF-IDF keywords |
| `stage2_report.html` | Interactive report — 2D narrative map, topic list, word clouds. Open in a browser (loads Plotly from cdnjs). Use `--report-name <file>.html` to keep earlier reports. |
| `embeddings_<backend>.npz` | Embedding cache per backend; re-runs with the same texts skip the embedding step |

**Embeddings** default to Google `gemini-embedding-2` (768 dims, clustering task
prefix, batched 100 texts/request; ~3 min for 4.5k texts). `--embedder local`
uses MiniLM offline. On a labelled sample, Gemini separated a copy-paste
astroturf narrative from the genuine opinion it imitates (ARI 0.84 vs 0.36 for
MiniLM), which MiniLM could not. Caches are per backend
(`embeddings_gemini.npz`, `embeddings_local.npz`).

Without `NVIDIA_API_KEY`, labels are the top c-TF-IDF keywords (as in BERTopic).
Extra input fields (`created_utc`, `post_id`, `parent_id`, `subreddit`) pass
through to the result for Stage 3.

Python:

```python
from candor.ingest import load_comments
from candor.spatial_engine import SpatialEngine, SpatialEngineConfig

engine = SpatialEngine(SpatialEngineConfig(topic_distance_threshold=0.55))
result = engine.process(load_comments("reddit_posts.json"))
```

Synthetic demo data: `venv/bin/python examples/make_sample_data.py`, then run the
pipeline on `examples/sample_comments.json` with `--min-cluster-size 3`.

### Tuning knobs (`SpatialEngineConfig` / CLI flags)

| Param | Default | Effect |
|---|---|---|
| `hdbscan_min_cluster_size` (`--min-cluster-size`) | 10 | Smallest narrative. ~10 for a few thousand comments; 20–50 for 10k+. |
| `hdbscan_selection_method` (`--selection`) | `leaf` | `leaf` = many fine narratives; `eom` = fewer, broader ones (less noise). |
| `topic_method` (`--topic-method`) | `agglomerative` | How narrative centroids merge into topics. `dbscan` is the original spec; it chain-merges unrelated narratives on real data. |
| `embedding_backend` (`--embedder`, env `EMBEDDING_BACKEND`) | `gemini` | `gemini` or `local`. |
| `topic_distance_threshold` (`--topic-threshold`) | per backend: gemini 0.06, local 0.6 | Agglomerative: max average cosine distance within a topic. Lower = more, tighter topics. Gemini similarities are compressed (~10x smaller distances), so values don't transfer between backends. |
| `topic_eps` (`--topic-eps`) | 0.5 | DBSCAN only. |
| `umap_n_neighbors` / `umap_min_dist` | 15 / 0.0 | Local vs. global structure of the clustering space. |
| `random_state` | 42 | Reproducible runs; `None` for parallel (faster) UMAP. |

## Tests

```bash
venv/bin/python -m pytest
```
Tests use a fake embedder and labeler, so they need no network or API key.
