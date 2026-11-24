"""Autonomy controller: evidence in, autonomy changes out (ADR 0010).

Evidence is counted **per work item**, from human-verified outcomes only:

* positive: ``approved`` (A2 draft sent as-is) or ``qa_pass`` (A3 sample judged correct)
* negative: ``edited``, ``rejected``, ``qa_fail``, or ``reopened`` (user reopened an AI-resolved ticket)
* an edit whose reason is *minor* (``controller.minor_reasons``, e.g. tone) still counts as correct work:
  the action was right, the wording was polished
* An item with any negative signal is negative, even if it was approved first.
* Only evidence gathered **since the category's level last changed** counts, so one bad week can't
  demote a category twice, and A2-era approvals can't mask A3-era mistakes.
* Precision uses only items a human verified (every A2 review, the random A3 QA sample). Reopens of
  unsampled A3 work are measured as a **reopen rate over all executions** instead, so negatives from
  100% of the work are never compared with positives from a 20% sample.

Asymmetric by design:

* **Demotion is automatic and immediate.** It is written as a runtime override that the policy engine
  applies as a cap, so no deploy is needed. Triggered by an ``unsafe`` verdict at any level, or **at A3**
  by QA-sample precision < ``demote_precision`` (≥ ``demote_min_samples`` samples) or a reopen rate
  above ``max_reopen_rate`` across all executions. At A2 a human
  already approves every action, so low precision there blocks promotion rather than demoting. Demoting
  A2 → A1 would remove oversight *and* stop the reviews that produce the evidence needed to recover.
* **Promotion is only ever proposed.** The controller writes the exact ``autonomy.yaml`` change; the
  category owner applies it (``hao controller apply``) and it ships as a reviewed policy PR.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from hao import clock
from hao.audit import log as audit
from hao.db.models import HaoAutonomyChange, HaoAutonomyOverride, HaoFeedback, HaoWorkItem
from hao.policy.schema import Level, Policy

POSITIVE = {"approved", "qa_pass"}
NEGATIVE = {"edited", "rejected", "qa_fail", "reopened"}
VERIFIED = {"approved", "edited", "rejected", "qa_pass", "qa_fail"}  # a human looked at the item
ACTOR = "system:autonomy-controller"
GLOBAL = "*"  # override key for the kill switch: applies to every category
SPONSOR = "it-ops-head"  # ownership.yaml key of the person who can switch the kill switch back off
_BY_RANK = {lvl.rank: lvl for lvl in Level}


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@dataclass
class CategoryEvidence:
    category: str
    items: int = 0
    positive: int = 0
    negative: int = 0
    verdicts: dict[str, int] = field(default_factory=dict)
    reasons: dict[str, int] = field(default_factory=dict)
    unsafe: int = 0
    executions: int = 0  # A3 executions by the agent (the denominator for reopens)
    reopened_executions: int = 0

    @property
    def precision(self) -> float | None:
        """Share of *verified* items that were right: every A2 review, or the random A3 QA sample."""
        return self.positive / self.items if self.items else None

    @property
    def reopen_rate(self) -> float | None:
        return self.reopened_executions / self.executions if self.executions else None


def load_caps(session: Session) -> dict[str, Level]:
    caps: dict[str, Level] = {}
    for o in session.scalars(select(HaoAutonomyOverride).where(HaoAutonomyOverride.active)):
        lvl = Level(o.cap)
        caps[o.category] = min(caps.get(o.category, lvl), lvl, key=lambda x: x.rank)
    return caps


def effective_level(policy: Policy, caps: dict[str, Level], category: str) -> Level:
    level = policy.categories[category].current_level
    for key in (category, GLOBAL):
        if key in caps and caps[key] < level:
            level = caps[key]
    return level


def level_changed_at(session: Session) -> dict[str, datetime]:
    """When each category's level last changed. Evidence from before that belongs to a different level."""
    out: dict[str, datetime] = {}
    for o in session.scalars(select(HaoAutonomyOverride)):
        for t in (o.created_at, o.cleared_at):
            if t and _utc(t) > out.get(o.category, datetime.min.replace(tzinfo=UTC)):
                out[o.category] = _utc(t)
    for ch in session.scalars(select(HaoAutonomyChange).where(HaoAutonomyChange.status == "applied")):
        if ch.applied_at and _utc(ch.applied_at) > out.get(ch.category, datetime.min.replace(tzinfo=UTC)):
            out[ch.category] = _utc(ch.applied_at)
    return out


def evidence(session: Session, policy: Policy, as_of: datetime | None = None,
             current_level_only: bool = True) -> dict[str, CategoryEvidence]:
    as_of = as_of or clock.now()
    since = as_of - timedelta(days=policy.controller.window_days)
    changed = level_changed_at(session) if current_level_only else {}
    rows = session.scalars(select(HaoFeedback).order_by(HaoFeedback.id)).all()
    per_item: dict[tuple[str, int], list[HaoFeedback]] = defaultdict(list)
    for f in rows:
        t = _utc(f.created_at)
        if f.category and since <= t <= as_of and t > changed.get(f.category, since - timedelta(seconds=1)):
            per_item[(f.category, f.work_item_id or -f.id)].append(f)

    minor = set(policy.controller.minor_reasons)
    out: dict[str, CategoryEvidence] = {c: CategoryEvidence(c) for c in policy.categories}

    # Every A3 execution in scope, so reopens are measured against all of them, not just the ones we saw.
    executed: dict[str, set[int]] = defaultdict(set)
    for w in session.scalars(select(HaoWorkItem).where(HaoWorkItem.lane == "auto")):
        t = _utc(w.created_at)
        if since <= t <= as_of and t > changed.get(w.category, since - timedelta(seconds=1)):
            executed[w.category].add(w.id)
    for cat, ids in executed.items():
        out.setdefault(cat, CategoryEvidence(cat)).executions = len(ids)

    for (cat, item_id), fbs in per_item.items():
        ev = out.setdefault(cat, CategoryEvidence(cat))
        if item_id in executed.get(cat, ()) and any(f.verdict == "reopened" for f in fbs):
            ev.reopened_executions += 1
        verified = any(f.verdict in VERIFIED for f in fbs)
        if not verified:
            # A reopen on unsampled A3 work counts in the reopen rate, not in precision. Counting it there
            # would compare negatives from 100% of executions against positives from a 20% sample.
            for f in fbs:
                ev.verdicts[f.verdict] = ev.verdicts.get(f.verdict, 0) + 1
            continue
        minor_edit = [f for f in fbs if f.verdict == "edited" and f.reason_code in minor]
        negative = [f for f in fbs if f.verdict in NEGATIVE and f not in minor_edit]
        ev.items += 1
        if negative:
            ev.negative += 1
        else:
            ev.positive += 1
        for f in fbs:
            ev.verdicts[f.verdict] = ev.verdicts.get(f.verdict, 0) + 1
            if f.reason_code:
                ev.reasons[f.reason_code] = ev.reasons.get(f.reason_code, 0) + 1
            ev.unsafe += f.reason_code == "unsafe"
    return out


@dataclass
class ControllerRun:
    demoted: list[dict] = field(default_factory=list)
    proposed: list[dict] = field(default_factory=list)
    evidence: dict[str, CategoryEvidence] = field(default_factory=dict)


def _evidence_dict(ev: CategoryEvidence, policy: Policy) -> dict:
    return {"items": ev.items, "positive": ev.positive, "negative": ev.negative,
            "precision": None if ev.precision is None else round(ev.precision, 4), "verdicts": ev.verdicts,
            "executions": ev.executions, "reopened_executions": ev.reopened_executions,
            "reasons": ev.reasons, "window_days": policy.controller.window_days}


def check_demotion(session: Session, policy: Policy, category: str, as_of: datetime | None = None) -> dict | None:
    """Run after every negative signal. Returns the demotion written, if any."""
    if category not in policy.categories:
        return None
    ev = evidence(session, policy, as_of).get(category)
    if ev is None:
        return None
    caps = load_caps(session)
    current = effective_level(policy, caps, category)
    if current == Level.A0:
        return None
    c = policy.controller
    reason = ""
    if ev.unsafe:
        reason = f"{ev.unsafe} verdict(s) marked unsafe"
    elif current == Level.A3:
        # Only A3 loses autonomy for quality. At A2 a human already approves every action, so poor precision
        # there blocks promotion instead; demoting to A1 would also stop the reviews that are the only way back.
        if ev.items >= c.demote_min_samples and ev.precision is not None and ev.precision < c.demote_precision:
            reason = f"QA precision {ev.precision:.1%} < {c.demote_precision:.0%} over {ev.items} sampled items"
        elif (ev.executions >= c.reopen_min_executions and ev.reopen_rate is not None
              and ev.reopen_rate > c.max_reopen_rate):
            reason = (f"reopen rate {ev.reopen_rate:.1%} > {c.max_reopen_rate:.0%} over "
                      f"{ev.executions} executions")
    if not reason:
        return None
    new = _BY_RANK[current.rank - 1]
    o = HaoAutonomyOverride(category=category, cap=new.value, reason=reason, evidence=_evidence_dict(ev, policy))
    session.add(o)
    session.commit()
    result = {"category": category, "from": current.value, "to": new.value, "reason": reason, "override": o.id}
    audit.append(session, incident_sys_id="", actor=ACTOR, action="autonomy.demoted", payload=result,
                 policy_version=policy.version)
    return result


def run(session: Session, policy: Policy, as_of: datetime | None = None) -> ControllerRun:
    """Scheduled run (e.g. nightly): demotion checks for every category, then promotion proposals."""
    out = ControllerRun()
    for cat in policy.categories:
        d = check_demotion(session, policy, cat, as_of)
        if d:
            out.demoted.append(d)
    out.evidence = evidence(session, policy, as_of)
    caps = load_caps(session)
    c = policy.controller
    open_props = {p.category for p in session.scalars(select(HaoAutonomyChange)
                                                      .where(HaoAutonomyChange.status == "proposed"))}
    for cat, ev in out.evidence.items():
        spec = policy.categories.get(cat)
        if (spec is None or cat in caps or GLOBAL in caps or cat in open_props
                or spec.current_level.rank >= spec.max_level.rank):
            continue
        if ev.items >= c.min_samples and ev.precision is not None and ev.precision >= c.promote_precision:
            to = _BY_RANK[spec.current_level.rank + 1]
            patch = (f"policy/autonomy.yaml  categories.{cat}.current_level: "
                     f"{spec.current_level.value} → {to.value}")
            ch = HaoAutonomyChange(category=cat, from_level=spec.current_level.value, to_level=to.value,
                                   direction="promote", evidence=_evidence_dict(ev, policy), status="proposed",
                                   patch=patch)
            session.add(ch)
            session.commit()
            item = {"proposal": ch.id, "category": cat, "from": ch.from_level, "to": ch.to_level,
                    "precision": round(ev.precision, 4), "items": ev.items,
                    "owner": policy.owners[spec.owner].name}
            out.proposed.append(item)
            audit.append(session, incident_sys_id="", actor=ACTOR, action="autonomy.promotion_proposed",
                         payload=item, policy_version=policy.version)
    return out


# ----------------------------------------------------------------------------- owner actions

class ControllerError(Exception):
    pass


def apply_promotion(session: Session, policy: Policy, proposal_id: int, approved_by: str,
                    policy_dir: Path | None = None) -> HaoAutonomyChange:
    """Owner approves a proposal. Edits autonomy.yaml in place (for the PR) and records who approved it."""
    ch = session.get(HaoAutonomyChange, proposal_id)
    if ch is None or ch.status != "proposed":
        raise ControllerError("no open proposal with that id")
    spec = policy.categories[ch.category]
    owner = policy.owners[spec.owner]
    if approved_by.lower() != owner.email.lower():
        raise ControllerError(f"only the category owner ({owner.email}) can approve this promotion")
    if policy_dir is not None:
        path = policy_dir / "autonomy.yaml"
        text = path.read_text()
        pattern = re.compile(rf"^(\s*{re.escape(ch.category)}:\s*\{{.*?current_level:\s*){ch.from_level}\b", re.M)
        new_text, n = pattern.subn(rf"\g<1>{ch.to_level}", text)
        if n != 1:
            raise ControllerError(f"could not find {ch.category} current_level {ch.from_level} in {path}")
        path.write_text(new_text)
    ch.status, ch.approved_by, ch.applied_at = "applied", approved_by, clock.now()
    session.commit()
    audit.append(session, incident_sys_id="", actor=f"user:{approved_by}", action="autonomy.promotion_approved",
                 payload={"proposal": ch.id, "category": ch.category, "from": ch.from_level, "to": ch.to_level},
                 policy_version=policy.version)
    return ch


def clear_override(session: Session, policy: Policy, category: str, cleared_by: str, note: str = "") -> int:
    """Owner lifts a demotion after fixing the cause. Returns the number of overrides cleared."""
    owner = policy.owners[policy.categories[category].owner]
    if cleared_by.lower() != owner.email.lower():
        raise ControllerError(f"only the category owner ({owner.email}) can lift this demotion")
    rows = session.scalars(select(HaoAutonomyOverride).where(HaoAutonomyOverride.category == category,
                                                            HaoAutonomyOverride.active)).all()
    for o in rows:
        o.active, o.cleared_by, o.cleared_at = False, cleared_by, clock.now()
    session.commit()
    if rows:
        audit.append(session, incident_sys_id="", actor=f"user:{cleared_by}", action="autonomy.demotion_cleared",
                     payload={"category": category, "overrides": [o.id for o in rows], "note": note},
                     policy_version=policy.version)
    return len(rows)


def status(session: Session, policy: Policy, as_of: datetime | None = None) -> list[dict]:
    ev = evidence(session, policy, as_of)
    caps = load_caps(session)
    props = {p.category: p for p in session.scalars(select(HaoAutonomyChange)
                                                    .where(HaoAutonomyChange.status == "proposed"))}
    rows = []
    for cat, spec in policy.categories.items():
        e = ev.get(cat, CategoryEvidence(cat))
        rows.append({
            "category": cat, "policy_level": spec.current_level.value, "max_level": spec.max_level.value,
            "effective_level": effective_level(policy, caps, cat).value, "items": e.items,
            "precision": None if e.precision is None else round(e.precision, 4),
            "verdicts": dict(Counter(e.verdicts)), "demoted": cat in caps,
            "proposal": props[cat].id if cat in props else None, "owner": policy.owners[spec.owner].name,
        })
    return rows


# ----------------------------------------------------------------------------- kill switch

def kill_switch_state(session: Session) -> dict | None:
    o = session.scalars(select(HaoAutonomyOverride).where(HaoAutonomyOverride.category == GLOBAL,
                                                          HaoAutonomyOverride.active)
                        .order_by(HaoAutonomyOverride.id.desc()).limit(1)).first()
    if o is None:
        return None
    return {"cap": o.cap, "reason": o.reason, "by": o.created_by, "since": _utc(o.created_at).isoformat()}


def kill_switch_on(session: Session, policy: Policy, by: str, reason: str, cap: str = "A1") -> dict:
    """Any accountable owner can stop the line. AI keeps triaging and writing handoff packets (A1) or
    stops entirely (A0); nothing is executed, and pending approvals can no longer run."""
    owners = {o.email.lower() for o in policy.owners.values()}
    if by.lower() not in owners:
        raise ControllerError("only a named owner in ownership.yaml can pull the kill switch")
    if Level(cap) > Level.A1:
        raise ControllerError("the kill switch caps at A1 or A0")
    if not reason.strip():
        raise ControllerError("a reason is required")
    if kill_switch_state(session):
        raise ControllerError("the kill switch is already on")
    o = HaoAutonomyOverride(category=GLOBAL, cap=cap, reason=reason, evidence={}, created_by=by)
    session.add(o)
    session.commit()
    audit.append(session, incident_sys_id="", actor=f"user:{by}", action="autonomy.kill_switch_on",
                 payload={"cap": cap, "reason": reason}, policy_version=policy.version)
    return kill_switch_state(session)


def kill_switch_off(session: Session, policy: Policy, by: str, note: str = "") -> None:
    """Only the sponsor can restart AI actions: stopping is easy, restarting is a decision."""
    sponsor = policy.owners[SPONSOR].email.lower()
    if by.lower() != sponsor:
        raise ControllerError(f"only the sponsor ({sponsor}) can switch the kill switch off")
    rows = session.scalars(select(HaoAutonomyOverride).where(HaoAutonomyOverride.category == GLOBAL,
                                                            HaoAutonomyOverride.active)).all()
    if not rows:
        raise ControllerError("the kill switch is not on")
    for o in rows:
        o.active, o.cleared_by, o.cleared_at = False, by, clock.now()
    session.commit()
    audit.append(session, incident_sys_id="", actor=f"user:{by}", action="autonomy.kill_switch_off",
                 payload={"note": note}, policy_version=policy.version)
