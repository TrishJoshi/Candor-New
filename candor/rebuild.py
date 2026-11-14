"""Rebuild the narrative map (map.html) of stored analyses, e.g. after a change to the map.

    python -m candor.rebuild <analysis_id> [...]
    python -m candor.rebuild --all

Uses the stored stage2.json / post.json / scores.json. Analyses from before embeddings were
stored (embeddings.npy) are re-embedded once with Gemini (cheap; no context.dev credits).
"""
from __future__ import annotations

import json
import logging
import re
import sys

import numpy as np

from candor import live, store
from candor.schemas import ProcessingResult

logger = logging.getLogger(__name__)


def rebuild_map(analysis_id: str) -> None:
    d = store.run_dir(analysis_id)
    post = json.loads((d / "post.json").read_text(encoding="utf-8"))
    scores = json.loads((d / "scores.json").read_text(encoding="utf-8"))
    result = ProcessingResult.model_validate_json((d / "stage2.json").read_text(encoding="utf-8"))
    cfg = live.engine_config(len(result.comments))
    emb_path = d / "embeddings.npy"
    if emb_path.exists():
        embeddings = np.load(emb_path)
    else:
        logger.info("%s: re-embedding %d comments", analysis_id, len(result.comments))
        embeddings = live.SpatialEngine(cfg).embed(result.comments)
        np.save(emb_path, np.asarray(embeddings, dtype="float32"))
    model = None
    old = d / "map.html"
    if old.exists() and (m := re.search(r'"labels":"LLM \(([^)"]+)\)"', old.read_text(encoding="utf-8"))):
        model = m.group(1)
    if not any(n.summary for n in result.narratives):  # runs from before AI summaries existed
        from candor.labeling import add_summaries, default_labeler
        if add_summaries(result, default_labeler()):
            store.write_json(analysis_id, "stage2.json", result.model_dump())
    live.write_map(analysis_id, post, result, embeddings, cfg, scores, model)


def main(argv: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    ids = ([p.name for p in sorted(store.RUNS.iterdir()) if (p / "stage2.json").exists()]
           if argv == ["--all"] else argv)
    if not ids:
        sys.exit(__doc__)
    for aid in ids:
        try:
            rebuild_map(aid)
            print(f"rebuilt {aid}")
        except Exception as e:  # keep going; one broken run shouldn't stop the rest
            print(f"FAILED {aid}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
