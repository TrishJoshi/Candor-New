"""Cluster keyword representations via class-based TF-IDF (c-TF-IDF).

This is the representation BERTopic uses for its topic words and word clouds:
all documents of a cluster are treated as one "class document", term frequency
is computed per class, and IDF is computed across classes, so words that are
frequent in one cluster but rare in the others score highest.

    W(t, c) = tf(t, c) * log(1 + A / f(t))

where tf is L1-normalised per class, A is the average number of words per
class and f(t) is the frequency of t across all classes.
"""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer
from sklearn.preprocessing import normalize

# Conversational filler that dominates Reddit text but says nothing about a cluster.
REDDIT_STOP_WORDS = frozenset(
    """
    just like don dont im ive id ill youre theyre thats theres doesnt didnt isnt
    wasnt arent cant wont wouldnt couldnt shouldnt yeah yes lol lmao gonna wanna
    really actually pretty thing things lot got get gets getting know think
    say said make makes way going want did does good people deleted removed
    https http www com amp gt lt reddit edit oh ok okay sure maybe
    """.split()
)
STOP_WORDS = sorted(ENGLISH_STOP_WORDS | REDDIT_STOP_WORDS)

Keywords = list[tuple[str, float]]


def class_tfidf(
    docs_by_class: Mapping[int, Sequence[str]],
    top_n: int = 30,
    exclude_classes: Sequence[int] = (),
) -> dict[int, Keywords]:
    """Top ``top_n`` (term, weight) pairs per class, highest weight first.

    Classes in ``exclude_classes`` (e.g. noise) still contribute to the IDF, so
    globally common words are down-weighted, but get no keywords of their own.
    """
    classes = [c for c in docs_by_class if docs_by_class[c]]
    if not classes:
        return {}
    joined = [" ".join(docs_by_class[c]) for c in classes]

    vectorizer = CountVectorizer(
        stop_words=STOP_WORDS,
        ngram_range=(1, 2),
        token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z]+\b",
        lowercase=True,
    )
    try:
        counts = vectorizer.fit_transform(joined)
    except ValueError:  # empty vocabulary (only stop words / punctuation)
        return {c: [] for c in classes if c not in exclude_classes}

    words_per_class = counts.sum() / len(classes)
    term_freq = np.asarray(counts.sum(axis=0)).ravel()
    idf = np.log1p(words_per_class / term_freq)
    weights = normalize(counts, norm="l1", axis=1).multiply(idf).tocsr()

    vocab = vectorizer.get_feature_names_out()
    out: dict[int, Keywords] = {}
    for row, c in enumerate(classes):
        if c in exclude_classes:
            continue
        rw = weights.getrow(row)
        order = np.argsort(-rw.data)[:top_n]
        out[c] = [(str(vocab[rw.indices[i]]), float(rw.data[i])) for i in order]
    return out


def keywords_label(keywords: Keywords, n: int = 3) -> str:
    """Offline label from the top keywords, skipping words already inside a chosen bigram."""
    picked: list[str] = []
    for word, _ in keywords:
        if any(word in p or p in word for p in picked):
            continue
        picked.append(word)
        if len(picked) == n:
            break
    return " · ".join(picked) if picked else "Unlabeled"
