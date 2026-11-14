"""
Scrape a Facebook post (or any comment page) with context.dev and save it in the
same JSON shape scorer.py reads (see sample_post.json).

Setup:
    pip install context.dev
    export CONTEXT_DEV_API_KEY=ctxt_secret_...

Usage:
    python context_scraper.py <post_url> [out.json] [--no-accounts]
    python scorer.py out.json scored_out.json

X / Twitter URLs (x.com, twitter.com) are scraped logged in. Auth comes from .env:
    X_PROFILE_ID                 saved context.dev browser profile (`python context_scraper.py login`
                                 creates one from X_USERNAME / X_PASSWORD), or
    X_AUTH_TOKEN + X_CT0         auth_token and ct0 cookies copied from a logged-in browser.
For X, each reply author's profile page is also scraped (join date, followers, ...) into
comment.author.account for C1; pass --no-accounts to skip that.

context.dev's JSON extraction is asked to fill a schema that mirrors our post format,
so no conversion step is needed. Note: Facebook hides most comments behind login /
"View more comments", so a public scrape will usually return only a partial comment
set — scorer.py already flags that as `partial_scrape`.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlparse

from context.dev import ContextDev

AUTHOR = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "Display name if no ID is visible"},
        "name": {"type": "string"},
        "profile_url": {"type": "string", "description": "Full href of the author's profile link"},
    },
    "required": ["id", "name", "profile_url"],
}

COMMENT = {
    "type": "object",
    "properties": {
        "comment_id": {"type": "string"},
        "author": AUTHOR,
        "message": {"type": "string", "description": "Exact comment text, empty string if image-only"},
        "attachments": {"type": "array", "items": {"type": "object", "properties": {
            "type": {"type": "string"}, "url": {"type": "string"}, "title": {"type": "string"}}}},
        "reaction_count": {"type": "integer"},
        "created_time": {"type": "string", "description": "ISO 8601 with timezone offset"},
    },
    "required": ["comment_id", "author", "message", "created_time"],
}

POST_SCHEMA = {
    "type": "object",
    "properties": {
        "post_id": {"type": "string"},
        "permalink_url": {"type": "string"},
        "author": AUTHOR,
        "message": {"type": "string"},
        "share_count": {"type": "integer"},
        "comment_count": {"type": "integer", "description": "Total comment count shown on the post"},
        "created_time": {"type": "string", "description": "ISO 8601 with timezone offset"},
        "comments": {"type": "array", "items": COMMENT},
    },
    "required": ["post_id", "message", "comments"],
}

INSTRUCTIONS = (
    "Extract the social media post and every visible comment. Copy text verbatim; do not "
    "summarise or translate. Convert relative times ('3h', '1d') to absolute ISO 8601 "
    "timestamps. Use the comment's own id from its permalink when available."
)


# --------------------------------------------------------------------------------------
# X / Twitter support (logged-in context.dev browser profile)
# --------------------------------------------------------------------------------------
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

X_INSTRUCTIONS = (
    "This is an X (Twitter) post page. The main post is the first tweet; every reply below "
    "it is a comment. Copy text verbatim; do not summarise or translate. For each author, "
    "author.id is the @handle without the @ in lowercase, author.name is the display name, "
    "and author.profile_url is https://x.com/<handle>. comment_id and post_id are the numeric "
    "status id from the tweet's /status/<id> link. created_time is the exact datetime "
    "attribute of the tweet's <time> element (ISO 8601). reaction_count is the reply's like "
    "count. comment_count is the post's reply count. Put an image-only reply's images in "
    "attachments with type 'photo'."
)

X_ACCOUNT_SCHEMA = {
    "type": "object",
    "properties": {
        "handle": {"type": "string"},
        "display_name": {"type": "string"},
        "joined": {"type": "string", "description": "Join month as YYYY-MM from the 'Joined <Month YYYY>' line"},
        "followers_count": {"type": "integer"},
        "following_count": {"type": "integer"},
        "posts_count": {"type": "integer", "description": "Number of posts shown under the name"},
        "verified": {"type": "boolean"},
        "has_profile_photo": {"type": "boolean", "description": "false if the avatar is X's default egg/silhouette"},
        "is_protected": {"type": "boolean", "description": "true if posts are protected (lock icon)"},
        "is_suspended_or_missing": {"type": "boolean"},
        "bio": {"type": "string"},
    },
    "required": ["handle"],
}


def load_env(path: str = ENV_PATH) -> None:
    """Minimal .env reader (accepts optional 'export ' prefix); never overrides real env vars."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.removeprefix("export ").split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip("'\""))


def is_x(url: str) -> bool:
    return urlparse(url).netloc.lower().removeprefix("www.").removeprefix("mobile.") in {"x.com", "twitter.com"}


def client() -> ContextDev:
    return ContextDev(api_key=os.environ["CONTEXT_DEV_API_KEY"], timeout=300)


def x_auth() -> dict:
    """shared_params auth for X. Prefers a saved browser profile (X_PROFILE_ID); otherwise
    falls back to session cookies copied from a logged-in browser (X_AUTH_TOKEN + X_CT0).
    context.dev does not allow profiles and custom headers together, so it's one or the other."""
    if pid := os.environ.get("X_PROFILE_ID"):
        return {"profile": {"type": "existing", "profileId": pid}}
    tok, ct0 = os.environ.get("X_AUTH_TOKEN"), os.environ.get("X_CT0")
    if tok and ct0:
        return {"headers": {"Cookie": f"auth_token={tok}; ct0={ct0}", "x-csrf-token": ct0}}
    sys.exit("No X auth: set X_AUTH_TOKEN + X_CT0 in .env, or run `python context_scraper.py login`")


def x_login() -> str:
    """Sign in once with X_USERNAME / X_PASSWORD, save the browser profile id to .env."""
    page = client().web.scrape(
        url="https://x.com/home",
        formats={"markdown": True},
        shared_params={"profile": {
            "type": "new",
            "identifier": os.environ["X_USERNAME"].strip().lstrip("@"),
            "password": os.environ["X_PASSWORD"],
            "description": "x-scraper",
        }},
        timeout_opts={"milliseconds": 240_000, "behavior": "return-partial"},
    )
    if not page.profile:
        raise RuntimeError("context.dev did not return a profile id")
    md = (page.markdown.content or "") if getattr(page, "markdown", None) else ""
    with open(ENV_PATH, "a", encoding="utf-8") as f:
        f.write(f"\nexport X_PROFILE_ID={page.profile.id}\n")
    os.environ["X_PROFILE_ID"] = page.profile.id
    print(f"profile created and saved to .env: {page.profile.id}")
    print("home page preview (check it looks logged in):\n", md[:600])
    return page.profile.id


def scrape_x_account(handle: str) -> dict:
    page = client().web.scrape(
        url=f"https://x.com/{handle}",
        formats={"json": True},
        json_params={"schema": X_ACCOUNT_SCHEMA,
                     "instructions": "Extract this X profile header. Use null for anything not shown."},
        shared_params={**x_auth(),
                       "actions": [{"type": "wait", "milliseconds": 2500}]},
        timeout_opts={"milliseconds": 120_000, "behavior": "return-partial"},
    )
    return dict(page.json.data) if page.json.success and page.json.data else {"handle": handle, "error": "extraction_failed"}


def scrape_x_post(url: str, scrolls: int = 8) -> dict:
    page = client().web.scrape(
        url=url,
        formats={"json": True},
        json_params={"schema": POST_SCHEMA, "instructions": X_INSTRUCTIONS},
        shared_params={
            **x_auth(),
            "actions": [
                {"type": "waitFor", "selector": "article[data-testid='tweet']"},
                {"type": "scroll", "direction": "down", "amount": "viewport", "max_scrolls": scrolls},
                {"type": "perform", "action": "If a 'Show probably spam' or 'Show more replies' button is visible, click it; otherwise do nothing"},
                {"type": "scroll", "direction": "down", "amount": "viewport", "max_scrolls": 4},
            ],
        },
        timeout_opts={"milliseconds": 240_000, "behavior": "return-partial"},
    )
    if not page.json.success:
        raise RuntimeError(f"context.dev extraction failed: {page.json}")
    return dict(page.json.data)


def scrape_post(url: str, with_accounts: bool = True) -> dict:
    if is_x(url):
        post = scrape_x_post(url)
        post["platform"] = "x"
    else:
        page = client().web.scrape(
            url=url,
            formats={"json": True},
            json_params={"schema": POST_SCHEMA, "instructions": INSTRUCTIONS},
        )
        if not page.json.success:
            raise RuntimeError(f"context.dev extraction failed: {page.json}")
        post = dict(page.json.data)
    post.setdefault("permalink_url", url)
    for c in post.get("comments", []):  # fill fields scorer.py expects
        c.setdefault("attachments", [])
        c.setdefault("replies", [])
        c["author"].setdefault("id", c["author"].get("name", ""))
    if post.get("platform") == "x" and with_accounts:
        # C1 data: one profile-page scrape per unique reply author
        cache: dict[str, dict] = {}
        for c in post.get("comments", []):
            h = c["author"]["id"].lstrip("@").lower()
            if h and h not in cache:
                cache[h] = scrape_x_account(h)
            c["author"]["account"] = cache.get(h)
    return post


if __name__ == "__main__":
    load_env()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args[:1] == ["login"]:
        x_login()
        sys.exit()
    if not args:
        sys.exit("usage: python context_scraper.py login | <post_url> [out.json] [--no-accounts]")
    out = args[1] if len(args) > 1 else "scraped_post.json"
    post = scrape_post(args[0], with_accounts="--no-accounts" not in sys.argv)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(post, f, ensure_ascii=False, indent=2)
    print(f"saved {len(post.get('comments', []))} comments -> {out}")
