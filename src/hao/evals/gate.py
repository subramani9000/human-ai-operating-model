"""Eval gate: CI fails if a change makes the system measurably worse (ADR 0011).

Measures the same things the ADRs argue from: redaction recall and precision, classifier accuracy and
risk-downgrade errors on both golden sets, and high-risk over-autonomy in the policy simulation. Each
current value is compared with ``evals/baselines.yaml``. Improving a baseline is a deliberate,
reviewed change (``hao eval gate --update`` in the same PR), never a side effect.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from hao.config import REPO_ROOT
from hao.evals.classification_eval import evaluate_classification, run_classifier
from hao.evals.golden import read_golden
from hao.evals.redaction_eval import evaluate_redaction
from hao.intake.classifiers import make_classifier
from hao.policy.schema import load_policy
from hao.policy.simulate import simulate

BASELINES = REPO_ROOT / "evals" / "baselines.yaml"
HIGHER_IS_BETTER = {"recall", "precision", "accuracy", "macro_f1"}


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def measure(classifier: str = "keyword") -> dict:
    data = REPO_ROOT / "data" / "synthetic"
    tickets_file = data / "tickets.jsonl" if (data / "tickets.jsonl").exists() else data / "sample_tickets.jsonl"
    labels_file = data / "labels.jsonl" if (data / "labels.jsonl").exists() else data / "sample_labels.jsonl"
    red = evaluate_redaction(_read_jsonl(tickets_file), _read_jsonl(labels_file))
    out: dict = {"redaction": {"recall": round(red.recall["ALL"], 4), "precision": round(red.precision, 4)}}
    clf = make_classifier(classifier)
    policy = load_policy(REPO_ROOT / "policy")
    for golden in ("golden_v1", "hard_v1"):
        rows = read_golden(REPO_ROOT / "data" / "golden" / f"{golden}.jsonl")
        preds = run_classifier(clf, rows, workers=1 if classifier == "keyword" else 4)
        rep = evaluate_classification(rows, preds)
        sim = simulate("current", policy, rows, preds)
        out[f"{classifier}/{golden}"] = {"accuracy": round(rep.accuracy, 4), "macro_f1": round(rep.macro_f1, 4),
                                         "risk_downgrades": rep.risk_downgrades,
                                         "over_autonomy_high_risk": sim.over_high_risk}
    return out


def compare(baseline: dict, current: dict, tolerance: float) -> list[dict]:
    rows = []
    for group, metrics in current.items():
        for name, value in metrics.items():
            base = baseline.get(group, {}).get(name)
            if base is None:
                rows.append({"metric": f"{group}.{name}", "baseline": None, "current": value, "ok": True,
                             "note": "new metric (no baseline)"})
                continue
            if name in HIGHER_IS_BETTER:
                ok = value >= base - tolerance
            else:  # counts of bad outcomes: must not increase
                ok = value <= base
            note = "" if ok else ("regressed" if value != base else "")
            if ok and value != base:
                note = "improved: run `hao eval gate --update`" if (
                    (name in HIGHER_IS_BETTER and value > base) or (name not in HIGHER_IS_BETTER and value < base)
                ) else "within tolerance"
            rows.append({"metric": f"{group}.{name}", "baseline": base, "current": value, "ok": ok, "note": note})
    return rows


def load_baselines(path: Path = BASELINES) -> tuple[dict, float]:
    data = yaml.safe_load(path.read_text())
    return data["metrics"], float(data.get("tolerance", 0.005))


def write_baselines(current: dict, tolerance: float, path: Path = BASELINES) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ("# Eval baselines enforced in CI by `hao eval gate` (ADR 0011).\n"
              "# Higher is better for recall/precision/accuracy/macro_f1 (may drop by at most `tolerance`).\n"
              "# Counts of bad outcomes (risk_downgrades, over_autonomy_high_risk) may never increase.\n"
              "# Update only deliberately, in the PR that earns the improvement: `hao eval gate --update`.\n")
    path.write_text(header + yaml.safe_dump({"tolerance": tolerance, "metrics": current}, sort_keys=True))
