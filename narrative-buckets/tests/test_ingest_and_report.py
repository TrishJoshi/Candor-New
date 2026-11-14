"""Tests for the Reddit ingest adapter and report data builder."""

from __future__ import annotations

import json

import numpy as np

from candor.ingest import flatten_reddit_posts, load_comments
from candor.schemas import Comment, Keyword, NarrativeSummary, ProcessingResult, TopicSummary
from candor.spatial_engine import SpatialEngineConfig
from candor.visualize import build_report_data, write_report

POSTS = [
    {
        "post_id": "t3_a",
        "title": "Title A",
        "body": "Body A",
        "author": "op",
        "subreddit": "politics",
        "created_utc": 100.0,
        "comments": [
            {
                "comment_id": "t1_1",
                "author": "x",
                "body": "top level",
                "created_utc": 101.0,
                "upvote_count": 3,
                "replies": [
                    {"comment_id": "t1_2", "author": "y", "body": "reply", "created_utc": 102.0, "replies": []}
                ],
            }
        ],
    }
]


def test_flatten_reddit_posts_keeps_thread_structure():
    items = flatten_reddit_posts(POSTS)
    assert [c.comment_id for c in items] == ["t3_a", "t1_1", "t1_2"]
    post, top, reply = (c.model_dump() for c in items)
    assert post["kind"] == "post" and post["text"] == "Title A\n\nBody A"
    assert top["parent_id"] == "t3_a" and top["depth"] == 0
    assert reply["parent_id"] == "t1_1" and reply["depth"] == 1
    assert reply["post_id"] == "t3_a" and reply["subreddit"] == "politics"
    assert reply["created_utc"] == 102.0


def test_load_comments_detects_format(tmp_path):
    nested, flat = tmp_path / "nested.json", tmp_path / "flat.json"
    nested.write_text(json.dumps(POSTS))
    flat.write_text(json.dumps([{"comment_id": "c1", "author": "a", "text": "hi"}]))
    assert len(load_comments(nested)) == 3
    assert load_comments(flat)[0].text == "hi"


def test_report_data_and_html(tmp_path):
    rng = np.random.default_rng(0)
    comments = [
        Comment(comment_id=f"c{i}", author="a</script>", text=f"text {i}", subreddit="s",
                narrative_id=i % 2 if i < 8 else -1, topic_id=0 if i < 8 else -1,
                narrative_label="n", topic_label="t")
        for i in range(10)
    ]
    kw = [Keyword(word="w", weight=0.5)]
    result = ProcessingResult(
        comments=comments,
        narratives=[
            NarrativeSummary(narrative_id=k, label=f"n{k}", topic_id=0, size=4,
                             representative_comment_ids=["c0"], keywords=kw)
            for k in (0, 1)
        ],
        topics=[TopicSummary(topic_id=0, label="t", narrative_ids=[0, 1], size=8, keywords=kw)],
    )
    emb = rng.normal(size=(10, 8))
    data = build_report_data(result, emb, SpatialEngineConfig())
    assert len(data["points"]["x"]) == 10
    assert data["meta"]["n_noise"] == 2
    assert [tuple(s) for s in data["topics"][0]["subreddits"]] == [("s", 8)]

    out = write_report(result, emb, tmp_path / "r.html")
    html = out.read_text()
    assert "__CANDOR_DATA__" not in html
    assert "a</script>" not in html  # user text cannot break out of the data block


def test_report_engagement_and_authenticity():
    """Upvotes size the dots; ``authors`` adds per-point scores and per-narrative engagement by band."""
    comments = [
        Comment(comment_id=f"c{i}", author=f"u{i % 3}", text=f"text {i}", narrative_id=i % 2,
                topic_id=0, upvote_count=[10, 0, 5, None][i % 4])
        for i in range(8)
    ]
    kw = [Keyword(word="w", weight=0.5)]
    result = ProcessingResult(
        comments=comments,
        narratives=[NarrativeSummary(narrative_id=k, label=f"n{k}", topic_id=0, size=4,
                                     representative_comment_ids=["c0"], keywords=kw) for k in (0, 1)],
        topics=[TopicSummary(topic_id=0, label="t", narrative_ids=[0, 1], size=8, keywords=kw)],
    )
    emb = np.random.default_rng(0).normal(size=(8, 8))

    plain = build_report_data(result, emb, SpatialEngineConfig())
    assert plain["meta"]["has_upvotes"] and not plain["meta"]["has_authenticity"]
    assert "auth" not in plain["points"] and plain["points"]["upvotes"][:4] == [10, 0, 5, None]

    authors = {"u0": {"score": 92.0, "label": "likely_authentic"},
               "u1": {"score": 75.0, "label": "uncertain"}}  # label (red flag) wins over the score band
    data = build_report_data(result, emb, SpatialEngineConfig(), authors=authors)
    assert data["points"]["band"][:3] == ["ok", "warn", None]  # u2 is unscored
    assert data["points"]["auth"][:3] == [92.0, 75.0, None]
    by = {n["id"]: n["by_band"] for n in data["narratives"]}
    # narrative 0 = c0 (u0, 10), c2 (u2, 5), c4 (u1, 10), c6 (u0, 5): upvotes + 1 each
    assert by[0] == {"ok": 11 + 6, "warn": 11, "bad": 0, "na": 6}
    assert data["meta"]["engagement"]["upvotes"] == sum(n["upvotes"] for n in data["narratives"])
