"""Platform service: receives the client system's webhooks and runs intake.

Guarantees, because the client's webhooks are at-least-once and our own writes trigger new events:
1. **Authenticity:** HMAC signature is verified before the body is parsed.
2. **Idempotency:** each delivery id is processed once; each incident is triaged once.
3. **No feedback loops:** our work note triggers ``incident.updated``, which intake ignores.
4. **Fast ack:** we answer 202 immediately and process in the background, so the client doesn't retry.

Run:  hao platform serve   (then `hao platform subscribe`)
"""

from __future__ import annotations

import json
import os
import random
import time
from collections.abc import Callable
from datetime import datetime
from urllib.parse import parse_qs

from fastapi import BackgroundTasks, Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from hao import clock, obs
from hao.adapters.base import TicketingAdapter
from hao.agents.drafter import Drafter, TemplateDrafter, make_drafter
from hao.agents.lanes import LaneDeps, ReviewError, review, sweep_expired
from hao.agents.playbooks import Playbooks, load_playbooks
from hao.audit import log as audit
from hao.config import Settings, get_settings
from hao.controller import autonomy as ctl
from hao.dashboard import metrics as dashboard
from hao.dashboard import render
from hao.dashboard.metrics import load_value
from hao.db.models import HaoAuditEvent, HaoDecision, HaoIntakeResult, HaoProcessedEvent, HaoWorkItem
from hao.db.session import init_db, make_engine, make_session_factory
from hao.intake.classifiers import Classifier, make_classifier
from hao.intake.redaction import Redactor
from hao.mock_ticketing.webhooks import verify
from hao.platform import console
from hao.platform.auth import current_user, require_admin, require_viewer
from hao.platform.intake import process_created, process_reopen
from hao.policy.schema import Policy, load_policy


def create_app(settings: Settings, adapter_factory: Callable[[], TicketingAdapter], classifier: Classifier,
               redactor: Redactor | None = None, policy: Policy | None = None, drafter: Drafter | None = None,
               playbooks: Playbooks | None = None, lanes: bool = True, rng: random.Random | None = None) -> FastAPI:
    obs.setup_logging()
    engine = make_engine(settings.platform_database_url or settings.database_url)
    init_db(engine, prefix="hao_")
    session_factory = make_session_factory(engine)
    redactor = redactor or Redactor()
    policy = policy or load_policy(settings.policy_dir)
    playbooks = playbooks or load_playbooks(settings.policy_dir)
    drafter = drafter or TemplateDrafter()
    rng = rng or random.Random()
    problems = playbooks.check_against(policy)
    if problems:
        raise ValueError(f"playbooks do not match policy: {problems}")
    app = FastAPI(title="Human+AI operating model: platform", version="0.4.0")
    app.state.session_factory = session_factory
    app.state.settings = settings
    viewer = [Depends(require_viewer)]
    admin = [Depends(require_admin)]

    def set_policy(new: Policy, actor: str = "system:platform") -> None:
        """Swap the live policy (after a merged policy PR). Recorded once per version change."""
        problems_ = playbooks.check_against(new)
        if problems_:
            raise ValueError(f"playbooks do not match policy: {problems_}")
        app.state.policy = new
        with session_factory() as session:
            last = session.scalar(select(HaoAuditEvent).where(HaoAuditEvent.action == "policy.loaded")
                                  .order_by(HaoAuditEvent.id.desc()).limit(1))
            if last is None or last.policy_version != new.version:
                audit.append(session, incident_sys_id="", actor=actor, action="policy.loaded",
                             payload={"label": new.version_label,
                                      "levels": {k: c.current_level.value for k, c in new.categories.items()}},
                             policy_version=new.version)

    app.state.set_policy = set_policy
    set_policy(policy)

    agent_actor = settings.platform_actor.replace("intake", "agent")

    def lane_deps(adapter: TicketingAdapter) -> LaneDeps:
        return LaneDeps(session_factory=session_factory, adapter=adapter.with_actor(agent_actor),
                        policy=app.state.policy,
                        playbooks=playbooks, drafter=drafter, redactor=redactor, actor=agent_actor,
                        console_url=settings.public_console_url, rng=rng)

    def run_intake(incident: dict, delivery_id: str) -> None:
        adapter = adapter_factory()
        token = obs.bind(sys_id=incident.get("sys_id"), number=incident.get("number"), delivery_id=delivery_id)
        start = time.perf_counter()
        try:
            res = process_created(incident, delivery_id=delivery_id, session_factory=session_factory,
                                  adapter=adapter, classifier=classifier, redactor=redactor,
                                  policy=app.state.policy, actor=settings.platform_actor,
                                  lane_deps=lane_deps(adapter) if lanes else None)
            if res is not None:
                with session_factory() as session:
                    item = session.scalars(select(HaoWorkItem).where(HaoWorkItem.incident_sys_id == res.incident_sys_id)
                                           .order_by(HaoWorkItem.id)).first()
                obs.log("intake.done", category=res.category, confidence=res.confidence, classifier=res.classifier,
                        autonomy_level=item.level if item else None, lane_state=item.state if item else None,
                        pii_entities=sum(res.pii_entities.values()), ms=int((time.perf_counter() - start) * 1000))
        except Exception as exc:
            obs.logger.exception("intake.failed", extra={"fields": {"error": str(exc)[:300]}})
            raise
        finally:
            obs.unbind(token)
            if hasattr(adapter, "close"):
                adapter.close()

    @app.get("/health")
    def health():
        return {"status": "ok", "classifier": classifier.name, "policy": app.state.policy.version_label,
                "policy_version": app.state.policy.version}

    @app.post("/webhooks/ticketing", status_code=202)
    async def receive(
        request: Request,
        tasks: BackgroundTasks,
        x_kaveri_signature: str = Header(""),
        x_kaveri_delivery: str = Header(""),
        x_kaveri_event: str = Header(""),
    ):
        body = await request.body()
        secrets_ = [x for x in (settings.webhook_secret, settings.webhook_secret_previous) if x]
        if not any(verify(body, x_kaveri_signature, sec) for sec in secrets_):  # previous: during rotation
            obs.log("webhook.rejected", reason="invalid signature", delivery_id=x_kaveri_delivery)
            raise HTTPException(401, "invalid signature")
        payload = json.loads(body)
        try:
            sent = datetime.fromisoformat(payload.get("sent_at", ""))
        except ValueError:
            raise HTTPException(401, "missing or invalid sent_at") from None
        if abs((clock.now() - sent).total_seconds()) > settings.webhook_max_age_s:
            obs.log("webhook.rejected", reason="stale", delivery_id=x_kaveri_delivery)
            raise HTTPException(401, "stale delivery (possible replay)")
        incident = payload.get("incident", {})
        event = payload.get("event", x_kaveri_event)
        if not x_kaveri_delivery or not incident.get("sys_id"):
            raise HTTPException(400, "missing delivery id or incident")

        reopened = (event == "incident.updated" and str(incident.get("state")) in ("1", "2")
                    and int(incident.get("reopen_count") or 0) > 0)
        with session_factory() as session:
            status = "accepted" if event == "incident.created" or reopened else "ignored"
            session.add(HaoProcessedEvent(delivery_id=x_kaveri_delivery, event=event,
                                          incident_sys_id=incident["sys_id"], status=status,
                                          detail="" if status == "accepted"
                                          else "only created and reopened events are processed"))
            try:
                session.commit()
            except IntegrityError:
                return {"status": "duplicate"}  # at-least-once delivery: already seen this one

        if event == "incident.created":
            tasks.add_task(run_intake, incident, x_kaveri_delivery)
            return {"status": "accepted"}
        if reopened:
            tasks.add_task(process_reopen, incident, delivery_id=x_kaveri_delivery, session_factory=session_factory,
                           policy=app.state.policy)
            return {"status": "accepted"}
        return {"status": "ignored"}

    @app.get("/intake/{sys_id}", dependencies=viewer)
    def get_intake(sys_id: str):
        with session_factory() as session:
            r = session.scalar(select(HaoIntakeResult).where(HaoIntakeResult.incident_sys_id == sys_id))
            if r is None:
                raise HTTPException(404, "not triaged")
            return {c.name: getattr(r, c.name) for c in HaoIntakeResult.__table__.columns}

    @app.get("/decisions/{sys_id}", dependencies=viewer)
    def get_decision(sys_id: str):
        """Everything audit asks for: what was decided, by what logic, on what data, by whom, when."""
        with session_factory() as session:
            d = session.scalar(select(HaoDecision).where(HaoDecision.incident_sys_id == sys_id)
                               .order_by(HaoDecision.id.desc()).limit(1))
            if d is None:
                raise HTTPException(404, "no decision")
            return {"decision": {c.name: getattr(d, c.name) for c in HaoDecision.__table__.columns},
                    "audit_trail": audit.trail(session, sys_id)}

    @app.get("/audit/verify", dependencies=viewer)
    def audit_verify(expected_head: str | None = None):
        r = audit.verify(session_factory, expected_head)
        return {"ok": r.ok, "events": r.events, "head_hash": r.head_hash, "broken_at_id": r.broken_at_id,
                "reason": r.reason}

    # ------------------------------------------------------------------ work items + human review

    def item_dict(i: HaoWorkItem) -> dict:
        return {c.name: getattr(i, c.name) for c in HaoWorkItem.__table__.columns}

    @app.get("/work-items", dependencies=viewer)
    def list_items(state: str = "", lane: str = ""):
        with session_factory() as session:
            stmt = select(HaoWorkItem).order_by(HaoWorkItem.id.desc()).limit(500)
            if state:
                stmt = stmt.where(HaoWorkItem.state == state)
            if lane:
                stmt = stmt.where(HaoWorkItem.lane == lane)
            return [item_dict(i) for i in session.scalars(stmt)]

    @app.get("/work-items/{item_id}", dependencies=viewer)
    def get_item(item_id: int):
        with session_factory() as session:
            i = session.get(HaoWorkItem, item_id)
            if i is None:
                raise HTTPException(404, "not found")
            return item_dict(i)

    def do_review(item_id: int, data: dict, user: str | None) -> HaoWorkItem:
        adapter = adapter_factory()
        try:
            # With SSO the reviewer is whoever is signed in; a reviewer field in the request is ignored.
            return review(lane_deps(adapter), item_id, reviewer=user or data.get("reviewer", ""),
                          verdict=data.get("verdict", ""), reason_code=data.get("reason_code", ""),
                          reply=data.get("reply"), note=data.get("note", ""))
        except ReviewError as exc:
            raise HTTPException(409, str(exc)) from exc
        finally:
            if hasattr(adapter, "close"):
                adapter.close()

    @app.post("/work-items/{item_id}/review")
    def review_item(item_id: int, body: dict = Body(...), user: str | None = Depends(current_user)):
        return item_dict(do_review(item_id, body, user))

    @app.get("/console", response_class=HTMLResponse)
    def console_index(user: str | None = Depends(current_user)):
        with session_factory() as session:
            pending = session.scalars(select(HaoWorkItem).where(HaoWorkItem.state.in_(["awaiting_approval",
                                                                                         "qa_pending"]))
                                      .order_by(HaoWorkItem.id)).all()
            recent = session.scalars(select(HaoWorkItem).order_by(HaoWorkItem.id.desc()).limit(25)).all()
            return console.index(pending, recent, user=user, kill_switch=_kill_switch_state())

    @app.get("/console/items/{item_id}", response_class=HTMLResponse)
    def console_item(item_id: int, error: str = "", user: str | None = Depends(current_user)):
        with session_factory() as session:
            i = session.get(HaoWorkItem, item_id)
            if i is None:
                raise HTTPException(404, "not found")
            d = session.get(HaoDecision, i.decision_id)
            adapter = adapter_factory()
            try:  # reviewers are Kaveri staff: they see the original ticket, read live from the client system
                incident = adapter.get_incident(i.incident_sys_id).raw
            finally:
                if hasattr(adapter, "close"):
                    adapter.close()
            _, _, vault = redactor.redact_ticket(incident)
            return console.item(i, d, incident, vault, playbooks.reason_codes, error, user=user)

    @app.post("/console/items/{item_id}")
    async def console_submit(item_id: int, request: Request, user: str | None = Depends(current_user)):
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
        try:
            do_review(item_id, form, user)
        except HTTPException as exc:
            return RedirectResponse(f"/console/items/{item_id}?error={exc.detail}", status_code=303)
        return RedirectResponse("/console", status_code=303)

    # ------------------------------------------------------------------ autonomy controller

    def _kill_switch_state() -> dict | None:
        with session_factory() as session:
            return ctl.kill_switch_state(session)

    @app.get("/killswitch", dependencies=viewer)
    def killswitch_status():
        return {"on": _kill_switch_state() is not None, "state": _kill_switch_state()}

    @app.post("/killswitch")
    def killswitch_set(body: dict = Body(...), user: str | None = Depends(current_user)):
        """Body: {"on": true, "reason": "...", "cap": "A1"} or {"on": false}. Identity from SSO (dev: "by")."""
        by = user or str(body.get("by", ""))
        with session_factory() as session:
            try:
                if body.get("on"):
                    ctl.kill_switch_on(session, app.state.policy, by, str(body.get("reason", "")),
                                       str(body.get("cap", "A1")))
                else:
                    ctl.kill_switch_off(session, app.state.policy, by, str(body.get("note", "")))
            except ctl.ControllerError as exc:
                raise HTTPException(409, str(exc)) from exc
        return killswitch_status()

    @app.post("/sweep", dependencies=admin)
    def sweep():
        """Expire A2 drafts nobody reviewed in time (call from a scheduler every 15 minutes)."""
        adapter = adapter_factory()
        try:
            return {"expired": sweep_expired(lane_deps(adapter))}
        finally:
            if hasattr(adapter, "close"):
                adapter.close()

    @app.get("/controller/status", dependencies=viewer)
    def controller_status():
        with session_factory() as session:
            return ctl.status(session, app.state.policy)

    @app.post("/controller/run", dependencies=admin)
    def controller_run():
        """Nightly job endpoint: demotion checks for every category, then promotion proposals."""
        with session_factory() as session:
            r = ctl.run(session, app.state.policy)
            return {"demoted": r.demoted, "proposed": r.proposed}

    @app.post("/policy/reload", dependencies=admin)
    def policy_reload():
        """Load policy/ from disk after a policy PR is merged and deployed."""
        set_policy(load_policy(settings.policy_dir), actor="system:policy-reload")
        return {"policy": app.state.policy.version_label, "policy_version": app.state.policy.version}

    # ------------------------------------------------------------------ leadership dashboard

    value = load_value(settings.policy_dir)

    @app.get("/dashboard.json", dependencies=viewer)
    def dashboard_json(days: int = 28):
        return dashboard.compute(session_factory, app.state.policy, value, days=days)

    @app.get("/dashboard", response_class=HTMLResponse, dependencies=viewer)
    def dashboard_html(days: int = 28):
        return render.html(dashboard.compute(session_factory, app.state.policy, value, days=days))

    @app.get("/metrics/prometheus", response_class=PlainTextResponse)
    def prometheus():
        """Aggregate counts only, no ticket data. Expose on the internal network for Prometheus to scrape."""
        lines = []

        def metric(name: str, help_: str, kind: str, samples: list[tuple[dict, float]]):
            lines.extend([f"# HELP {name} {help_}", f"# TYPE {name} {kind}"])
            for labels, v in samples:
                lab = ",".join(f'{k}="{v_}"' for k, v_ in labels.items())
                lines.append(f"{name}{{{lab}}} {v}" if lab else f"{name} {v}")

        with session_factory() as session:
            def grouped(col):
                return session.execute(select(col, func.count()).group_by(col)).all()

            metric("hao_decisions_total", "Autonomy decisions by level", "counter",
                   [({"level": lvl}, n) for lvl, n in grouped(HaoDecision.level)])
            metric("hao_work_items", "Work items by state", "gauge",
                   [({"state": st}, n) for st, n in grouped(HaoWorkItem.state)])
            metric("hao_webhook_events_total", "Inbound webhook events by processing status", "counter",
                   [({"status": st}, n) for st, n in grouped(HaoProcessedEvent.status)])
            metric("hao_kill_switch_on", "1 if the kill switch is on", "gauge",
                   [({}, 1 if ctl.kill_switch_state(session) else 0)])
            metric("hao_active_demotions", "Active controller demotions", "gauge",
                   [({}, sum(1 for k in ctl.load_caps(session) if k != ctl.GLOBAL))])
            tokens = session.scalar(select(func.coalesce(func.sum(HaoIntakeResult.input_tokens
                                                                  + HaoIntakeResult.output_tokens), 0)))
            metric("hao_classifier_tokens_total", "LLM tokens used by the classifier", "counter", [({}, tokens)])
        metric("hao_audit_chain_ok", "1 if the audit hash chain verifies", "gauge",
               [({}, 1 if audit.verify(session_factory).ok else 0)])
        return "\n".join(lines) + "\n"

    @app.get("/metrics", dependencies=viewer)
    def metrics():
        with session_factory() as session:
            events = dict(session.execute(select(HaoProcessedEvent.status, func.count())
                                          .group_by(HaoProcessedEvent.status)).all())
            cats = dict(session.execute(select(HaoIntakeResult.category, func.count())
                                        .group_by(HaoIntakeResult.category)).all())
            avg_conf = session.scalar(select(func.avg(HaoIntakeResult.confidence)))
            levels = dict(session.execute(select(HaoDecision.level, func.count()).group_by(HaoDecision.level)).all())
            states = dict(session.execute(select(HaoWorkItem.state, func.count()).group_by(HaoWorkItem.state)).all())
        return {"events": events, "categories": cats, "levels": levels, "work_items": states,
                "avg_confidence": avg_conf}

    return app


def create_app_from_env() -> FastAPI:
    from hao.adapters.servicenow import ServiceNowAdapter

    settings = get_settings()
    return create_app(settings, lambda: ServiceNowAdapter.from_settings(settings),
                      make_classifier(os.environ.get("HAO_CLASSIFIER", "keyword")),
                      drafter=make_drafter(os.environ.get("HAO_DRAFTER", "template")))
