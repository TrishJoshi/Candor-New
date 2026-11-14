"""Text embedding backends.

* :class:`GeminiEmbedder` — Google ``gemini-embedding-2`` via the REST
  ``batchEmbedContents`` endpoint (default backend).
* :class:`SentenceTransformerEmbedder` — local ``all-MiniLM-L6-v2``; offline
  fallback, no API key needed.

Both are callables ``texts -> np.ndarray`` (one L2-normalised row per text) and
expose ``cache_id``, a string that changes whenever their output would.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Sequence

import httpx
import numpy as np
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-embedding-2"
# gemini-embedding-2 ignores the task_type field; the task goes in the text.
GEMINI_CLUSTERING_PREFIX = "task: clustering | query: "
GEMINI_MAX_BATCH = 100  # API rejects batchEmbedContents with more than 100 requests
_MAX_CHARS = 20_000  # well under the 8,192-token input limit


def _normalize(x: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.where(norm == 0, 1, norm)


class SentenceTransformerEmbedder:
    """Local sentence-transformers model. Loads lazily on first call."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", batch_size: int = 64) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self._model = None

    @property
    def cache_id(self) -> str:
        return f"local:{self.model_name}"

    def __call__(self, texts: Sequence[str]) -> np.ndarray:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model.encode(
            list(texts),
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=len(texts) > 1000,
            convert_to_numpy=True,
        )


class GeminiAPIError(RuntimeError):
    def __init__(self, status: int, message: str, retry_after: float | None = None) -> None:
        super().__init__(f"Gemini API {status}: {message}")
        self.status = status
        self.retry_after = retry_after


class _Retryable(GeminiAPIError):
    """429 / 5xx / transport errors — worth retrying."""


def _wait_for(base_wait):
    """Honour the server's retry delay on 429s, else fall back to ``base_wait``."""

    def wait(state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome else None
        if isinstance(exc, GeminiAPIError) and exc.retry_after is not None:
            return exc.retry_after
        return base_wait(state)

    return wait


class GeminiEmbedder:
    """Gemini embeddings, batched 100 texts per request, with retries.

    The API key is sent in the ``x-goog-api-key`` header (never in the URL), so
    it does not appear in logs or error messages.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        dimensions: int = 768,
        task_prefix: str = GEMINI_CLUSTERING_PREFIX,
        batch_size: int = GEMINI_MAX_BATCH,
        max_retries: int = 8,
        retry_wait_min: float = 2.0,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,  # tests inject a mock
    ) -> None:
        if not 1 <= batch_size <= GEMINI_MAX_BATCH:
            raise ValueError(f"batch_size must be 1..{GEMINI_MAX_BATCH}")
        self.model = model
        self.dimensions = dimensions
        self.task_prefix = task_prefix
        self.batch_size = batch_size
        self._client = httpx.Client(
            base_url=GEMINI_API_BASE,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
            transport=transport,
        )
        self._embed_batch_with_retry = retry(
            retry=retry_if_exception_type(_Retryable),
            wait=_wait_for(wait_exponential(multiplier=2, min=retry_wait_min, max=90)),
            stop=stop_after_attempt(max_retries),
            before_sleep=lambda s: logger.warning(
                "Gemini embedding retry %d after: %s", s.attempt_number, s.outcome.exception()
            ),
            reraise=True,
        )(self._embed_batch)

    @classmethod
    def from_env(cls, **kwargs) -> "GeminiEmbedder":
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Add it to .env, or use the local embedder "
                "(--embedder local)."
            )
        kwargs.setdefault("model", os.getenv("GEMINI_EMBEDDING_MODEL", DEFAULT_GEMINI_MODEL))
        kwargs.setdefault("dimensions", int(os.getenv("GEMINI_EMBEDDING_DIMS", "768")))
        return cls(api_key=api_key, **kwargs)

    @property
    def cache_id(self) -> str:
        return f"gemini:{self.model}:{self.dimensions}:{self.task_prefix}"

    def _prepare(self, text: str) -> str:
        text = text.strip() or "[empty]"  # the API rejects empty parts
        return self.task_prefix + text[:_MAX_CHARS]

    def _embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        body = {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": self._prepare(t)}]},
                    "outputDimensionality": self.dimensions,
                }
                for t in texts
            ]
        }
        try:
            resp = self._client.post(f"/models/{self.model}:batchEmbedContents", json=body)
        except httpx.TransportError as exc:
            raise _Retryable(0, f"transport error: {exc}") from exc

        if resp.status_code == 200:
            embeddings = resp.json()["embeddings"]
            if len(embeddings) != len(texts):
                raise GeminiAPIError(200, f"expected {len(texts)} embeddings, got {len(embeddings)}")
            return [e["values"] for e in embeddings]

        message, retry_after = _parse_error(resp)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise _Retryable(resp.status_code, message, retry_after)
        raise GeminiAPIError(resp.status_code, message)

    def __call__(self, texts: Sequence[str]) -> np.ndarray:
        texts = list(texts)
        out: list[list[float]] = []
        n_batches = -(-len(texts) // self.batch_size)
        start = time.monotonic()
        for b, i in enumerate(range(0, len(texts), self.batch_size), 1):
            out.extend(self._embed_batch_with_retry(texts[i : i + self.batch_size]))
            if b % 10 == 0 or b == n_batches:
                logger.info(
                    "Gemini embeddings: %d/%d texts (%.0fs)",
                    len(out), len(texts), time.monotonic() - start,
                )
        if not out:
            return np.zeros((0, self.dimensions), dtype=np.float32)
        # Truncated (<3072-dim) Gemini vectors are not guaranteed unit-length.
        return _normalize(np.asarray(out, dtype=np.float32))


def _parse_error(resp: httpx.Response) -> tuple[str, float | None]:
    """Error message and server-suggested retry delay (seconds), if any."""
    retry_after = None
    try:
        err = resp.json().get("error", {})
        message = err.get("message", resp.text)[:500]
        for detail in err.get("details", []):
            delay = detail.get("retryDelay")  # e.g. "31s" in google.rpc.RetryInfo
            if delay and (m := re.fullmatch(r"([\d.]+)s", delay)):
                retry_after = float(m.group(1))
    except ValueError:
        message = resp.text[:500]
    if retry_after is None and (header := resp.headers.get("retry-after")):
        try:
            retry_after = float(header)
        except ValueError:
            pass
    return message, retry_after


def make_embedder(backend: str, local_model: str = "all-MiniLM-L6-v2", batch_size: int = 64):
    """Embedder for ``backend`` ('gemini' or 'local')."""
    if backend == "gemini":
        return GeminiEmbedder.from_env()
    if backend == "local":
        return SentenceTransformerEmbedder(local_model, batch_size)
    raise ValueError(f"Unknown embedding backend {backend!r} (expected 'gemini' or 'local')")
