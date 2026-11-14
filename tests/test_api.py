"""API + storage tests with a fake pipeline (no network)."""

from __future__ import annotations

import sys
import time
import types

import pytest
from fastapi.testclient import TestClient

from candor import store
from candor.urls import normalize_reddit_url


@pytest.mark.parametrize("url, expected", [
    ("https://www.reddit.com/r/politics/comments/1xvsfrn/some_title/", ("https://www.reddit.com/r/politics/comments/1xvsfrn/", "t3_1xvsfrn")),
    ("https://old.reddit.com/r/AskReddit/comments/1abcd2/x/kz9/?context=3#c", ("https://www.reddit.com/r/AskReddit/comments/1abcd2/", "t3_1abcd2")),
    ("https://reddit.com/comments/1abcd2", ("https://www.reddit.com/comments/1abcd2/", "t3_1abcd2")),
    ("https://redd.it/1abcd2", ("https://www.reddit.com/comments/1abcd2/", "t3_1abcd2")),
    ("https://www.reddit.com/r/politics/", None),
    ("https://evil.com/r/x/comments/1abcd2/", None),
    ("https://notreddit.com.evil.io/r/x/comments/1abcd2/", None),
    ("javascript:alert(1)", None),
    ("https://www.reddit.com/r/x/comments/../../etc/", None),
])
def test_normalize(url, expected):
    assert normalize_reddit_url(url) == expected


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "candor.db")
    monkeypatch.setattr(store, "RUNS", tmp_path / "analyses")
    monkeypatch.setattr(store, "_initialised", False)

    fake = types.ModuleType("candor.live")

    class AnalysisError(Exception):
        pass

    def analyze(aid, url, progress):
        progress(0.5, "halfway")
        if "1bad00" in url:
            raise AnalysisError("We couldn't load this Reddit post")
        if "1slow0" in url:
            time.sleep(0.5)
        store.write_json(aid, "view.json", {"meta": {"mode": "live"}, "threads": [], "profiles": {}})
        (store.run_dir(aid) / "map.html").write_text("<html>map</html>")
        return {"n_comments": 3, "mean_score": 77.0}

    fake.AnalysisError, fake.analyze, fake.warmup = AnalysisError, analyze, lambda: None
    monkeypatch.setitem(sys.modules, "candor.live", fake)
    from candor import api
    with TestClient(api.app) as c:
        yield c


def _wait(client, aid, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        a = client.get(f"/api/analyses/{aid}").json()
        if a["status"] in ("done", "error"):
            return a
        time.sleep(0.05)
    raise AssertionError("analysis did not finish")


def test_analyze_flow_cookie_and_history(client):
    r = client.post("/api/analyze", json={"url": "https://www.reddit.com/r/x/comments/1abcd2/t/"})
    assert r.status_code == 200 and "candor_vid" in r.cookies
    aid = r.json()["id"]
    a = _wait(client, aid)
    assert a["status"] == "done" and a["summary"]["mean_score"] == 77.0 and "client_ip" not in a
    assert client.get(f"/api/analyses/{aid}/view").json()["meta"]["mode"] == "live"
    assert "map" in client.get(f"/api/analyses/{aid}/map").text
    hist = client.get("/api/history").json()["analyses"]
    assert [h["id"] for h in hist] == [aid]
    # a different visitor (new cookie jar) doesn't see it
    client.cookies.clear()
    assert client.get("/api/history").json()["analyses"] == []


def test_user_facing_error(client):
    aid = client.post("/api/analyze", json={"url": "https://www.reddit.com/r/x/comments/1bad00/"}).json()["id"]
    a = _wait(client, aid)
    assert a["status"] == "error" and "couldn't load" in a["error"]
    assert client.get(f"/api/analyses/{aid}/view").status_code == 404


def test_rejects_non_reddit_and_unknown_ids(client):
    assert client.post("/api/analyze", json={"url": "https://example.com/"}).status_code == 400
    assert client.get("/api/analyses/doesnotexist").status_code == 404
    assert client.get("/api/analyses/..%2F..%2Fetc/view").status_code == 404


def test_same_post_running_is_joined(client):
    url = "https://www.reddit.com/r/x/comments/1slow0/"
    a = client.post("/api/analyze", json={"url": url}).json()
    b = client.post("/api/analyze", json={"url": url}).json()
    assert b["id"] == a["id"] and b["joined"]
    _wait(client, a["id"])


def test_restart_marks_running_jobs_failed(client):
    aid = store.create("u", "u", "t3_zzzz1", "v", "ip")
    store.update(aid, status="running")
    assert store.mark_interrupted() == 1
    assert store.get(aid)["status"] == "error"


def test_distribution_top3_plus_others():
    from candor.view import distribution
    ns = [{"id": -1, "label": "noise", "weighted_share": 0.2}] + [
        {"id": i, "label": f"n{i}", "weighted_share": s} for i, s in enumerate([0.1, 0.4, 0.05, 0.25])]
    d = distribution(ns)
    assert [x["id"] for x in d] == [1, 3, 0, None]
    assert d[-1]["label"] == "Others" and abs(d[-1]["share"] - 0.25) < 1e-9  # n2 + noise


def test_history_backfills_distribution(client):
    r = client.post("/api/analyze", json={"url": "https://www.reddit.com/r/x/comments/1abc11/t/"})
    aid = r.json()["id"]
    _wait(client, aid)
    store.write_json(aid, "view.json", {"narratives": [{"id": 0, "label": "a", "weighted_share": 1.0}]})
    h = client.get("/api/history").json()["analyses"]
    assert h[0]["summary"]["narratives"] == [{"id": 0, "label": "a", "share": 1.0}]


def test_recent_result_is_reused(client):
    url = "https://www.reddit.com/r/x/comments/1abc22/t/"
    first = client.post("/api/analyze", json={"url": url}).json()["id"]
    _wait(client, first)
    again = client.post("/api/analyze", json={"url": url}).json()
    assert again["id"] == first and again.get("reused")


def test_dashboard_map_prefers_server_build(client):
    assert client.get("/api/dashboard/map").status_code == 200  # committed fallback in web/
    (store.DATA / "dashboard").mkdir(parents=True, exist_ok=True)
    (store.DATA / "dashboard" / "narrative-map.html").write_text("<html>server build</html>")
    assert "server build" in client.get("/api/dashboard/map").text
