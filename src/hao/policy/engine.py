"""Policy engine: turns a classification into an autonomy level, with a full explanation trail.

Order of evaluation (ADR 0006). Each step can only keep or **lower** the level, never raise it:

1. **Start** at the category's ``current_level`` (unknown category → ``unknown_category.level`` = A0)
2. **Application cap:** ``applications[app].max_level`` ("category isn't risk": payroll via SSO is still payroll)
3. **Risk-tier caps:** for the category's tier and the application's tier
4. **Rules**, in file order: ``downgrade`` lowers by N, ``cap`` lowers to a ceiling. Tripwire rules
   (``text_matches``) look at the ticket text itself, so they still fire when the classifier is
   confidently wrong.
5. **Controller demotions**: runtime caps written by the autonomy controller when quality drops
6. **Clamp** to the category's ``max_level``

Because no step raises the level, every safety cap in the policy holds whatever the rules or inputs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from hao.policy.schema import Level, Policy, Rule

LEVEL_NAMES = {
    Level.A0: "Human only",
    Level.A1: "AI assists, human leads",
    Level.A2: "AI drafts, human approves",
    Level.A3: "AI acts",
}
_BY_RANK = {lvl.rank: lvl for lvl in Level}


@dataclass(frozen=True)
class DecisionInput:
    category: str
    application: str = ""
    confidence: float = 0.0
    priority: str = "4"
    requester_is_vip: bool = False
    reopen_count: int = 0
    text: str = ""  # redacted ticket text, for deterministic tripwire rules


@dataclass
class Decision:
    level: Level
    category: str
    risk_tier: str
    owner: str  # owner key from ownership.yaml
    owner_name: str
    assignment_group: str
    policy_version: str
    policy_label: str
    steps: list[dict] = field(default_factory=list)

    @property
    def applied_rules(self) -> list[str]:
        return [s["rule"] for s in self.steps if s.get("rule") and s["to"] != s["from"]]

    def explain(self) -> str:
        changed = [s for s in self.steps if s["to"] != s["from"]]
        why = "; ".join(f"{s['reason']} → {s['to']}" for s in changed) or "category default"
        return f"{self.level.value} ({LEVEL_NAMES[self.level]}). {why}."


def _lower(level: Level, cap: Level) -> Level:
    return cap if cap.rank < level.rank else level


def _matches(rule: Rule, x: DecisionInput) -> bool:
    w = rule.when
    checks = []
    if w.confidence_below is not None:
        checks.append(x.confidence < w.confidence_below)
    if w.priority_in is not None:
        checks.append(str(x.priority) in w.priority_in)
    if w.requester_is_vip is not None:
        checks.append(x.requester_is_vip == w.requester_is_vip)
    if w.reopen_count_at_least is not None:
        checks.append(x.reopen_count >= w.reopen_count_at_least)
    if w.text_matches is not None:
        checks.append(bool(_rx(w.text_matches).search(x.text)))
    return all(checks)


@lru_cache(maxsize=64)
def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I)  # every condition set on the rule must hold


def decide(policy: Policy, x: DecisionInput, caps: dict[str, Level] | None = None) -> Decision:
    """``caps``: runtime demotions from the autonomy controller (category → ceiling). They can only lower."""
    steps: list[dict] = []

    def step(reason: str, before: Level, after: Level, **extra) -> Level:
        steps.append({"reason": reason, "from": before.value, "to": after.value, **extra})
        return after

    cat = policy.categories.get(x.category)
    if cat is None:
        u = policy.unknown_category
        level = step(f"category '{x.category}' not in policy", u.level, u.level)
        owner, group, tier, max_level = u.owner, u.assignment_group, "unknown", u.level
    else:
        level = step(f"category {x.category} current_level", cat.current_level, cat.current_level)
        owner, group, tier, max_level = cat.owner, cat.assignment_group, cat.risk_tier, cat.max_level

    app = policy.applications.get(x.application)
    if app is not None:
        level = step(f"application {x.application} cap", level, _lower(level, app.max_level))
        level = step(f"application risk tier {app.risk_tier}", level,
                     _lower(level, policy.risk_tiers[app.risk_tier].max_level))
    if tier in policy.risk_tiers:
        level = step(f"category risk tier {tier}", level, _lower(level, policy.risk_tiers[tier].max_level))

    for rule in policy.rules:
        if not _matches(rule, x):
            continue
        if rule.effect.downgrade is not None:
            target = _BY_RANK[max(0, level.rank - rule.effect.downgrade)]
        else:
            target = _lower(level, rule.effect.cap)
        level = step(f"rule {rule.id}", level, target, rule=rule.id)

    if caps and x.category in caps:
        level = step("controller demotion", level, _lower(level, caps[x.category]), rule="controller-demotion")
    if caps and "*" in caps:
        level = step("kill switch", level, _lower(level, caps["*"]), rule="kill-switch")

    level = step("category max_level", level, _lower(level, max_level))

    return Decision(
        level=level,
        category=x.category if cat is not None else "unknown",
        risk_tier=tier,
        owner=owner,
        owner_name=policy.owners[owner].name,
        assignment_group=group,
        policy_version=policy.version,
        policy_label=policy.version_label,
        steps=steps,
    )
