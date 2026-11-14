"""Stage 2 — Spatial engine.

Embeds comments, clusters them into fine-grained *narratives* (UMAP + HDBSCAN),
clusters the narrative centroids into macro *topics* (DBSCAN, cosine), describes
every cluster with c-TF-IDF keywords, labels it with an LLM (or the keywords
when no LLM is configured), and returns the annotated comments.

Run it from the command line via :mod:`candor.pipeline`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import hdbscan
import numpy as np
from sklearn.cluster import DBSCAN, AgglomerativeClustering

from candor.embedders import make_embedder
from candor.labeling import Labeler, default_labeler
from candor.representation import Keywords, class_tfidf, keywords_label
from candor.schemas import (
    NOISE_ID,
    NOISE_LABEL,
    Comment,
    Keyword,
    NarrativeSummary,
    ProcessingResult,
    TopicSummary,
)

logger = logging.getLogger(__name__)

Embedder = Callable[[Sequence[str]], np.ndarray]

# Reddit placeholders that carry no semantic content.
_EMPTY_TEXTS = {"", "[deleted]", "[removed]"}
_DEFAULT: Any = object()  # sentinel: "use default_labeler()"


# Tuned topic thresholds per embedding backend, from sweeps on reddit_posts.json.
# Gemini similarities are compressed (random pairs ~0.73 vs ~0.06 for MiniLM), so
# its centroid distances are ~10x smaller. A relative (quantile) threshold was
# tried and did not transfer between the two models.
TOPIC_THRESHOLDS = {"local": 0.6, "gemini": 0.06}


@dataclass
class SpatialEngineConfig:
    # 'gemini' (gemini-embedding-2, needs GEMINI_API_KEY) or 'local' (MiniLM, offline)
    embedding_backend: str = "gemini"
    local_embedding_model: str = "all-MiniLM-L6-v2"
    embedding_batch_size: int = 64  # local backend only
    # UMAP
    umap_n_neighbors: int = 15
    umap_n_components: int = 5
    umap_min_dist: float = 0.0  # 0.0 packs clusters tightly (recommended for clustering)
    random_state: int | None = 42  # None = faster (parallel) but non-deterministic
    # Tier 1: narratives
    hdbscan_min_cluster_size: int = 10  # ~10 suits a few thousand comments; raise for 10k+
    hdbscan_min_samples: int | None = None  # None = same as min_cluster_size
    # 'leaf' favours many fine-grained clusters (narratives); 'eom' favours few large ones.
    hdbscan_selection_method: str = "leaf"
    # Tier 2: topics, clustered on narrative centroids (cosine distance).
    # 'agglomerative' (average linkage) avoids DBSCAN's single-linkage chaining,
    # which on real data merges unrelated narratives into one mega-topic.
    topic_method: str = "agglomerative"  # or "dbscan"
    # Agglomerative: max average cosine distance to merge. None = the backend's
    # tuned default (TOPIC_THRESHOLDS) — embedding models put cosine distances
    # on very different scales, so one value does not transfer between them.
    topic_distance_threshold: float | None = None
    topic_eps: float = 0.5  # dbscan only
    topic_min_samples: int = 2  # dbscan only
    # Labeling / representation
    representatives_per_narrative: int = 5
    keywords_per_cluster: int = 30

    def resolved_topic_threshold(self) -> float:
        if self.topic_distance_threshold is not None:
            return self.topic_distance_threshold
        return TOPIC_THRESHOLDS[self.embedding_backend]


class SpatialEngine:
    def __init__(
        self,
        config: SpatialEngineConfig | None = None,
        embedder: Embedder | None = None,
        labeler: Labeler | None = _DEFAULT,
    ) -> None:
        """``labeler=None`` forces offline keyword labels; omit it to use the env default."""
        self.config = config or SpatialEngineConfig()
        self.embedder = embedder or make_embedder(
            self.config.embedding_backend,
            self.config.local_embedding_model,
            self.config.embedding_batch_size,
        )
        self.labeler = default_labeler() if labeler is _DEFAULT else labeler

    # ------------------------------------------------------------------ public

    def embed(self, comments: Sequence[Comment]) -> np.ndarray:
        """L2-normalised embeddings for every comment (one row per comment)."""
        texts = [c.text for c in comments]
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        return _l2_normalize(np.asarray(self.embedder(texts), dtype=np.float32))

    def process(
        self, comments: Sequence[Comment], embeddings: np.ndarray | None = None
    ) -> ProcessingResult:
        """Cluster and label ``comments``.

        ``embeddings`` (one row per comment, from :meth:`embed`) may be passed to
        reuse vectors already computed, e.g. for visualization or tuning runs.
        """
        comments = [c.model_copy() for c in comments]
        valid_idx = [i for i, c in enumerate(comments) if c.text.strip() not in _EMPTY_TEXTS]
        narrative_of = np.full(len(comments), NOISE_ID, dtype=int)

        if not valid_idx:
            logger.warning("No non-empty comments to cluster")
            return _assemble(comments, narrative_of, {}, {}, {}, {}, {}, {})

        # 2.1 Vectorize + reduce
        if embeddings is None:
            emb = self.embed([comments[i] for i in valid_idx])
        else:
            if len(embeddings) != len(comments):
                raise ValueError(f"{len(embeddings)} embeddings for {len(comments)} comments")
            emb = _l2_normalize(np.asarray(embeddings, dtype=np.float32)[valid_idx])
        reduced = self._reduce(emb)

        # 2.2 Narratives
        labels = self._cluster_narratives(reduced)
        narrative_of[valid_idx] = labels
        centroids = {
            int(n): _l2_normalize(emb[labels == n].mean(axis=0))
            for n in np.unique(labels)
            if n != NOISE_ID
        }
        logger.info(
            "%d narratives from %d comments (%d noise, %.0f%%)",
            len(centroids), len(valid_idx), int((labels == NOISE_ID).sum()),
            100 * (labels == NOISE_ID).mean(),
        )

        # 2.3 Topics
        topic_of = self._cluster_topics(centroids)

        # 2.4 Keywords (c-TF-IDF) and labels
        texts_by_narrative: dict[int, list[str]] = {}
        for i, n in zip(valid_idx, labels):
            texts_by_narrative.setdefault(int(n), []).append(comments[i].text)
        texts_by_topic: dict[int, list[str]] = {}
        for n, docs in texts_by_narrative.items():
            texts_by_topic.setdefault(topic_of.get(n, NOISE_ID), []).extend(docs)

        k = self.config.keywords_per_cluster
        narrative_kw = class_tfidf(texts_by_narrative, top_n=k, exclude_classes=[NOISE_ID])
        topic_kw = class_tfidf(texts_by_topic, top_n=k, exclude_classes=[NOISE_ID])

        representatives = self._representatives(emb, labels, centroids, valid_idx)
        narrative_labels = {
            n: self._label(
                lambda: self.labeler.label_narrative([comments[i].text for i in reps]),
                narrative_kw.get(n, []),
            )
            for n, reps in representatives.items()
        }
        topic_labels = {
            t: self._label(
                lambda: self.labeler.label_topic(
                    [narrative_labels[n] for n in sorted(topic_of) if topic_of[n] == t]
                ),
                topic_kw.get(t, []),
            )
            for t in sorted(set(topic_of.values()))
        }

        # 2.5 Reassemble
        return _assemble(
            comments, narrative_of, topic_of, narrative_labels, topic_labels,
            representatives, narrative_kw, topic_kw,
        )

    # ----------------------------------------------------------------- stages

    def _label(self, ask_llm: Callable[[], str | None], keywords: Keywords) -> str:
        label = ask_llm() if self.labeler is not None else None
        return label or keywords_label(keywords)

    def _reduce(self, embeddings: np.ndarray) -> np.ndarray:
        cfg = self.config
        n = len(embeddings)
        # UMAP's spectral init needs n > n_components + 1; tiny inputs skip it.
        if n <= cfg.umap_n_components + 2:
            logger.info("Only %d comments; skipping UMAP", n)
            return embeddings

        import umap  # deferred: numba compilation makes this import slow

        reducer = umap.UMAP(
            n_neighbors=min(cfg.umap_n_neighbors, n - 1),
            n_components=cfg.umap_n_components,
            min_dist=cfg.umap_min_dist,
            metric="cosine",
            random_state=cfg.random_state,
            n_jobs=1 if cfg.random_state is not None else -1,  # a seed forces single-thread anyway
            compatibility_layout=True,  # keep the classic UMAP layout; 0.6 warns otherwise
        )
        return reducer.fit_transform(embeddings)

    def _cluster_narratives(self, reduced: np.ndarray) -> np.ndarray:
        cfg = self.config
        if len(reduced) < cfg.hdbscan_min_cluster_size:
            return np.full(len(reduced), NOISE_ID, dtype=int)
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=cfg.hdbscan_min_cluster_size,
            min_samples=cfg.hdbscan_min_samples,
            metric="euclidean",
            cluster_selection_method=cfg.hdbscan_selection_method,
        )
        return clusterer.fit_predict(reduced).astype(int)

    def _cluster_topics(self, centroids: dict[int, np.ndarray]) -> dict[int, int]:
        """Map narrative_id -> topic_id. Unmerged narratives become singleton topics."""
        if not centroids:
            return {}
        cfg = self.config
        ids = sorted(centroids)
        matrix = np.stack([centroids[n] for n in ids])
        if len(ids) == 1:
            raw = np.array([0])
        elif cfg.topic_method == "agglomerative":
            raw = AgglomerativeClustering(
                n_clusters=None,
                distance_threshold=cfg.resolved_topic_threshold(),
                metric="cosine",
                linkage="average",
            ).fit_predict(matrix)
        elif cfg.topic_method == "dbscan":
            raw = DBSCAN(
                eps=cfg.topic_eps, min_samples=cfg.topic_min_samples, metric="cosine"
            ).fit_predict(matrix)
        else:
            raise ValueError(f"Unknown topic_method {cfg.topic_method!r}")

        next_id = int(raw.max()) + 1 if (raw >= 0).any() else 0
        topic_of: dict[int, int] = {}
        for n, t in zip(ids, raw):
            if t == NOISE_ID:
                t, next_id = next_id, next_id + 1
            topic_of[n] = int(t)
        logger.info("%d topics from %d narratives", len(set(topic_of.values())), len(ids))
        return topic_of

    def _representatives(
        self,
        embeddings: np.ndarray,
        labels: np.ndarray,
        centroids: dict[int, np.ndarray],
        valid_idx: list[int],
    ) -> dict[int, list[int]]:
        """Indices (into the full comment list) of the comments closest to each centroid.

        Near-duplicate texts are skipped so copy-pasted astroturf doesn't fill
        every slot with the same sentence.
        """
        k = self.config.representatives_per_narrative
        reps: dict[int, list[int]] = {}
        for n, centroid in centroids.items():
            members = np.flatnonzero(labels == n)
            sims = embeddings[members] @ centroid
            chosen: list[int] = []
            seen: set[bytes] = set()
            for m in members[np.argsort(-sims)]:
                key = np.round(embeddings[m], 2).tobytes()  # near-identical texts collide
                if key in seen:
                    continue
                seen.add(key)
                chosen.append(valid_idx[m])
                if len(chosen) == k:
                    break
            reps[n] = chosen
        return reps


# ---------------------------------------------------------------------- helpers


def _assemble(
    comments: list[Comment],
    narrative_of: np.ndarray,
    topic_of: dict[int, int],
    narrative_labels: dict[int, str],
    topic_labels: dict[int, str],
    representatives: dict[int, list[int]],
    narrative_kw: dict[int, Keywords],
    topic_kw: dict[int, Keywords],
) -> ProcessingResult:
    for c, n in zip(comments, narrative_of):
        n = int(n)
        if n == NOISE_ID:
            c.narrative_id, c.narrative_label = NOISE_ID, NOISE_LABEL
            c.topic_id, c.topic_label = NOISE_ID, NOISE_LABEL
        else:
            t = topic_of[n]
            c.narrative_id, c.narrative_label = n, narrative_labels[n]
            c.topic_id, c.topic_label = t, topic_labels[t]

    def kw(pairs: Keywords) -> list[Keyword]:
        return [Keyword(word=w, weight=round(x, 6)) for w, x in pairs]

    sizes = {n: int((narrative_of == n).sum()) for n in topic_of}
    narratives = [
        NarrativeSummary(
            narrative_id=n,
            label=narrative_labels[n],
            topic_id=topic_of[n],
            size=sizes[n],
            representative_comment_ids=[comments[i].comment_id for i in representatives[n]],
            keywords=kw(narrative_kw.get(n, [])),
        )
        for n in sorted(topic_of)
    ]
    topics = [
        TopicSummary(
            topic_id=t,
            label=topic_labels[t],
            narrative_ids=[n for n in sorted(topic_of) if topic_of[n] == t],
            size=sum(sizes[n] for n in topic_of if topic_of[n] == t),
            keywords=kw(topic_kw.get(t, [])),
        )
        for t in sorted(topic_labels)
    ]
    return ProcessingResult(comments=comments, narratives=narratives, topics=topics)


def _l2_normalize(x: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.where(norm == 0, 1, norm)
