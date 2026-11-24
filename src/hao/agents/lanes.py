"""Agent lanes: what happens after the policy engine picks a level (ADR 0008).

    A0  human only      → nothing beyond the triage note; a human owns it end to end
    A1  AI assists      → route to the right group + handoff packet (summary, KB, similar tickets, next step)
    A2  AI drafts       → route + plan + draft, parked for approval; runs under the approver's name
    A3  AI acts         → route + plan + draft, executed now; a sample goes to QA review

Fail-safe everywhere: no playbook, an unusable plan, a draft that fails its guardrails, or any tool error
drops the ticket to the A1 handoff. A failure never results in *more* autonomy.
"""

from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from hao.adapters.base import TicketingAdapter, TicketingError
from hao.agents.drafter import CREDENTIAL, Draft, Drafter, DraftRequest, check_draft
from hao.agents.playbooks import Playbooks
from hao.agents.retrieval import BM25, Doc
from hao.agents.tools import ToolContext, ToolRefused, run_tool
from hao.audit import log as audit
from hao.controller.autonomy import check_demotion, load_caps
from hao.db.models import HaoFeedback, HaoIntakeResult, HaoWorkItem
from hao.intake.redaction import Redactor
from hao.intake.taxonomy import default_taxonomy
from hao.policy.engine import Decision, DecisionInput, decide
from hao.policy.schema import Level, Policy

LANES = {Level.A0: "human", Level.A1: "assist", Level.A2: "draft", Level.A3: "auto"}


class ReviewError(Exception):
    pass


@dataclass
class LaneDeps:
    session_factory: sessionmaker[Session]
    adapter: TicketingAdapter
    policy: Policy
    playbooks: Playbooks
    drafter: Drafter
    redactor: Redactor = field(default_factory=Redactor)
    actor: str = "ai:hao-agent"
    console_url: str = "http://localhost:8002/console"
    rng: random.Random = field(default_factory=random.Random)
    _kb: list[dict] | None = None

    def kb(self) -> list[dict]:
        if self._kb is None:
            try:
                self._kb = self.adapter.list_kb()
            except TicketingError:
                return []  # retrieval is best-effort; don't cache a failure
        return self._kb


# ----------------------------------------------------------------------------- context + planning

def gather_context(deps: LaneDeps, sys_id: str, text: str) -> dict:
    kb_index = BM25([Doc(a["number"], a["short_description"], a["text"], {}) for a in deps.kb()])
    kb = [{"number": d.id, "title": d.title, "text": d.text, "score": s} for d, s in kb_index.search(text, k=2)]
    with deps.session_factory() as session:
        past = session.scalars(select(HaoIntakeResult).order_by(HaoIntakeResult.id.desc()).limit(2000)).all()
        docs = [Doc(p.incident_sys_id, p.redacted_short_description, p.redacted_description,
                    {"number": p.number, "category": p.category}) for p in past]
    similar = [{"number": d.meta["number"], "category": d.meta["category"], "subject": d.title[:80], "score": s}
               for d, s in BM25(docs).search(text, k=3, exclude={sys_id})] if docs else []
    return {"kb": kb, "similar": similar}


def make_plan(deps: LaneDeps, category: str, application: str, text: str) -> tuple[list[dict], str, str]:
    """Returns (plan, package, problem). An empty plan with a problem means: cannot act, hand off."""
    pb = deps.playbooks.playbooks.get(category)
    if pb is None:
        return [], "", f"no playbook for {category}"
    package = next((p for p in deps.playbooks.software_catalog if p.lower() in text.lower()), "")
    plan = []
    for action in pb.actions:
        args = dict(action.args)
        if action.tool.startswith("identity."):
            default = default_taxonomy().categories.get(category)
            args["application"] = application or (default.default_application if default else "")
            if not args["application"]:
                return [], "", "could not identify the application to act on"
        if action.tool == "software.request_install":
            if not package:
                return [], "", "requested software is not in the catalogue"
            args["package"] = package
        plan.append({"tool": action.tool, "args": args})
    return plan, package, ""


# ----------------------------------------------------------------------------- lanes

def run_lane(incident: dict, intake: HaoIntakeResult, decision: Decision, decision_id: int, x: DecisionInput,
             deps: LaneDeps) -> HaoWorkItem:
    sys_id, level = incident["sys_id"], decision.level
    redacted, _, vault = deps.redactor.redact_ticket(incident)
    common = {"incident_sys_id": sys_id, "policy_version": decision.policy_version, "decision_id": decision_id}
    item = HaoWorkItem(incident_sys_id=sys_id, number=incident.get("number", ""), decision_id=decision_id,
                       level=level.value, lane=LANES[level], state="human_only", category=decision.category,
                       context={"decision_input": asdict(x)}, plan=[], draft={}, results=[], error="", reviewer="")

    with deps.session_factory() as session:
        session.add(item)
        session.commit()

        if level == Level.A0:
            audit.append(session, actor=deps.actor, action="lane.human_only", payload={}, **common)
            return item

        _route(deps, session, incident, decision, common)
        ctx = gather_context(deps, sys_id, x.text)
        plan, package, problem = make_plan(deps, decision.category, intake.application, x.text)
        draft = deps.drafter.draft(DraftRequest(
            category=decision.category, level=level.value, application=intake.application, ticket=redacted,
            actions=[p["tool"] for p in plan], kb=ctx["kb"], similar=ctx["similar"],
            playbook=deps.playbooks.playbooks.get(decision.category) if plan else None, package=package,
            signals=intake.signals))
        guard = check_draft(draft, allowed_tokens=set(vault._by_token))
        item.context = {**item.context, **ctx, "guardrail_problems": guard, "plan_problem": problem}
        item.draft = draft.model_dump()
        audit.append(session, actor=f"ai:{draft.drafter}", action="lane.drafted",
                     payload={"drafter": draft.drafter, "model": draft.model, "guardrail_problems": guard,
                              "plan": plan, "plan_problem": problem, "kb": [k["number"] for k in ctx["kb"]]},
                     **common)

        can_act = level >= Level.A2 and plan and not guard
        if not can_act:
            reason = problem or ("draft failed guardrails: " + "; ".join(guard) if guard else "")
            if level >= Level.A2:
                audit.append(session, actor=deps.actor, action="lane.degraded", payload={"reason": reason}, **common)
            return _hand_off(deps, session, item, draft, common, reason=reason if level >= Level.A2 else "")

        item.plan = plan
        if level == Level.A2:
            item.state = "awaiting_approval"
            session.commit()
            audit.append(session, actor=deps.actor, action="lane.awaiting_approval",
                         payload={"work_item": item.id, "plan": plan}, **common)
            _note(deps, sys_id,
                  f"[AI draft · awaiting approval] {deps.console_url}/items/{item.id}\n"
                  f"Planned actions: {', '.join(p['tool'] for p in plan)}\n{draft.handoff_summary}")
            return item

        # A3: act now, as the agent
        ok = _execute(deps, session, item, incident, vault.reveal(draft.reply), vault.reveal(draft.close_notes),
                      level, deps.actor, common)
        if not ok:
            return _hand_off(deps, session, item, draft, common, reason=f"execution failed: {item.error}",
                             keep_state=True)
        sampled = deps.rng.random() < deps.playbooks.qa_sample_rate
        if sampled:
            qa = HaoWorkItem(incident_sys_id=sys_id, number=item.number, decision_id=decision_id, level=level.value,
                             lane="qa", state="qa_pending", category=item.category, plan=plan, draft=item.draft,
                             context={}, results=item.results, error="", reviewer="", parent_id=item.id)
            session.add(qa)
            session.commit()
            audit.append(session, actor=deps.actor, action="qa.sampled", payload={"qa_item": qa.id}, **common)
        _note(deps, sys_id, f"[AI resolved · A3] actions: {', '.join(p['tool'] for p in plan)}. "
                            f"QA sample: {'yes' if sampled else 'no'}. Owner: {decision.owner_name}.")
        return item


def _route(deps: LaneDeps, session: Session, incident: dict, decision: Decision, common: dict) -> None:
    if decision.level < deps.playbooks.routing_min_level:
        return
    current, target = incident.get("assignment_group", ""), decision.assignment_group
    if not target or current == target:
        return
    try:
        deps.adapter.assign(incident["sys_id"], target)
        audit.append(session, actor=deps.actor, action="action.executed",
                     payload={"tool": "incident.assign", "from": current, "to": target}, **common)
    except TicketingError as exc:
        audit.append(session, actor=deps.actor, action="action.failed",
                     payload={"tool": "incident.assign", "error": str(exc)[:300]}, **common)


def _execute(deps: LaneDeps, session: Session, item: HaoWorkItem, incident: dict, reply: str, close_notes: str,
             level: Level, actor: str, common: dict) -> bool:
    adapter = deps.adapter if actor == deps.actor else deps.adapter.with_actor(actor)
    raw = adapter.get_incident(incident["sys_id"]).raw  # fresh, inside the boundary
    ctx = ToolContext(adapter=adapter, incident=raw, level=level, actor=actor, reply=reply, close_notes=close_notes)
    allowed = {a.tool for a in deps.playbooks.playbooks[item.category].actions}
    results = []
    for step in item.plan:
        try:
            out = run_tool(step["tool"], step["args"], ctx, allowed)
            results.append({"tool": step["tool"], "ok": True, "result": out})
            audit.append(session, actor=actor, action="action.executed",
                         payload={"tool": step["tool"], "args": step["args"], "result": out}, **common)
        except (ToolRefused, TicketingError) as exc:
            kind = "action.refused" if isinstance(exc, ToolRefused) else "action.failed"
            results.append({"tool": step["tool"], "ok": False, "error": str(exc)[:300]})
            audit.append(session, actor=actor, action=kind,
                         payload={"tool": step["tool"], "args": step["args"], "error": str(exc)[:300]}, **common)
            item.results, item.state, item.error = results, "failed", f"{step['tool']}: {exc}"[:500]
            session.commit()
            return False
    item.results, item.state = results, "executed"
    session.commit()
    audit.append(session, actor=actor, action="lane.executed", payload={"work_item": item.id}, **common)
    return True


def _hand_off(deps: LaneDeps, session: Session, item: HaoWorkItem, draft: Draft, common: dict, reason: str = "",
              keep_state: bool = False) -> HaoWorkItem:
    if not keep_state:
        item.state = "handed_off"
    item.lane = "assist" if not keep_state else item.lane
    session.commit()
    ctx = item.context
    kb = "; ".join(f"{k['number']} {k['title']}" for k in ctx.get("kb", [])) or "none found"
    similar = ", ".join(f"{s['number']} ({s['category']})" for s in ctx.get("similar", [])) or "none"
    lines = ["[AI handoff packet]" + (f" Could not act: {reason}." if reason else ""),
             f"Summary: {draft.handoff_summary}", f"KB: {kb}", f"Similar tickets: {similar}",
             f"Suggested next step: {draft.next_step}"]
    _note(deps, item.incident_sys_id, "\n".join(lines))
    audit.append(session, actor=deps.actor, action="lane.handed_off", payload={"reason": reason}, **common)
    return item


def _note(deps: LaneDeps, sys_id: str, text: str) -> None:
    try:
        deps.adapter.add_work_note(sys_id, text)
    except TicketingError:
        pass  # the audit trail still has everything; a missing note must not crash the lane


# ----------------------------------------------------------------------------- human review

CREDENTIAL_OR_LINK = re.compile(CREDENTIAL.pattern + r"|https?://(?!([\w-]+\.)*kaveri\.example)", re.I)


def review(deps: LaneDeps, item_id: int, *, reviewer: str, verdict: str, reason_code: str = "",
           reply: str | None = None, note: str = "") -> HaoWorkItem:
    """Approve / edit / reject an A2 draft, or pass / fail a QA sample. Every verdict becomes feedback."""
    if not re.fullmatch(r"[^@\s]+@[^@\s]+", reviewer or ""):
        raise ReviewError("reviewer must be an email address")
    with deps.session_factory() as session:
        item = session.get(HaoWorkItem, item_id)
        if item is None:
            raise ReviewError("work item not found")
        common = {"incident_sys_id": item.incident_sys_id, "decision_id": item.decision_id,
                  "policy_version": deps.policy.version}
        needs_reason = verdict in ("edit", "reject", "fail")
        if needs_reason and reason_code not in deps.playbooks.reason_codes:
            raise ReviewError(f"reason_code must be one of {deps.playbooks.reason_codes}")

        if item.state == "qa_pending" and verdict in ("pass", "fail"):
            item.state, item.reviewer = "qa_done", reviewer
            target = session.get(HaoWorkItem, item.parent_id) if item.parent_id else item
            _feedback(session, target, reviewer, f"qa_{verdict}", reason_code, note)  # evidence about the A3 item
            session.commit()
            audit.append(session, actor=f"user:{reviewer}", action="qa.reviewed",
                         payload={"work_item": item.id, "verdict": verdict, "reason_code": reason_code}, **common)
            if verdict == "fail":
                check_demotion(session, deps.policy, item.category)
            return item
        if item.state != "awaiting_approval" or verdict not in ("approve", "edit", "reject"):
            raise ReviewError(f"cannot {verdict} an item in state {item.state}")

        if verdict == "reject":
            item.state, item.reviewer = "rejected", reviewer
            _feedback(session, item, reviewer, "rejected", reason_code, note)
            session.commit()
            audit.append(session, actor=f"user:{reviewer}", action="review.rejected",
                         payload={"work_item": item.id, "reason_code": reason_code}, **common)
            _note(deps, item.incident_sys_id, f"[AI draft rejected by {reviewer}] reason: {reason_code}. "
                                              "A human will handle this ticket.")
            check_demotion(session, deps.policy, item.category)
            return item

        # approve / edit: re-check the decision under the policy *now*, which may have changed since the draft
        x = DecisionInput(**item.context["decision_input"])
        now = decide(deps.policy, x, load_caps(session))
        if now.level < Level.A2:
            raise ReviewError(f"policy {now.policy_label} now gives {now.level} for this ticket; it can't be executed")
        text = item.draft.get("reply", "") if verdict == "approve" else (reply or "")
        if verdict == "edit" and not text.strip():
            raise ReviewError("an edit needs the reply text")
        if CREDENTIAL_OR_LINK.search(text):
            raise ReviewError("reply contains a credential or a non-kaveri.example link")

        incident = deps.adapter.get_incident(item.incident_sys_id).raw
        _, _, vault = deps.redactor.redact_ticket(incident)  # deterministic: same tokens as when drafted
        item.reviewer = reviewer
        _feedback(session, item, reviewer, "approved" if verdict == "approve" else "edited", reason_code, note)
        session.commit()
        action = "review.approved" if verdict == "approve" else "review.edited"
        audit.append(session, actor=f"user:{reviewer}", action=action,
                     payload={"work_item": item.id, "reason_code": reason_code}, **common)
        notes = vault.reveal(item.draft.get("close_notes", ""))
        ok = _execute(deps, session, item, incident, vault.reveal(text), notes, Level.A2, f"user:{reviewer}", common)
        if verdict == "edit":
            check_demotion(session, deps.policy, item.category)
        _note(deps, item.incident_sys_id,
              f"[AI draft {'approved' if verdict == 'approve' else 'edited and approved'} by {reviewer}] "
              + ("actions executed." if ok else f"execution failed: {item.error}"))
        return item


def _feedback(session: Session, item: HaoWorkItem, reviewer: str, verdict: str, reason_code: str, note: str) -> None:
    session.add(HaoFeedback(decision_id=item.decision_id, work_item_id=item.id, category=item.category,
                            level=item.level, reviewer=reviewer, verdict=verdict, reason_code=reason_code,
                            corrected_category="", corrected_group="", note=note[:1000]))
