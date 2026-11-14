"""Persistent storage for live analyses.

SQLite (data/candor.db) indexes every analysis and which visitor ran it; each run's
artifacts live in data/analyses/<id>/:

    post.json       scraped thread (base format)
    accounts.json   scraped commenter profiles (scorer input)
    stage2.json     narrative engine ProcessingResult
    scores.json     account scores (profile_authenticity_scorer.score_live)
    view.json       data for the Reddit mockup (web/view.html)
    map.html        interactive narrative map

Kept on disk so results can be re-used later (dashboard, re-scoring, research).
Analysis ids are random and unguessable; anyone with the link can view an analysis,
but a visitor's history list only shows what their own cookie ran.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CANDOR_DATA_DIR", ROOT / "data"))
DB_PATH = DATA / "candor.db"
RUNS = DATA / "analyses"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id            TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    post_id       TEXT NOT NULL,
    status        TEXT NOT NULL,          -- queued | running | done | error
    progress      REAL NOT NULL DEFAULT 0,
    message       TEXT,
    error         TEXT,
    title         TEXT,
    subreddit     TEXT,
    summary_json  TEXT,                   -- small JSON for history cards
    created_at    REAL NOT NULL,
    finished_at   REAL,
    client_ip     TEXT
);
CREATE INDEX IF NOT EXISTS analyses_post ON analyses(post_id, created_at);
CREATE TABLE IF NOT EXISTS visitor_analyses (
    visitor_id  TEXT NOT NULL,
    analysis_id TEXT NOT NULL REFERENCES analyses(id),
    created_at  REAL NOT NULL,
    PRIMARY KEY (visitor_id, analysis_id)
);
"""
_init_lock = threading.Lock()
_initialised = False


@contextmanager
def _db():
    global _initialised
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if not _initialised:
            with _init_lock:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(_SCHEMA)
                _initialised = True
        yield conn
        conn.commit()
    finally:
        conn.close()


def new_id() -> str:
    return secrets.token_hex(8)


def run_dir(analysis_id: str) -> Path:
    if not analysis_id.isalnum():  # ids are hex; never let one become a path
        raise ValueError("bad analysis id")
    d = RUNS / analysis_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def create(url: str, canonical_url: str, post_id: str, visitor_id: str, client_ip: str | None) -> str:
    aid = new_id()
    now = time.time()
    with _db() as c:
        c.execute("INSERT INTO analyses (id, url, canonical_url, post_id, status, message, created_at, client_ip) "
                  "VALUES (?, ?, ?, ?, 'queued', 'Queued', ?, ?)", (aid, url, canonical_url, post_id, now, client_ip))
        c.execute("INSERT INTO visitor_analyses VALUES (?, ?, ?)", (visitor_id, aid, now))
    return aid


def link_visitor(visitor_id: str, analysis_id: str) -> None:
    with _db() as c:
        c.execute("INSERT OR IGNORE INTO visitor_analyses VALUES (?, ?, ?)", (visitor_id, analysis_id, time.time()))


def update(analysis_id: str, **fields) -> None:
    allowed = {"status", "progress", "message", "error", "title", "subreddit", "summary_json", "finished_at"}
    if not fields or not set(fields) <= allowed:
        raise ValueError(f"bad fields {set(fields) - allowed}")
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _db() as c:
        c.execute(f"UPDATE analyses SET {cols} WHERE id = ?", (*fields.values(), analysis_id))


def get(analysis_id: str) -> dict | None:
    with _db() as c:
        row = c.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
    return _public(row) if row else None


def find_active(post_id: str) -> str | None:
    """An analysis of this post that is still queued/running (re-used instead of re-scraping)."""
    with _db() as c:
        row = c.execute("SELECT id FROM analyses WHERE post_id = ? AND status IN ('queued', 'running') "
                        "ORDER BY created_at DESC LIMIT 1", (post_id,)).fetchone()
    return row["id"] if row else None


def count_active_for_ip(client_ip: str) -> int:
    with _db() as c:
        return c.execute("SELECT COUNT(*) FROM analyses WHERE client_ip = ? AND status IN ('queued', 'running')",
                         (client_ip,)).fetchone()[0]


def history(visitor_id: str, limit: int = 50) -> list[dict]:
    with _db() as c:
        rows = c.execute("SELECT a.* FROM analyses a JOIN visitor_analyses v ON v.analysis_id = a.id "
                         "WHERE v.visitor_id = ? ORDER BY v.created_at DESC LIMIT ?", (visitor_id, limit)).fetchall()
    return [_public(r) for r in rows]


def mark_interrupted() -> int:
    """At startup: jobs that were running when the process died will never finish."""
    with _db() as c:
        cur = c.execute("UPDATE analyses SET status = 'error', error = ?, finished_at = ? "
                        "WHERE status IN ('queued', 'running')",
                        ("The server restarted during this analysis. Please run it again.", time.time()))
        return cur.rowcount


def write_json(analysis_id: str, name: str, obj) -> Path:
    p = run_dir(analysis_id) / name
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, p)
    return p


def read_file(analysis_id: str, name: str) -> Path | None:
    if not analysis_id.isalnum():
        return None
    p = RUNS / analysis_id / name
    return p if p.is_file() else None


def _public(row: sqlite3.Row) -> dict:
    d = dict(row)
    d.pop("client_ip", None)
    d["summary"] = json.loads(d.pop("summary_json") or "null")
    return d
