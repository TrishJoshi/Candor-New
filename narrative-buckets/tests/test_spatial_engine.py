"""Offline tests for the spatial engine (fake embedder + labeler, no network)."""

from __future__ import annotations

import numpy as np
import pytest

from candor.labeling import clean_label
from candor.schemas import NOISE_ID, NOISE_LABEL, Comment
from candor.spatial_engine import SpatialEngine, SpatialEngineConfig

DIM = 32


def _basis(i: int) -> np.ndarray:
    v = np.zeros(DIM)
    v[i] = 1.0
    return v


# Two topics: narratives A1/A2 share a "topic" direction (0), B1 is elsewhere.
CENTERS = {
    "A1": _basis(0) + 0.6 * _basis(1),
    "A2": _basis(0) + 0.6 * _basis(2),
    "B1": _basis(10) + 0.6 * _basis(11),
}


class FakeEmbedder:
    """Text format '<group>:<n>' -> noisy vector around that group's center."""

    def __init__(self, seed: int = 0) -> None:
        self.rng = np.random.default_rng(seed)

    def __call__(self, texts):
        out = []
        for t in texts:
            group = t.split(":")[0]
            center = CENTERS.get(group)
            if center is None:  # noise: random direction
                center = self.rng.normal(size=DIM) * 3
            out.append(center + self.rng.normal(scale=0.03, size=DIM))
        return np.array(out)


class FakeLabeler:
    def __init__(self) -> None:
        self.narrative_calls: list[list[str]] = []
        self.topic_calls: list[list[str]] = []

    def label_narrative(self, texts):
        self.narrative_calls.append(list(texts))
        return f"narr-{texts[0].split(':')[0]}"

    def label_topic(self, labels):
        self.topic_calls.append(list(labels))
        return "topic-" + "+".join(sorted(labels))


def _comments(spec: dict[str, int]) -> list[Comment]:
    out = []
    for group, n in spec.items():
        for i in range(n):
            out.append(Comment(comment_id=f"{group}-{i}", author=f"u{i}", text=f"{group}:{i}"))
    return out


def _engine(labeler=None, **cfg) -> SpatialEngine:
    # Fake vectors: A1/A2 centroids are ~0.27 apart, B1 ~1.0 away.
    cfg.setdefault("topic_distance_threshold", 0.5)
    return SpatialEngine(
        SpatialEngineConfig(**cfg), embedder=FakeEmbedder(), labeler=labeler or FakeLabeler()
    )


def test_narratives_and_topics_are_recovered():
    labeler = FakeLabeler()
    comments = _comments({"A1": 15, "A2": 15, "B1": 15})
    result = _engine(labeler).process(comments)

    by_group: dict[str, set[int]] = {}
    for c in result.comments:
        by_group.setdefault(c.text.split(":")[0], set()).add(c.narrative_id)

    # Each group is one distinct, non-noise narrative.
    assert all(len(ids) == 1 and NOISE_ID not in ids for ids in by_group.values())
    assert len({next(iter(ids)) for ids in by_group.values()}) == 3

    topic = {c.text.split(":")[0]: c.topic_id for c in result.comments}
    assert topic["A1"] == topic["A2"] != topic["B1"]

    assert len(result.narratives) == 3
    assert len(result.topics) == 2
    assert all(len(call) <= 5 for call in labeler.narrative_calls)
    assert sum(t.size for t in result.topics) == 45


@pytest.mark.parametrize("method", ["agglomerative", "dbscan"])
def test_singleton_narrative_gets_its_own_topic(method):
    result = _engine(topic_method=method, topic_eps=0.3).process(_comments({"A1": 15, "B1": 15}))
    topic_ids = {c.topic_id for c in result.comments}
    assert len(topic_ids) == 2 and NOISE_ID not in topic_ids


def test_dbscan_topic_method_merges_close_narratives():
    result = _engine(topic_method="dbscan", topic_eps=0.3).process(
        _comments({"A1": 15, "A2": 15, "B1": 15})
    )
    topic = {c.text.split(":")[0]: c.topic_id for c in result.comments}
    assert topic["A1"] == topic["A2"] != topic["B1"]


def test_precomputed_embeddings_match_internal_embedding():
    comments = _comments({"A1": 15, "B1": 15})
    engine = _engine()
    embeddings = FakeEmbedder()([c.text for c in comments])
    a = engine.process(comments, embeddings=embeddings)
    b = _engine().process(comments)
    assert [c.narrative_id for c in a.comments] == [c.narrative_id for c in b.comments]
    with pytest.raises(ValueError):
        engine.process(comments, embeddings=embeddings[:5])


class NoneLabeler:
    """Simulates an LLM that failed after all retries."""

    def label_narrative(self, texts):
        return None

    def label_topic(self, labels):
        return None


@pytest.mark.parametrize("labeler", [None, NoneLabeler()])
def test_keyword_labels_when_llm_missing_or_failing(labeler):
    words = {"A1": "charging stations broken network", "B1": "measles vaccine children outbreak"}
    comments = [
        Comment(comment_id=f"{g}-{i}", author="u", text=f"{g}:{i} {words[g]}")
        for g in words for i in range(15)
    ]
    engine = SpatialEngine(SpatialEngineConfig(), embedder=FakeEmbedder(), labeler=labeler)
    result = engine.process(comments)
    labels = {c.text.split(":")[0]: c.narrative_label for c in result.comments}
    assert "charging" in labels["A1"] and "measles" in labels["B1"]
    narrative = next(n for n in result.narratives if "charging" in n.label)
    assert narrative.keywords and narrative.keywords[0].weight > 0
    assert all(t.keywords for t in result.topics)


def test_noise_and_deleted_comments_are_labeled_noise():
    comments = _comments({"A1": 15, "A2": 15})
    comments += [
        Comment(comment_id="del", author="x", text="[deleted]"),
        Comment(comment_id="empty", author="x", text="   "),
    ]
    result = _engine().process(comments)
    by_id = {c.comment_id: c for c in result.comments}
    for cid in ("del", "empty"):
        c = by_id[cid]
        assert (c.narrative_id, c.topic_id) == (NOISE_ID, NOISE_ID)
        assert c.narrative_label == c.topic_label == NOISE_LABEL


def test_too_few_comments_is_all_noise():
    result = _engine().process(_comments({"A1": 3}))
    assert all(c.narrative_id == NOISE_ID for c in result.comments)
    assert result.narratives == [] and result.topics == []


def test_empty_input():
    assert _engine().process([]).comments == []


def test_extra_fields_and_order_preserved_input_not_mutated():
    comments = _comments({"A1": 10})
    for i, c in enumerate(comments):
        setattr(c, "created_utc", 1_700_000_000 + i)
    result = _engine().process(comments)
    assert [c.comment_id for c in result.comments] == [c.comment_id for c in comments]
    assert result.comments[3].model_dump()["created_utc"] == 1_700_000_003
    assert comments[0].narrative_id is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("<think>hmm</think>\nEVs Too Expensive", "EVs Too Expensive"),
        ('"Remote Work Debate."', "Remote Work Debate"),
        ("Label: Housing Costs\nextra", "Housing Costs"),
        ("", ""),
    ],
)
def test_clean_label(raw, expected):
    assert clean_label(raw) == expected


def test_class_tfidf_prefers_distinctive_words():
    from candor.representation import class_tfidf, keywords_label

    kw = class_tfidf(
        {
            0: ["battery charging station", "charging network battery"],
            1: ["battery recycling plant", "recycling mines battery"],
            NOISE_ID: ["battery lol"],
        },
        exclude_classes=[NOISE_ID],
    )
    assert set(kw) == {0, 1}
    top0 = [w for w, _ in kw[0][:2]]
    assert "charging" in top0 and "battery" not in top0  # shared word is down-weighted
    assert keywords_label(kw[0]).startswith("charging")
    assert class_tfidf({0: ["the and of"]}) == {0: []}
