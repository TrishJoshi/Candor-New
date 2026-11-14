"""Offline tests for the Gemini embedder (HTTP mocked with httpx.MockTransport)."""

from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

from candor.embedders import GEMINI_CLUSTERING_PREFIX, GeminiAPIError, GeminiEmbedder


def _embedder(handler, **kw) -> GeminiEmbedder:
    return GeminiEmbedder(
        api_key="test-key", dimensions=4, retry_wait_min=0,
        transport=httpx.MockTransport(handler), **kw,
    )


def _ok(request: httpx.Request) -> httpx.Response:
    reqs = json.loads(request.content)["requests"]
    return httpx.Response(
        200, json={"embeddings": [{"values": [float(len(r["content"]["parts"][0]["text"])), 1, 0, 0]} for r in reqs]}
    )


def test_batches_prefix_and_normalisation():
    seen: list[dict] = []

    def handler(request):
        assert request.headers["x-goog-api-key"] == "test-key"
        assert "key=" not in str(request.url)  # key never in the URL
        assert str(request.url).endswith("/models/gemini-embedding-2:batchEmbedContents")
        seen.append(json.loads(request.content))
        return _ok(request)

    emb = _embedder(handler)([f"text {i}" for i in range(230)] + ["   "])
    assert emb.shape == (231, 4)
    assert np.allclose(np.linalg.norm(emb, axis=1), 1)
    assert [len(b["requests"]) for b in seen] == [100, 100, 31]
    first = seen[0]["requests"][0]
    assert first["content"]["parts"][0]["text"] == GEMINI_CLUSTERING_PREFIX + "text 0"
    assert first["outputDimensionality"] == 4
    assert seen[-1]["requests"][-1]["content"]["parts"][0]["text"].endswith("[empty]")


def test_retries_rate_limit_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": {"message": "quota", "details": [{"retryDelay": "0s"}]}})
        return _ok(request)

    assert _embedder(handler)(["a", "b"]).shape == (2, 4)
    assert calls["n"] == 3


def test_client_error_is_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    with pytest.raises(GeminiAPIError, match="400: bad request"):
        _embedder(handler)(["a"])
    assert calls["n"] == 1


def test_from_env_requires_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        GeminiEmbedder.from_env()


def test_cache_id_changes_with_settings():
    a = _embedder(_ok).cache_id
    b = GeminiEmbedder(api_key="k", dimensions=8, transport=httpx.MockTransport(_ok)).cache_id
    assert a != b and "gemini-embedding-2" in a
