"""Command-line entry point for Stage 2 (clustering + report).

    python -m candor.pipeline reddit_posts.json --out-dir out/

Accepts either the scraper's nested Reddit dump or a flat list of ``Comment``
dicts. Writes ``stage2_result.json`` (the :class:`ProcessingResult`) and
``stage2_report.html`` (interactive cluster map + word clouds) to ``--out-dir``.
Embeddings are cached in ``--out-dir`` so tuning re-runs skip re-embedding.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
from pathlib import Path
from typing import Sequence

import numpy as np

from candor.ingest import load_comments
from candor.schemas import NOISE_ID, Comment, ProcessingResult
from candor.spatial_engine import SpatialEngine, SpatialEngineConfig

logger = logging.getLogger(__name__)


def cached_embeddings(engine: SpatialEngine, comments: Sequence[Comment], cache: Path) -> np.ndarray:
    """Embed ``comments``, reusing ``cache`` when model and texts are unchanged."""
    embedder_id = getattr(engine.embedder, "cache_id", type(engine.embedder).__name__)
    digest = hashlib.sha256(embedder_id.encode())
    for c in comments:
        digest.update(c.comment_id.encode() + b"\0" + c.text.encode() + b"\0")
    key = digest.hexdigest()

    if cache.exists():
        with np.load(cache) as data:
            if str(data["key"]) == key:
                logger.info("Using cached embeddings from %s", cache)
                return data["embeddings"]
    logger.info("Embedding %d texts with %s", len(comments), embedder_id)
    embeddings = engine.embed(comments)
    np.savez(cache, key=key, embeddings=embeddings)
    return embeddings


def labels_source(engine: SpatialEngine) -> str:
    """Human-readable origin of the labels, for the report subtitle."""
    model = getattr(engine.labeler, "model", None)
    return f"LLM ({model})" if model else "c-TF-IDF keywords"


def print_summary(result: ProcessingResult, max_topics: int = 25) -> None:
    noise = sum(c.narrative_id == NOISE_ID for c in result.comments)
    total = len(result.comments)
    print(
        f"\n{len(result.topics)} topics, {len(result.narratives)} narratives, "
        f"noise {noise}/{total} ({100 * noise / max(total, 1):.0f}%)"
    )
    for topic in sorted(result.topics, key=lambda t: -t.size)[:max_topics]:
        print(f"\n[topic {topic.topic_id}] {topic.label}  ({topic.size})")
        for n in sorted(
            (n for n in result.narratives if n.topic_id == topic.topic_id), key=lambda n: -n.size
        ):
            print(f"    [narrative {n.narrative_id}] {n.label}  ({n.size})")
    if len(result.topics) > max_topics:
        print(f"\n... {len(result.topics) - max_topics} smaller topics not shown")


def main(argv: Sequence[str] | None = None) -> None:
    try:  # before argparse, so .env can set defaults such as EMBEDDING_BACKEND
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    defaults = SpatialEngineConfig()
    parser = argparse.ArgumentParser(description="Stage 2: cluster comments into narratives/topics")
    parser.add_argument("input", type=Path, help="Reddit dump or flat list of Comment objects (JSON)")
    parser.add_argument("--out-dir", type=Path, default=Path("out"))
    parser.add_argument(
        "--embedder", choices=["gemini", "local"],
        default=os.getenv("EMBEDDING_BACKEND", defaults.embedding_backend),
        help="gemini = gemini-embedding-2 (needs GEMINI_API_KEY); local = MiniLM, offline",
    )
    parser.add_argument("--min-cluster-size", type=int, default=defaults.hdbscan_min_cluster_size)
    parser.add_argument("--min-samples", type=int, default=defaults.hdbscan_min_samples)
    parser.add_argument("--selection", choices=["leaf", "eom"], default=defaults.hdbscan_selection_method)
    parser.add_argument("--topic-method", choices=["agglomerative", "dbscan"], default=defaults.topic_method)
    parser.add_argument(
        "--topic-threshold", type=float, default=defaults.topic_distance_threshold,
        help="agglomerative: max average cosine distance between narratives in a topic "
        "(default: tuned per embedder)",
    )
    parser.add_argument("--topic-eps", type=float, default=defaults.topic_eps, help="dbscan only")
    parser.add_argument("--no-report", action="store_true", help="Skip the HTML report")
    parser.add_argument(
        "--report-name", default="stage2_report.html",
        help="Report filename inside --out-dir (use a new name to keep earlier reports)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("httpx", "httpx2", "huggingface_hub", "sentence_transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    comments = load_comments(args.input)
    logger.info("Loaded %d items from %s", len(comments), args.input)

    config = SpatialEngineConfig(
        embedding_backend=args.embedder,
        hdbscan_min_cluster_size=args.min_cluster_size,
        hdbscan_min_samples=args.min_samples,
        hdbscan_selection_method=args.selection,
        topic_method=args.topic_method,
        topic_distance_threshold=args.topic_threshold,
        topic_eps=args.topic_eps,
    )
    engine = SpatialEngine(config)
    embeddings = cached_embeddings(engine, comments, args.out_dir / f"embeddings_{args.embedder}.npz")
    result = engine.process(comments, embeddings=embeddings)

    result_path = args.out_dir / "stage2_result.json"
    result_path.write_text(result.model_dump_json(indent=2))
    logger.info("Wrote %s", result_path)

    if not args.no_report:
        from candor.visualize import write_report

        report_path = write_report(
            result, embeddings, args.out_dir / args.report_name, config,
            source=args.input.name, labels_source=labels_source(engine),
        )
        logger.info("Wrote %s", report_path)

    print_summary(result)


if __name__ == "__main__":
    main()
