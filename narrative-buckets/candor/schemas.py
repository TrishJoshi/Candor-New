"""Data contracts shared between pipeline stages."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict

NOISE_ID = -1
NOISE_LABEL = "Noise"


class Comment(BaseModel):
    """A post or comment flowing through the pipeline.

    Extra fields from Stage 1 (e.g. ``created_utc``, ``post_id``, ``subreddit``)
    are preserved untouched so Stage 3 still has timestamps to work with.
    """

    model_config = ConfigDict(extra="allow")

    comment_id: str
    author: str
    text: str
    # Populated by the spatial engine (Stage 2):
    narrative_id: Optional[int] = None
    narrative_label: Optional[str] = None
    topic_id: Optional[int] = None
    topic_label: Optional[str] = None


class Keyword(BaseModel):
    """A c-TF-IDF term and its weight (higher = more distinctive for the cluster)."""

    word: str
    weight: float


class NarrativeSummary(BaseModel):
    """One fine-grained narrative (HDBSCAN cluster)."""

    narrative_id: int
    label: str
    topic_id: int
    size: int
    representative_comment_ids: List[str]
    keywords: List[Keyword] = []


class TopicSummary(BaseModel):
    """One macro topic (DBSCAN cluster of narrative centroids)."""

    topic_id: int
    label: str
    narrative_ids: List[int]
    size: int
    keywords: List[Keyword] = []


class ProcessingResult(BaseModel):
    comments: List[Comment]
    # Additive summaries for Stage 4 / visualization; not required by consumers.
    narratives: List[NarrativeSummary] = []
    topics: List[TopicSummary] = []
