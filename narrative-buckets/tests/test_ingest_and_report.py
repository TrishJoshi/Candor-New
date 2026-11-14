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
