"""Reddit post URL validation and normalisation (SCRAPING.md section 2)."""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger("candor.urls")

_POST_ID = re.compile(r"^[a-z0-9]{4,10}$")
_SHARE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{4,32}$")
SHARE_RESOLVE_TIMEOUT_S = float(os.environ.get("SHARE_RESOLVE_TIMEOUT_S", "6"))
_MAX_HOPS = 3
_UA = "Mozilla/5.0 (compatible; CandorBot/1.0; +https://candor-junction-2026.tech)"


def _is_reddit_host(host: str) -> bool:
    return host == "reddit.com" or host.endswith(".reddit.com")


def normalize_reddit_url(url: str) -> tuple[str, str] | None:
    """Canonical thread URL and ``t3_`` post id, or None if this isn't a Reddit post link.

    Accepts www./old./new./m./np. hosts, comment permalinks (cut back to the post, so the
    whole thread is analysed), query strings and fragments, and redd.it short links.
    """
    p = urlparse((url or "").strip())
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https"):
        return None
    parts = [x for x in p.path.split("/") if x]
    if host == "redd.it":
        pid = parts[0].lower() if parts else ""
        sub = None
    elif _is_reddit_host(host):
        if "comments" not in parts:
            return None
        i = parts.index("comments")
        pid = parts[i + 1].lower() if len(parts) > i + 1 else ""
        sub = parts[i - 1] if i >= 2 and parts[i - 2].lower() == "r" else None
    else:
        return None
    if not _POST_ID.match(pid):
        return None
    canonical = (f"https://www.reddit.com/r/{sub}/comments/{pid}/" if sub
                 else f"https://www.reddit.com/comments/{pid}/")
    return canonical, f"t3_{pid}"


def is_reddit_share_link(url: str) -> bool:
    """True for the app's "Share" links, e.g. ``https://www.reddit.com/r/<sub>/s/<token>``.

    These carry no post id; Reddit redirects them to the real post permalink.
    """
    p = urlparse((url or "").strip())
    if p.scheme not in ("http", "https") or not _is_reddit_host((p.hostname or "").lower()):
        return False
    parts = [x for x in p.path.split("/") if x]
    return len(parts) >= 2 and parts[-2] == "s" and bool(_SHARE_TOKEN.match(parts[-1]))


def resolve_share_link(url: str, client: httpx.Client | None = None) -> tuple[str, str] | None:
    """Follow a Reddit share link's redirects (Reddit hosts only) to the post it points at.

    Returns the same ``(canonical, post_id)`` as :func:`normalize_reddit_url`, or None if the
    link is not a share link, Reddit doesn't redirect it to a post (e.g. an expired token
    redirects to the subreddit), or the request fails.
    """
    if not is_reddit_share_link(url):
        return None
    own = client is None
    client = client or httpx.Client(timeout=SHARE_RESOLVE_TIMEOUT_S, headers={"User-Agent": _UA})
    current = url.strip()
    try:
        for _ in range(_MAX_HOPS):
            r = client.get(current, follow_redirects=False)
            loc = r.headers.get("location")
            if not r.is_redirect or not loc:
                return None
            current = urljoin(current, loc)
            if not _is_reddit_host((urlparse(current).hostname or "").lower()):
                return None
            if norm := normalize_reddit_url(current):
                return norm
    except httpx.HTTPError as exc:
        logger.warning("share link resolve failed for %s: %s: %s", url, type(exc).__name__, exc)
        return None
    finally:
        if own:
            client.close()
    return None
