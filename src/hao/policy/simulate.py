"""Policy simulation: what would this policy + this classifier do to real-looking traffic?

Each golden ticket is decided twice:

* **as classified:** what production would do, using the classifier's category, app and confidence
* **oracle:** what the policy *intends*, using the true category and app at full confidence

Comparing the two shows the harm a classifier's errors could cause *after* the policy's safety net:

* **Over-autonomy:** AI got a higher level than intended. This is the dangerous direction.
* **Under-autonomy:** AI got a lower level than intended. Safe, but costs automation.

Run it before every promotion (``--promote password_reset=A3``) and with ``--no-rules`` to see what the
confidence/VIP/P1 rules are actually buying.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from hao.intake.classifiers import Classification
from hao.intake.redaction import Redactor
from hao.policy.engine import DecisionInput, decide
from hao.policy.schema import Level, Policy


@dataclass
class SimulationResult:
    name: str
    n: int
    levels: dict[str, int]
    oracle_levels: dict[str, int]
    over: int
    over_high_risk: int
    under: int
    exact: int
    examples: list[dict] = field(default_factory=list)


def with_overrides(policy: Policy, promote: dict[str, str] | None = None, no_rules: bool = False) -> Policy:
    cats = dict(policy.categories)
    for cat, level in (promote or {}).items():
        if cat not in cats:
            raise ValueError(f"unknown category {cat!r}")
        lvl = Level(level)
        if cats[cat].max_level < lvl:
            raise ValueError(f"{cat} cannot be promoted to {lvl}: max_level is {cats[cat].max_level}")
        cats[cat] = cats[cat].model_copy(update={"current_level": lvl})
    return policy.model_copy(update={"categories": cats, "rules": [] if no_rules else policy.rules})


def simulate(name: str, policy: Policy, golden: list[dict], preds: list[Classification]) -> SimulationResult:
    levels, oracle_levels = Counter(), Counter()
    over = over_high = under = exact = 0
    examples = []
    redactor = Redactor()
    for row, p in zip(golden, preds, strict=True):
        t, lab = row["ticket"], row["label"]
        red = redactor.redact_ticket(t)[0]
        ctx = {"priority": t.get("priority", "4"), "requester_is_vip": t.get("u_vip") == "true",
               "reopen_count": int(t.get("reopen_count") or 0),
               "text": f"{red['short_description']}\n{red['description']}"}
        got = decide(policy, DecisionInput(p.category, p.application, p.confidence, **ctx))
        want = decide(policy, DecisionInput(lab["true_category"], lab["true_application"], 1.0, **ctx))
        levels[got.level.value] += 1
        oracle_levels[want.level.value] += 1
        if got.level.rank > want.level.rank:
            over += 1
            high = lab.get("risk_tier") in ("high", "critical") or want.risk_tier in ("high", "critical")
            over_high += high
            if len(examples) < 12:
                examples.append({"number": t["number"], "true": lab["true_category"], "pred": p.category,
                                 "confidence": p.confidence, "intended": want.level.value, "got": got.level.value,
                                 "high_risk": high})
        elif got.level.rank < want.level.rank:
            under += 1
        else:
            exact += 1
    return SimulationResult(name, len(golden), dict(levels), dict(oracle_levels), over, over_high, under, exact,
                            examples)


def render(results: list[SimulationResult], classifier: str, golden_name: str) -> str:
    def dist(d: dict[str, int], n: int) -> str:
        return " · ".join(f"{lvl} {100 * d.get(lvl, 0) / n:.0f}%" for lvl in ("A3", "A2", "A1", "A0"))

    lines = [
        f"# Policy simulation: `{classifier}` on `{golden_name}`", "",
        "Each ticket is decided twice: with the classifier's output (what production would do) and with the true "
        "label at full confidence (what the policy intends). **Over-autonomy** = AI got more autonomy than "
        "intended (dangerous). **Under-autonomy** = less than intended (safe, costs automation).", "",
        "| Scenario | Levels as classified | Over-autonomy | …on high/critical work | Under-autonomy | Exact |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r.name} | {dist(r.levels, r.n)} | **{r.over}** | **{r.over_high_risk}** | {r.under} | "
                     f"{100 * r.exact / r.n:.0f}% |")
    lines += ["", "Intended levels (oracle), first scenario: " + dist(results[0].oracle_levels, results[0].n), ""]
    for r in results:
        if r.examples:
            lines += [f"## Over-autonomy examples: {r.name}", "",
                      "| Ticket | True | Predicted | Conf. | Intended | Got | High risk |",
                      "|---|---|---|---|---|---|---|",
                      *[f"| {e['number']} | {e['true']} | {e['pred']} | {e['confidence']:.2f} | {e['intended']} | "
                        f"{e['got']} | {'yes' if e['high_risk'] else ''} |" for e in r.examples], ""]
    return "\n".join(lines)
