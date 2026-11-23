"""Golden set: a fixed, stratified evaluation sample that every classifier change is measured against."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path


def build_golden(tickets: list[dict], labels: list[dict], per_category: int = 20, seed: int = 7) -> list[dict]:
    """Stratify by category, then spread across phrasing styles within each category."""
    rng = random.Random(seed)
    buckets: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for i, lab in enumerate(labels):
        buckets[lab["true_category"]][lab["style"]].append(i)

    chosen: list[int] = []
    for cat in sorted(buckets):
        styles = {s: rng.sample(idx, len(idx)) for s, idx in sorted(buckets[cat].items())}
        picked: list[int] = []
        while len(picked) < per_category and any(styles.values()):
            for s in sorted(styles):  # round-robin over styles so rare styles are represented
                if styles[s] and len(picked) < per_category:
                    picked.append(styles[s].pop())
        chosen += picked
    chosen.sort(key=lambda i: tickets[i]["opened_at"])
    return [{"ticket": tickets[i], "label": labels[i]} for i in chosen]


def write_golden(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_golden(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
