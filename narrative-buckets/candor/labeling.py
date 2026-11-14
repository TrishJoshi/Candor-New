"""LLM labels for narratives and topics.

:class:`NemotronLabeler` calls NVIDIA Nemotron through any OpenAI-compatible
endpoint (default: the Verda/DataCrunch-hosted Nemotron 3.5 Super VL container;
NVIDIA's ``https://integrate.api.nvidia.com/v1`` also works via
``NVIDIA_BASE_URL``), with client-side throttling and exponential-backoff retries. A labeler
returns ``None`` when it cannot produce a label; the spatial engine then falls
back to c-TF-IDF keywords (see :mod:`candor.representation`). With no API key
configured, :func:`default_labeler` returns ``None`` and keyword labels are
used throughout.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Protocol, Sequence

import openai
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://containers.datacrunch.io/nemotron-3-5-super-vl-b300/v1"
DEFAULT_NVIDIA_MODEL = "nemotron-3.5-super-vl"

NARRATIVE_PROMPT = (
    "Read these {n} clustered social media comments. Summarize the core "
    "underlying opinion or perspective in exactly 3 to 5 words. "
    "Output nothing but the label.\n\n{comments}"
)
TOPIC_PROMPT = (
    "Here are distinct perspectives found in a discussion: {labels}. "
    "What is the single, overarching topic they are all debating? "
    "Summarize the macro-topic in exactly 1 to 3 words. "
    "Output nothing but the label."
)

_MAX_CHARS_PER_COMMENT = 500
_RETRYABLE = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)


class Labeler(Protocol):
    def label_narrative(self, texts: Sequence[str]) -> str | None: ...
    def label_topic(self, narrative_labels: Sequence[str]) -> str | None: ...


class NemotronLabeler:
    """LLM labeler using NVIDIA Nemotron through the OpenAI SDK."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_NVIDIA_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        requests_per_minute: int = 40,
        max_retries: int = 6,
        disable_thinking: bool = True,
    ) -> None:
        """``disable_thinking`` turns off Nemotron 3.x reasoning (vLLM chat-template
        flag). Labels are a few words; with reasoning on, the model spends ~250
        tokens thinking first and a small ``max_tokens`` yields an empty answer.
        """
        # max_retries=0 on the client: tenacity owns retry policy.
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=60)
        self.model = model
        self._extra_body = (
            {"chat_template_kwargs": {"enable_thinking": False}} if disable_thinking else None
        )
        self._max_tokens = 32 if disable_thinking else 1024
        self._min_interval = 60.0 / requests_per_minute
        self._last_call = 0.0
        self._complete_with_retry = retry(
            retry=retry_if_exception_type(_RETRYABLE),
            wait=wait_exponential(multiplier=2, min=2, max=60),
            stop=stop_after_attempt(max_retries),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )(self._complete)

    @classmethod
    def from_env(cls) -> "NemotronLabeler | None":
        api_key = os.getenv("NVIDIA_API_KEY")
        if not api_key:
            return None
        return cls(
            api_key=api_key,
            model=os.getenv("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL),
            base_url=os.getenv("NVIDIA_BASE_URL", DEFAULT_BASE_URL),
            requests_per_minute=int(os.getenv("NVIDIA_RPM", "40")),
            disable_thinking=os.getenv("NVIDIA_DISABLE_THINKING", "1") not in ("0", "false", "no"),
        )

    def _throttle(self) -> None:
        wait = self._last_call + self._min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _complete(self, prompt: str) -> str:
        self._throttle()
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=self._max_tokens,
            extra_body=self._extra_body,
        )
        return clean_label(resp.choices[0].message.content or "")

    def _ask(self, prompt: str) -> str | None:
        try:
            return self._complete_with_retry(prompt) or None
        except openai.OpenAIError as exc:
            logger.warning("LLM labeling failed (%s); falling back to keywords", exc)
            return None

    def label_narrative(self, texts: Sequence[str]) -> str | None:
        comments = "\n".join(
            f"{i}. {t[:_MAX_CHARS_PER_COMMENT].strip()}" for i, t in enumerate(texts, 1)
        )
        return self._ask(NARRATIVE_PROMPT.format(n=len(texts), comments=comments))

    def label_topic(self, narrative_labels: Sequence[str]) -> str | None:
        labels = "; ".join(f'"{l}"' for l in narrative_labels)
        return self._ask(TOPIC_PROMPT.format(labels=labels))


def clean_label(raw: str) -> str:
    """Strip reasoning tags, quotes, prefixes and trailing punctuation from an LLM label."""
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    text = re.sub(r"^(label|topic|narrative)\s*:\s*", "", lines[0], flags=re.IGNORECASE)
    return text.strip(" \"'*`.").strip()


def default_labeler() -> Labeler | None:
    """Nemotron if ``NVIDIA_API_KEY`` is set, otherwise ``None`` (keyword labels)."""
    llm = NemotronLabeler.from_env()
    if llm is None:
        logger.info("NVIDIA_API_KEY not set; using c-TF-IDF keyword labels")
    return llm
