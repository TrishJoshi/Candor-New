# AGENT.md — conventions for agents working in this repo

## Project

Candor visualizes how online narratives emerge, grow, split and decay across
platforms like Reddit, to detect Coordinated Inauthentic Behavior (CIB).

Pipeline stages:
1. **Scraping** — posts/comments with timestamp + account (context.dev).
2. **Spatial engine** — embed text, cluster into *narratives* (HDBSCAN), cluster
   narrative centroids into *topics* (agglomerative; DBSCAN optional), label both
   with an LLM.
   Entry point `python -m candor.pipeline <input.json> --out-dir out/`.
   Modules: `ingest.py` (scraper dump -> `Comment`s), `embedders.py` (Gemini
   default, local MiniLM fallback), `spatial_engine.py`
   (clustering), `representation.py` (c-TF-IDF keywords), `labeling.py` (LLM
   labels), `visualize.py` (HTML report). Topic tier defaults to agglomerative
   (average linkage) rather than DBSCAN — DBSCAN chain-merged unrelated
   narratives on real data.
3. **Account authenticity** — score accounts (age, privacy, topic diversity,
   activity bursts, cross-account coordination).
4. **Feedback loop** — narrative authenticity from account scores, and back.

## Development standards

- **Dependencies:** `requirements.txt` is the single source of truth. Any new
  third-party import must be added there (with a minimum version and a short
  comment saying what it is for) in the same change. Never `pip install` a
  package ad hoc without recording it.
- **Environment:** use the project venv at `./venv`.
  ```bash
  python -m venv venv
  venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu  # optional, CPU-only
  venv/bin/pip install -r requirements.txt
  ```
- **Secrets:** API keys go in `.env` (git-ignored); `.env.example` documents the
  required variables. Never commit keys or hardcode them.
- **Code layout:** library code in `candor/`, tests in `tests/`, runnable
  examples/data in `examples/`.
- **Data contracts:** inter-stage data uses the Pydantic models in
  `candor/schemas.py`. Change them deliberately — downstream stages depend on them.
- **Typing & style:** type hints on public functions, docstrings on modules and
  public APIs, `logging` (not `print`) inside library code.
- **Tests:** add/update `pytest` tests with every behavior change; run
  `venv/bin/python -m pytest` before committing. Tests must not require network
  access or API keys (inject fakes).
- **Generated output:** pipeline runs write to `out/` (git-ignored). Never commit
  generated outputs.
- **Config:** tunable parameters (clustering thresholds, model names) are
  exposed via config objects / env vars, not buried as literals.
