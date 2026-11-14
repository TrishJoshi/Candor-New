"""Reddit post URL validation and normalisation (SCRAPING.md section 2)."""
from __future__ import annotations

import re
from urllib.parse import urlparse

_POST_ID = re.compile(r"^[a-z0-9]{4,10}$")


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
    elif host == "reddit.com" or host.endswith(".reddit.com"):
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
