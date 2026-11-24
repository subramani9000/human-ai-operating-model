"""Simulated pilot: weeks of synthetic traffic through the *whole* system, with simulated humans.

Everything is the real code path: mock client system → signed webhooks → intake → policy → lanes →
review API → reopen webhooks → autonomy controller → owner approvals → policy reload. Only the people
are simulated, using the generator's ground-truth labels:

* **Reviewers** approve a draft whose category is right (and sometimes edit it for tone); they reject
  wrong ones with ``wrong_category``, or ``unsafe`` when the true work was high or critical risk.
* **QA reviewers** pass or fail sampled A3 work the same way.
* **Users** reopen wrongly resolved tickets often and correctly resolved ones rarely.
* **Category owners** approve a promotion proposal after ``approval_lag_days``.

The controller runs nightly. The result is a day-by-day story of autonomy being earned or lost, which
``hao pilot simulate`` writes up as a pilot readout.
"""

from __future__ import annotations

import random
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from hao import clock
from hao.adapters.servicenow import ServiceNowAdapter
from hao.config import REPO_ROOT, Settings
from hao.controller import autonomy as ctl
from hao.dashboard import metrics as dashboard
from hao.db.models import HaoAuditEvent, HaoAutonomyChange
from hao.intake.classifiers import make_classifier
from hao.mock_ticketing.app import create_app as create_mock
from hao.platform.app import create_app as create_platform
from hao.policy.schema import load_policy
from hao.synthetic.generator import GeneratorConfig, generate

KEY, SECRET = "pilot-key", "pilot-secret"
H = {"X-API-Key": KEY}
FIELDS = ("short_description", "description", "caller_id", "caller_email", "u_vip", "location", "contact_type",
          "category", "cmdb_ci", "priority", "assignment_group")


@dataclass
class PilotConfig:
    days: int = 21
    per_day: int = 60
    seed: int = 99
    classifier: str = "keyword"
    approval_lag_days: int = 2
    edit_rate: float = 0.05  # correct drafts the reviewer still edits (tone)
    reopen_if_wrong: float = 0.6
    reopen_if_right: float = 0.03
    start: datetime = field(default_factory=lambda: datetime(2026, 11, 2, tzinfo=UTC))


@dataclass
class PilotResult:
    config: PilotConfig
    events: list[dict]
    daily: list[dict]
    final: dict
    policy_start: dict
    policy_end: dict
    a3_funnel: dict = field(default_factory=dict)


def run_pilot(cfg: PilotConfig, workdir: Path | None = None, progress=None) -> PilotResult:
    work = Path(workdir or tempfile.mkdtemp(prefix="hao-pilot-"))
    policy_dir = work / "policy"
    if policy_dir.exists():
        shutil.rmtree(policy_dir)
    shutil.copytree(REPO_ROOT / "policy", policy_dir)  # owners' approvals edit this copy, never the repo
    for f in ("client.db", "platform.db"):
        (work / f).unlink(missing_ok=True)

    rng = random.Random(cfg.seed)
    clock.set_time(cfg.start)
    try:
        return _run(cfg, work, policy_dir, rng, progress)
    finally:
        clock.set_time(None)


def _run(cfg: PilotConfig, work: Path, policy_dir: Path, rng: random.Random, progress) -> PilotResult:
    holder: dict = {}

    def to_platform(request: httpx.Request) -> httpx.Response:
        resp = holder["platform"].post("/webhooks/ticketing", content=request.content, headers=dict(request.headers))
        return httpx.Response(resp.status_code, content=resp.content)

    ms = Settings(database_url=f"sqlite:///{work / 'client.db'}", mock_api_key=KEY, webhook_secret=SECRET)
    mock = TestClient(create_mock(ms, webhook_transport=httpx.MockTransport(to_platform), webhook_backoff_s=0))
    ps = Settings(platform_database_url=f"sqlite:///{work / 'platform.db'}", mock_api_key=KEY, webhook_secret=SECRET,
                  policy_dir=policy_dir, platform_actor="ai:hao-intake")
    app = create_platform(ps, lambda: ServiceNowAdapter(mock, KEY, actor="ai:hao-agent", backoff_s=0),
                          make_classifier(cfg.classifier), rng=random.Random(cfg.seed))
    platform = TestClient(app)
    holder["platform"] = platform
    mock.post("/api/x_kaveri/webhooks", json={"url": "http://platform/webhooks/ticketing"}, headers=H)
    sf = app.state.session_factory
    policy_start = {k: c.current_level.value for k, c in app.state.policy.categories.items()}

    tickets, labels = generate(GeneratorConfig(n=cfg.days * cfg.per_day, seed=cfg.seed, days=cfg.days,
                                               end_date=cfg.start + timedelta(days=cfg.days)))
    by_day: dict[int, list[int]] = defaultdict(list)
    for i, t in enumerate(tickets):
        day = min(cfg.days - 1, max(0, (datetime.fromisoformat(t["opened_at"]) - cfg.start).days))
        by_day[day].append(i)

    label_of: dict[str, dict] = {}
    events: list[dict] = []
    daily: list[dict] = []
    pending_approvals: list[tuple[int, int]] = []  # (due day, proposal id)
    approver: dict[int, str] = {}
    last_event = 0

    for day in range(cfg.days):
        day_start = cfg.start + timedelta(days=day, hours=9)
        idx = by_day.get(day, [])
        for k, i in enumerate(idx):
            clock.set_time(day_start + timedelta(minutes=k * 600 / max(1, len(idx))))
            body = {f: tickets[i][f] for f in FIELDS}
            sys_id = mock.post("/api/now/table/incident", json=body, headers=H).json()["result"]["sys_id"]
            label_of[sys_id] = labels[i]

        # Humans work through the queue in the afternoon.
        clock.set_time(day_start + timedelta(hours=7))
        for item in platform.get("/work-items", params={"state": "awaiting_approval"}).json():
            _review_draft(platform, item, label_of[item["incident_sys_id"]], cfg, rng)
        for item in platform.get("/work-items", params={"state": "qa_pending"}).json():
            _review_qa(platform, item, label_of[item["incident_sys_id"]])

        # Users reopen some AI-resolved tickets the same evening.
        clock.set_time(day_start + timedelta(hours=10))
        for item in platform.get("/work-items", params={"state": "executed"}).json():
            if item["id"] in _reopen_checked:
                continue
            _reopen_checked.add(item["id"])
            right = item["category"] == label_of[item["incident_sys_id"]]["true_category"]
            if rng.random() < (cfg.reopen_if_right if right else cfg.reopen_if_wrong):
                mock.patch(f"/api/now/table/incident/{item['incident_sys_id']}", json={"state": "2"}, headers=H)

        # Nightly controller run, then owners act on proposals that have waited long enough.
        night = day_start + timedelta(hours=14)
        clock.set_time(night)
        with sf() as session:
            run = ctl.run(session, app.state.policy, as_of=night)
        for p in run.proposed:
            pending_approvals.append((day + cfg.approval_lag_days, p["proposal"]))
        for due, pid in [x for x in pending_approvals if x[0] <= day]:
            pending_approvals.remove((due, pid))
            with sf() as session:
                policy = app.state.policy
                prop = session.get(HaoAutonomyChange, pid)
                owner = policy.owners[policy.categories[prop.category].owner]
                ch = ctl.apply_promotion(session, policy, pid, owner.email, policy_dir=policy_dir)
            app.state.set_policy(load_policy(policy_dir), actor="system:policy-reload")  # PR merged + deployed
            approver[ch.id] = owner.name

        # Read the day's controller events from the audit log: whatever triggered them (a review, a reopen
        # or the nightly run), the audit trail is the record.
        with sf() as session:
            for ev in session.scalars(select(HaoAuditEvent).where(HaoAuditEvent.id > last_event,
                                                                  HaoAuditEvent.action.like("autonomy.%"))
                                      .order_by(HaoAuditEvent.id)):
                last_event = ev.id
                if ev.action == "autonomy.decided":
                    continue
                kind = {"autonomy.demoted": "demoted", "autonomy.promotion_proposed": "proposed",
                        "autonomy.promotion_approved": "promoted"}.get(ev.action)
                if kind:
                    pl = ev.payload
                    events.append({"day": day + 1, "type": kind, "category": pl["category"], "from": pl["from"],
                                   "to": pl["to"], "reason": pl.get("reason", ""), "precision": pl.get("precision"),
                                   "items": pl.get("items"), "owner": pl.get("owner"),
                                   "approved_by": approver.get(pl.get("proposal"), "")})

        m = dashboard.compute(sf, app.state.policy, dashboard.load_value(policy_dir), as_of=night, days=1)
        daily.append({"day": day + 1, "tickets": m["volume"]["tickets"], "levels": m["volume"]["by_level"],
                      "ai_resolved": m["outcomes"]["ai_resolved"],
                      "approved": m["outcomes"]["approved_and_resolved"],
                      "rejected": m["quality"]["rejected"], "reopened": m["quality"]["reopened"],
                      "hours": m["value_estimate"]["human_hours_redirected"]})
        if progress:
            progress(day + 1, cfg.days, daily[-1])

    end = cfg.start + timedelta(days=cfg.days)
    final = dashboard.compute(sf, app.state.policy, dashboard.load_value(policy_dir), as_of=end, days=cfg.days + 1)
    policy_end = {k: c.current_level.value for k, c in app.state.policy.categories.items()}
    _reopen_checked.clear()
    funnel = _a3_funnel(sf, events, policy_end, cfg.start)
    return PilotResult(cfg, events, daily, final, policy_start, policy_end, funnel)


def _blocker(steps: list[dict]) -> str:
    for st in steps:
        if st["to"] != st["from"]:
            r = st["reason"]
            if r.startswith("application"):
                return "application cap (medium/high-risk app)"
            if st.get("rule") == "low-confidence-downgrade":
                return "classifier confidence < 0.80"
            if st.get("rule") == "very-low-confidence-human":
                return "classifier confidence < 0.50"
            labels = {"controller-demotion": "controller demotion (quality drop)",
                      "vip-requester-assist-only": "VIP requester (capped at A1)",
                      "reopened-ticket-human-led": "reopened ticket (capped at A1)"}
            return labels.get(st.get("rule"), st.get("rule") or r)
    return "nothing: ran at A3"


def _a3_funnel(sf, events: list[dict], policy_end: dict, pilot_start: datetime) -> dict:
    """For tickets classified into A3 categories after the last promotion: what kept them below A3?"""
    promoted = [e for e in events if e["type"] == "promoted"]
    a3 = {c for c, lvl in policy_end.items() if lvl == "A3"}
    if not promoted or not a3:
        return {}
    from hao.db.models import HaoDecision

    last_day = max(e["day"] for e in promoted)
    start = pilot_start + timedelta(days=last_day)
    with sf() as session:
        scoped = [d for d in session.scalars(select(HaoDecision))
                  if (d.received_at if d.received_at.tzinfo else d.received_at.replace(tzinfo=UTC)) >= start]
    if not scoped:
        return {}
    in_a3 = [d for d in scoped if d.predicted_category in a3]
    reasons: dict[str, int] = {}
    for d in in_a3:
        b = _blocker(d.steps)
        reasons[b] = reasons.get(b, 0) + 1
    return {"from_day": last_day + 1, "tickets": len(scoped), "in_a3_categories": len(in_a3),
            "a3": sum(1 for d in scoped if d.level == "A3"), "categories": sorted(a3),
            "blockers": dict(sorted(reasons.items(), key=lambda kv: -kv[1]))}


_reopen_checked: set[int] = set()


def _risky(label: dict) -> bool:
    return label["risk_tier"] in ("high", "critical")


def _review_draft(platform: TestClient, item: dict, label: dict, cfg: PilotConfig, rng: random.Random) -> None:
    reviewer = "l1.reviewer@kaveri.example"
    if item["category"] == label["true_category"]:
        if rng.random() < cfg.edit_rate:
            body = {"verdict": "edit", "reason_code": "tone", "reply": "Hi, this is sorted now. Thanks for waiting."}
        else:
            body = {"verdict": "approve"}
    else:
        body = {"verdict": "reject", "reason_code": "unsafe" if _risky(label) else "wrong_category"}
    platform.post(f"/work-items/{item['id']}/review", json={"reviewer": reviewer, **body})


def _review_qa(platform: TestClient, item: dict, label: dict) -> None:
    ok = item["category"] == label["true_category"]
    body = {"verdict": "pass"} if ok else {"verdict": "fail",
                                           "reason_code": "unsafe" if _risky(label) else "wrong_category"}
    platform.post(f"/work-items/{item['id']}/review", json={"reviewer": "qa.reviewer@kaveri.example", **body})


def render_readout(r: PilotResult) -> str:
    from hao.dashboard.render import markdown

    c = r.config
    changed = {k: (r.policy_start[k], v) for k, v in r.policy_end.items() if r.policy_start[k] != v}
    lines = [
        "# Pilot readout (simulated)", "",
        f"> **Simulated, not measured.** {c.days} days × {c.per_day} synthetic tickets/day "
        f"(seed {c.seed}) driven through the real system end to end, with simulated reviewers, QA and users "
        f"(`hao pilot simulate`). Classifier: `{c.classifier}`. Owners approve promotions after "
        f"{c.approval_lag_days} days. Reviewers approve correct drafts (editing {100 * c.edit_rate:.0f}% for tone) "
        f"and reject wrong ones; users reopen {100 * c.reopen_if_wrong:.0f}% of wrongly resolved tickets and "
        f"{100 * c.reopen_if_right:.0f}% of correct ones.", "",
        "## Autonomy changes", "",
    ]
    if r.events:
        lines += ["| Day | Event | Category | Change | Detail |", "|---|---|---|---|---|"]
        for e in r.events:
            proposed = f"{100 * (e.get('precision') or 0):.1f}% over {e.get('items')} items, to {e.get('owner')}"
            detail = {"demoted": e.get("reason", ""), "proposed": proposed,
                      "promoted": f"approved by {e.get('approved_by')}"}[e["type"]]
            lines.append(f"| {e['day']} | {e['type']} | {e['category']} | {e['from']} → {e['to']} | {detail} |")
    else:
        lines.append("None: no category reached the evidence bar for promotion or fell below it for demotion.")
    lines += ["", "Policy at the end vs start: " + (", ".join(f"`{k}` {a} → {b}" for k, (a, b) in changed.items())
                                                   or "unchanged"), "",
              "## Results over the whole pilot", "", markdown(r.final), "",
              *_funnel_md(r.a3_funnel),
              "## Day by day", "", "| Day | Tickets | A3 | A2 | A1 | A0 | AI-resolved | Approved drafts | Rejected | "
              "Reopened | Hours redirected (est.) |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for d in r.daily:
        lv = d["levels"]
        lines.append(f"| {d['day']} | {d['tickets']} | {lv['A3']} | {lv['A2']} | {lv['A1']} | {lv['A0']} | "
                     f"{d['ai_resolved']} | {d['approved']} | {d['rejected']} | {d['reopened']} | {d['hours']} |")
    lines += ["", "## Categories at the end", "", "| Category | Policy | Effective | Evidence (items) | Precision |",
              "|---|---|---|---|---|"]
    for cat in r.final["categories"]:
        p = "n/a" if cat["precision"] is None else f"{100 * cat['precision']:.1f}%"
        lines.append(f"| {cat['category']} | {cat['policy_level']} | {cat['effective_level']} | {cat['items']} | {p} |")
    return "\n".join(lines) + "\n"


def _funnel_md(f: dict) -> list[str]:
    if not f:
        return []
    n, k = f["tickets"], f["in_a3_categories"]
    lines = [f"## Where A3 was lost (day {f['from_day']} onwards, after the last promotion)", "",
             f"{k} of {n} tickets ({100 * k / n:.0f}%) were classified into the A3 categories "
             f"({', '.join(f['categories'])}); {f['a3']} ({100 * f['a3'] / n:.1f}% of all tickets) ran at A3. "
             "The first policy step that lowered each ticket's level:", "",
             "| What kept the ticket below A3 | Tickets | Share of A3-category tickets |", "|---|---|---|"]
    lines += [f"| {b} | {c} | {100 * c / k:.0f}% |" for b, c in f["blockers"].items()]
    return lines + [""]
