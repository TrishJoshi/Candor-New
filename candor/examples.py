"""Bundle finished analyses as static examples for the Demo tab.

    python -m candor.examples <analysis_id> [...]

Copies data/analyses/<id>/{view.json,map.html} to web/examples/<id>/, writes
web/examples/index.json (the demo feed's cards) and regenerates web/view.html and
web/demo.html from mockup/template.html (demo.html = the template in feed mode).
Examples open at view.html?id=ex-<id>, exactly like a live analysis.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from candor import store
from candor.view import distribution
from candor.schemas import NOISE_ID

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "examples"
TEMPLATE = ROOT / "mockup" / "template.html"


def card(aid: str, view: dict, href: str | None = None) -> dict:
    """One demo-feed card. Also used by GET /api/demo for live analyses."""
    t = view["threads"][0]
    s = view["meta"]["summary"]
    top = next((n for n in sorted(view.get("narratives") or [], key=lambda n: -n["weighted_share"])
                if n["id"] != NOISE_ID), None)
    return {
        "id": aid, "href": href or f"view.html?id=ex-{aid}", "post_id": t.get("id"), "sub": t.get("sub"), "title": t.get("title"), "score": t.get("score"),
        "num_comments": t.get("num_comments"), "posted_at": t.get("posted_at"),
        "scraped_at": view["meta"].get("scraped_at"),
        "n_scored": s.get("scored"), "mean_score": s.get("mean_score"), "label_counts": s.get("label_counts", {}),
        "narratives": distribution(view.get("narratives") or []),
        "top_summary": top.get("summary") if top else None,
    }


def write_pages() -> None:
    tpl = TEMPLATE.read_text(encoding="utf-8")
    (ROOT / "web" / "view.html").write_text(tpl, encoding="utf-8")
    (ROOT / "web" / "demo.html").write_text(tpl.replace("/*__GALLERY__*/false", "true"), encoding="utf-8")


def main(ids: list[str]) -> None:
    if not ids:
        sys.exit(__doc__)
    if OUT.exists():
        shutil.rmtree(OUT)
    cards = []
    for aid in ids:
        src = store.run_dir(aid)
        view = json.loads((src / "view.json").read_text(encoding="utf-8"))
        d = OUT / aid
        d.mkdir(parents=True)
        view["meta"]["analysis_id"] = f"ex-{aid}"
        view["meta"]["map_url"] = f"examples/{aid}/map.html"
        (d / "view.json").write_text(json.dumps(view, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        shutil.copy(src / "map.html", d / "map.html")
        cards.append(card(aid, view))
    (OUT / "index.json").write_text(json.dumps(cards, ensure_ascii=False, indent=1), encoding="utf-8")
    write_pages()
    print(f"{len(cards)} examples -> {OUT}")


if __name__ == "__main__":
    main(sys.argv[1:])
