"""Adapters from Stage 1 scraper output to Stage 2 ``Comment`` objects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from candor.schemas import Comment


def is_reddit_dump(raw: Any) -> bool:
    """True for the scraper's nested format: a list of posts with ``comments`` trees."""
    return isinstance(raw, list) and bool(raw) and isinstance(raw[0], dict) and "comments" in raw[0]


def flatten_reddit_posts(posts: Iterable[dict]) -> list[Comment]:
    """Flatten posts and their reply trees into one list.

    Posts become items with ``kind="post"`` and text = title + body. Comments keep
    ``post_id``, ``parent_id`` and reply ``depth`` so Stage 3 can rebuild threads.
    """
    out: list[Comment] = []

    def walk(nodes: list[dict], post: dict, parent_id: str, depth: int) -> None:
        for node in nodes:
            out.append(
                Comment(
                    comment_id=node["comment_id"],
                    author=node.get("author") or "[deleted]",
                    text=node.get("body") or "",
                    kind="comment",
                    post_id=post["post_id"],
                    parent_id=parent_id,
                    depth=depth,
                    subreddit=post.get("subreddit"),
                    created_utc=node.get("created_utc"),
                    upvote_count=node.get("upvote_count"),
                )
            )
            walk(node.get("replies") or [], post, node["comment_id"], depth + 1)

    for post in posts:
        title, body = post.get("title") or "", post.get("body") or ""
        out.append(
            Comment(
                comment_id=post["post_id"],
                author=post.get("author") or "[deleted]",
                text=f"{title}\n\n{body}".strip(),
                kind="post",
                post_id=post["post_id"],
                parent_id=None,
                depth=-1,
                subreddit=post.get("subreddit"),
                created_utc=post.get("created_utc"),
                upvote_count=post.get("upvote_count"),
                permalink=post.get("permalink"),
            )
        )
        walk(post.get("comments") or [], post, post["post_id"], 0)
    return out


def load_comments(path: Path) -> list[Comment]:
    """Load either a flat list of ``Comment`` dicts or a nested Reddit dump."""
    raw = json.loads(Path(path).read_text())
    if is_reddit_dump(raw):
        return flatten_reddit_posts(raw)
    return [Comment.model_validate(c) for c in raw]
