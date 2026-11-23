"""Okapi BM25 retrieval over the client's KB and previously triaged tickets.

Pure Python, no embedding model: at a few thousand documents BM25 is fast, explainable and good enough
to show agents "here's the article, here are 3 similar tickets". pgvector + embeddings is the upgrade
path once real tickets show where lexical search fails (e.g. Tanglish paraphrases).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

STOP = set("a an the and or to of in on for is it my me i we you your our this that with be at from have has "
           "was are not pls please hi hello team regards thanks kindly can could".split())


def tokens(text: str) -> list[str]:
    text = re.sub(r"<[A-Z_]+_\d+>", " ", text).lower()  # PII tokens carry no meaning for search
    return [t for t in re.findall(r"[a-z0-9][a-z0-9.\-]*[a-z0-9]|[a-z0-9]", text) if t not in STOP]


@dataclass
class Doc:
    id: str
    title: str
    text: str
    meta: dict


class BM25:
    def __init__(self, docs: list[Doc], k1: float = 1.4, b: float = 0.75):
        self.docs = docs
        self.k1, self.b = k1, b
        self._tf = [Counter(tokens(f"{d.title} {d.title} {d.text}")) for d in docs]  # title counts double
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg = sum(self._len) / len(self._len) if docs else 0.0
        df = Counter(t for tf in self._tf for t in tf)
        n = len(docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int = 3, exclude: set[str] | None = None) -> list[tuple[Doc, float]]:
        q = tokens(query)
        scored = []
        for i, doc in enumerate(self.docs):
            if exclude and doc.id in exclude:
                continue
            tf, length = self._tf[i], self._len[i]
            score = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    norm = self.k1 * (1 - self.b + self.b * length / self._avg)
                    score += self._idf[t] * f * (self.k1 + 1) / (f + norm)
            if score > 0:
                scored.append((doc, round(score, 3)))
        return sorted(scored, key=lambda x: x[1], reverse=True)[:k]
