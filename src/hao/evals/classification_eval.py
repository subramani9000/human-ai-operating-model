"""Classification eval against the golden set.

Beyond accuracy, it reports what matters for an operating model:

* **Risk-downgrade errors:** a payroll ticket classified as a password reset is far worse than a VPN
  ticket classified as an email issue, because it would route high-risk work to a low-risk autonomy lane.
* **Calibration:** the policy engine trusts ``confidence`` (rules downgrade below 0.80), so confidence
  has to mean something.
* **Accuracy by phrasing style:** does it work for Tanglish and angry tickets, not just tidy English?
"""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from hao.intake.classifiers import Classification, Classifier
from hao.intake.redaction import Redactor
from hao.intake.taxonomy import Taxonomy, default_taxonomy

BINS = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]


@dataclass
class ClassificationReport:
    classifier: str
    model: str
    n: int
    accuracy: float
    macro_f1: float
    app_accuracy: float
    unknown_rate: float
    risk_downgrades: int
    per_class: dict[str, dict[str, float]]
    by_style: dict[str, float]
    confusions: list[tuple[str, str, int]]
    calibration: list[dict]
    ece: float
    mean_latency_ms: float
    p95_latency_ms: float
    total_tokens: int
    total_cost_usd: float | None
    errors: int
    examples: list[dict] = field(default_factory=list)


def run_classifier(classifier: Classifier, golden: list[dict], workers: int = 1,
                   redactor: Redactor | None = None) -> list[Classification]:
    redactor = redactor or Redactor()
    redacted = [redactor.redact_ticket(row["ticket"])[0] for row in golden]  # same input as production
    if workers <= 1:
        return [classifier.classify(t) for t in redacted]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(classifier.classify, redacted))


def evaluate_classification(golden: list[dict], preds: list[Classification],
                            taxonomy: Taxonomy | None = None) -> ClassificationReport:
    taxonomy = taxonomy or default_taxonomy()
    labels = [row["label"] for row in golden]
    y_true = [lab["true_category"] for lab in labels]
    y_pred = [p.category for p in preds]
    n = len(golden)

    per_class: dict[str, dict[str, float]] = {}
    for cat in sorted(set(y_true)):
        tp = sum(t == p == cat for t, p in zip(y_true, y_pred, strict=True))
        fp = sum(p == cat and t != cat for t, p in zip(y_true, y_pred, strict=True))
        fn = sum(t == cat and p != cat for t, p in zip(y_true, y_pred, strict=True))
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per_class[cat] = {"precision": prec, "recall": rec, "f1": f1, "support": tp + fn}

    style_hits: dict[str, list[bool]] = defaultdict(list)
    for lab, p in zip(labels, y_pred, strict=True):
        style_hits[lab["style"]].append(lab["true_category"] == p)

    confusions = Counter((t, p) for t, p in zip(y_true, y_pred, strict=True) if t != p)

    calibration, ece = [], 0.0
    for lo, hi in BINS:
        idx = [i for i, p in enumerate(preds) if lo <= p.confidence < hi]
        if not idx:
            continue
        acc = sum(y_true[i] == y_pred[i] for i in idx) / len(idx)
        conf = sum(preds[i].confidence for i in idx) / len(idx)
        calibration.append({"bin": f"{lo:.1f}–{min(hi, 1.0):.1f}", "n": len(idx), "mean_confidence": conf,
                            "accuracy": acc})
        ece += len(idx) / n * abs(acc - conf)

    downgrades = [
        (row, p) for row, p in zip(golden, preds, strict=True)
        if p.category != "unknown"
        and taxonomy.risk_rank(p.category) < taxonomy.risk_rank(row["label"]["true_category"])
    ]
    latencies = sorted(p.latency_ms for p in preds)
    costs = [p.cost_usd for p in preds]
    examples = [
        {"number": row["ticket"]["number"], "true": row["label"]["true_category"], "pred": p.category,
         "confidence": p.confidence, "style": row["label"]["style"],
         "text": row["ticket"]["short_description"][:80]}
        for row, p in zip(golden, preds, strict=True) if row["label"]["true_category"] != p.category
    ][:15]

    return ClassificationReport(
        classifier=preds[0].classifier if preds else "",
        model=preds[0].model if preds else "",
        n=n,
        accuracy=sum(t == p for t, p in zip(y_true, y_pred, strict=True)) / n,
        macro_f1=sum(c["f1"] for c in per_class.values()) / len(per_class),
        app_accuracy=sum(lab["true_application"] == p.application for lab, p in zip(labels, preds, strict=True)) / n,
        unknown_rate=y_pred.count("unknown") / n,
        risk_downgrades=len(downgrades),
        per_class=per_class,
        by_style={s: sum(h) / len(h) for s, h in sorted(style_hits.items())},
        confusions=[(t, p, k) for (t, p), k in confusions.most_common(8)],
        calibration=calibration,
        ece=ece,
        mean_latency_ms=sum(latencies) / n,
        p95_latency_ms=latencies[min(n - 1, int(0.95 * n))],
        total_tokens=sum(p.input_tokens + p.output_tokens for p in preds),
        total_cost_usd=sum(costs) if all(c is not None for c in costs) else None,
        errors=sum(bool(p.error) for p in preds),
        examples=examples,
    )


def render_markdown(r: ClassificationReport, golden_name: str, usd_to_inr: float) -> str:
    pct = lambda x: f"{100 * x:.1f}%"  # noqa: E731
    cost = "not tracked (set LLM_*_PRICE_PER_MTOK)" if r.total_cost_usd is None else (
        f"${r.total_cost_usd:.4f} total · ₹{r.total_cost_usd * usd_to_inr / r.n:.3f} per ticket")
    lines = [
        f"# Classification eval: `{r.classifier}`{f' ({r.model})' if r.model else ''}",
        "",
        f"Golden set: `{golden_name}` · {r.n} tickets · input is **redacted** text, exactly as in production.",
        "",
        "| Metric | Value |", "|---|---|",
        f"| Accuracy | **{pct(r.accuracy)}** |",
        f"| Macro F1 | {r.macro_f1:.3f} |",
        f"| Application accuracy | {pct(r.app_accuracy)} |",
        f"| Returned `unknown` | {pct(r.unknown_rate)} |",
        f"| **Risk-downgrade errors** (true risk tier > predicted) | **{r.risk_downgrades}** |",
        f"| Expected calibration error | {r.ece:.3f} |",
        f"| Latency mean / p95 | {r.mean_latency_ms:.0f} ms / {r.p95_latency_ms} ms |",
        f"| Tokens | {r.total_tokens:,} |",
        f"| Cost | {cost} |",
        f"| API errors | {r.errors} |",
        "", "## Accuracy by phrasing style", "", "| Style | Accuracy |", "|---|---|",
        *[f"| {s} | {pct(a)} |" for s, a in r.by_style.items()],
        "", "## Per category", "", "| Category | Precision | Recall | F1 | n |", "|---|---|---|---|---|",
        *[f"| {c} | {m['precision']:.2f} | {m['recall']:.2f} | {m['f1']:.2f} | {m['support']:.0f} |"
          for c, m in r.per_class.items()],
        "", "## Calibration", "", "| Confidence | n | Mean confidence | Actual accuracy |", "|---|---|---|---|",
        *[f"| {b['bin']} | {b['n']} | {b['mean_confidence']:.2f} | {pct(b['accuracy'])} |" for b in r.calibration],
        "", "## Top confusions", "", "| True | Predicted | Count |", "|---|---|---|",
        *[f"| {t} | {p} | {k} |" for t, p, k in r.confusions],
        "", "## Sample errors", "",
        "| Ticket | Style | True | Predicted | Conf. | Subject |", "|---|---|---|---|---|---|",
        *[f"| {e['number']} | {e['style']} | {e['true']} | {e['pred']} | {e['confidence']:.2f} | "
          f"{e['text'].replace('|', '/')} |" for e in r.examples],
        "",
    ]
    return "\n".join(lines)
