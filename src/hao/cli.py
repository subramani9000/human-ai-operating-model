"""`hao` command line: generate data, validate policy, initialise the DB, run the mock client system."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import typer

from hao.config import REPO_ROOT, get_settings

app = typer.Typer(help="Human+AI operating model toolkit", no_args_is_help=True)
policy_app = typer.Typer(help="Operating-model policy", no_args_is_help=True)
db_app = typer.Typer(help="Database", no_args_is_help=True)
mock_app = typer.Typer(help="Mock client ticketing system", no_args_is_help=True)
golden_app = typer.Typer(help="Golden evaluation sets", no_args_is_help=True)
audit_app = typer.Typer(help="Tamper-evident audit log", no_args_is_help=True)
pilot_app = typer.Typer(help="Simulated pilot runs", no_args_is_help=True)
killswitch_app = typer.Typer(help="Pause all AI actions (any owner) / resume them (sponsor only)",
                             no_args_is_help=True)
controller_app = typer.Typer(help="Autonomy controller: evidence → demotions and promotion proposals",
                             no_args_is_help=True)
eval_app = typer.Typer(help="Evaluations", no_args_is_help=True)
platform_app = typer.Typer(help="Human+AI platform service (our side of the integration)", no_args_is_help=True)
app.add_typer(golden_app, name="golden")
app.add_typer(audit_app, name="audit")
app.add_typer(controller_app, name="controller")
app.add_typer(pilot_app, name="pilot")
app.add_typer(killswitch_app, name="killswitch")
app.add_typer(eval_app, name="eval")
app.add_typer(platform_app, name="platform")
app.add_typer(policy_app, name="policy")
app.add_typer(db_app, name="db")
app.add_typer(mock_app, name="mock")


@app.command()
def generate(
    n: int = typer.Option(5000, help="Number of tickets"),
    seed: int = typer.Option(42, help="Random seed (same seed → same dataset)"),
    out: Path = typer.Option(REPO_ROOT / "data" / "synthetic", help="Output directory"),
    sample: int = typer.Option(200, help="Also write a small committed sample of this size (0 to skip)"),
):
    """Generate the synthetic Kaveri service-desk dataset (tickets + ground-truth labels)."""
    from hao.synthetic.generator import GeneratorConfig
    from hao.synthetic.generator import generate as gen

    tickets, labels = gen(GeneratorConfig(n=n, seed=seed))
    out.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out / "tickets.jsonl", tickets)
    _write_jsonl(out / "labels.jsonl", labels)
    if sample:
        _write_jsonl(out / "sample_tickets.jsonl", tickets[:sample])
        _write_jsonl(out / "sample_labels.jsonl", labels[:sample])
    typer.echo(f"Wrote {len(tickets)} tickets and labels to {out}")


@app.command()
def profile(path: Path = typer.Option(REPO_ROOT / "data" / "synthetic", help="Dataset directory")):
    """Profile a dataset: the numbers that go in the discovery read-out."""
    tickets = _read_jsonl(path / "tickets.jsonl")
    labels = _read_jsonl(path / "labels.jsonl")
    n = len(tickets)
    pct = lambda k: f"{100 * k / n:5.1f}%"  # noqa: E731
    noise = Counter(x for lab in labels for x in lab["noise"])
    typer.echo(f"Tickets: {n}\n\nTrue category mix:")
    for cat, k in Counter(lab["true_category"] for lab in labels).most_common():
        typer.echo(f"  {cat:<22} {k:>5}  {pct(k)}")
    blank = sum(1 for t in tickets if not t["category"])
    other = sum(1 for t in tickets if t["category"] == "Other")
    misrouted = sum(1 for t, lab in zip(tickets, labels, strict=True)
                    if t["assignment_group"] != lab["true_assignment_group"])
    with_pii = sum(1 for lab in labels if lab["pii_types"])
    typer.echo("\nData quality (what the AI has to cope with):")
    typer.echo(f"  user category blank        {pct(blank)}")
    typer.echo(f"  user category 'Other'      {pct(other)}")
    typer.echo(f"  needs re-routing           {pct(misrouted)}")
    typer.echo(f"  contains PII               {pct(with_pii)}")
    for k, v in noise.most_common():
        typer.echo(f"  noise: {k:<19} {pct(v)}")
    typer.echo("\nStyle mix: " + ", ".join(f"{s} {pct(k).strip()}"
                                            for s, k in Counter(lab["style"] for lab in labels).most_common()))


@policy_app.command("validate")
def policy_validate(policy_dir: Path = typer.Option(None, help="Defaults to ./policy")):
    """Validate autonomy.yaml + ownership.yaml. Exit code 1 on failure (used in CI)."""
    from hao.policy.schema import load_policy

    try:
        policy = load_policy(policy_dir or get_settings().policy_dir)
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"Policy INVALID:\n{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    from hao.agents.playbooks import load_playbooks

    try:
        playbooks = load_playbooks(policy_dir or get_settings().policy_dir)
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"Playbooks INVALID:\n{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    problems = playbooks.check_against(policy)
    if problems:
        typer.secho("Playbooks do not match policy:\n  " + "\n  ".join(problems), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    levels = Counter(c.current_level.value for c in policy.categories.values())
    typer.secho(f"Policy OK — {policy.version_label} (version {policy.version})", fg=typer.colors.GREEN)
    typer.echo(f"  {len(policy.categories)} categories, {len(policy.applications)} applications, "
               f"{len(policy.rules)} rules, {len(policy.owners)} owners")
    typer.echo(f"  {len(playbooks.playbooks)} playbooks: {', '.join(playbooks.playbooks)}")
    typer.echo("  current levels: " + ", ".join(f"{lvl}={levels.get(lvl, 0)}" for lvl in ("A0", "A1", "A2", "A3")))


@policy_app.command("simulate")
def policy_simulate(
    classifier: str = typer.Option("keyword", help="keyword | claude"),
    golden: str = typer.Option("golden_v1", help="golden_v1 | hard_v1"),
    promote: list[str] = typer.Option([], help="What-if promotion, e.g. --promote password_reset=A3 (repeatable)"),
    write: bool = typer.Option(True, help="Write docs/eval-reports/policy-sim-<classifier>-<golden>.md"),
):
    """Simulate the policy on a golden set: level mix, over-autonomy (dangerous) and under-autonomy."""
    from hao.evals.classification_eval import run_classifier
    from hao.evals.golden import read_golden
    from hao.intake.classifiers import make_classifier
    from hao.policy.schema import load_policy
    from hao.policy.simulate import render, simulate, with_overrides

    rows = read_golden(GOLDEN_DIR / f"{golden}.jsonl")
    preds = run_classifier(make_classifier(classifier), rows, workers=1 if classifier == "keyword" else 4)
    base = load_policy(get_settings().policy_dir)
    overrides = dict(p.split("=", 1) for p in promote)
    scenarios = [("current policy", base), ("current policy, rules off", with_overrides(base, no_rules=True))]
    if overrides:
        label = ", ".join(f"{k}→{v}" for k, v in overrides.items())
        scenarios += [(f"promote {label}", with_overrides(base, overrides)),
                      (f"promote {label}, rules off", with_overrides(base, overrides, no_rules=True))]
    md = render([simulate(name, pol, rows, preds) for name, pol in scenarios], classifier, golden)
    typer.echo(md)
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / f"policy-sim-{classifier}-{golden}.md").write_text(md, encoding="utf-8")


def _platform_sessions():
    from hao.db.session import init_db, make_engine, make_session_factory

    s = get_settings()
    engine = make_engine(s.platform_database_url or s.database_url)
    init_db(engine, prefix="hao_")
    return make_session_factory(engine)


@audit_app.command("verify")
def audit_verify(expected_head: str = typer.Option("", help="Last anchored head hash, to detect a rewritten chain")):
    """Walk the hash chain from genesis. Exit code 1 if anything was edited, deleted, reordered or rewritten."""
    from hao.audit.log import verify

    r = verify(_platform_sessions(), expected_head or None)
    if r.ok:
        typer.secho(f"Audit chain OK: {r.events} events, head {r.head_hash}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"Audit chain BROKEN at event {r.broken_at_id}: {r.reason}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@audit_app.command("head")
def audit_head():
    """Print the current head hash. Ship it outside the database (audit mailbox / WORM storage) daily."""
    from hao.audit.log import head

    with _platform_sessions()() as session:
        h = head(session)
    typer.echo(f"{h.hash} {h.id} {h.ts.isoformat()}" if h else "empty")


@audit_app.command("export")
def audit_export(out: Path = typer.Option(Path("audit-export.jsonl")), incident: str = ""):
    """Export events (optionally one incident) as JSONL for internal audit."""
    from sqlalchemy import select

    from hao.audit.log import _ts
    from hao.db.models import HaoAuditEvent

    with _platform_sessions()() as session, out.open("w", encoding="utf-8") as fh:
        stmt = select(HaoAuditEvent).order_by(HaoAuditEvent.id)
        if incident:
            stmt = stmt.where(HaoAuditEvent.incident_sys_id == incident)
        n = 0
        for e in session.scalars(stmt):
            fh.write(json.dumps({"id": e.id, "ts": _ts(e.ts), "incident": e.incident_sys_id, "actor": e.actor,
                                 "action": e.action, "payload": e.payload, "policy_version": e.policy_version,
                                 "decision_id": e.decision_id, "prev_hash": e.prev_hash, "hash": e.hash}) + "\n")
            n += 1
    typer.echo(f"Exported {n} events to {out}")


@controller_app.command("status")
def controller_status():
    """Per category: policy level, effective level, evidence and open proposals."""
    from hao.controller import autonomy as ctl
    from hao.policy.schema import load_policy

    policy = load_policy(get_settings().policy_dir)
    with _platform_sessions()() as session:
        rows = ctl.status(session, policy)
    typer.echo(f"{'category':<22}{'policy':>7}{'effective':>10}{'items':>7}{'precision':>11}  notes")
    for r in rows:
        p = "" if r["precision"] is None else f"{100 * r['precision']:.1f}%"
        notes = ("DEMOTED " if r["demoted"] else "") + (f"proposal #{r['proposal']}" if r["proposal"] else "")
        typer.echo(f"{r['category']:<22}{r['policy_level']:>7}{r['effective_level']:>10}{r['items']:>7}{p:>11}"
                   f"  {notes}")


@controller_app.command("run")
def controller_run():
    """Scheduled run: automatic demotions, then promotion proposals for owners."""
    from hao.controller import autonomy as ctl
    from hao.policy.schema import load_policy

    policy = load_policy(get_settings().policy_dir)
    with _platform_sessions()() as session:
        r = ctl.run(session, policy)
    for d in r.demoted:
        typer.secho(f"DEMOTED {d['category']}: {d['from']} → {d['to']} ({d['reason']})", fg=typer.colors.RED)
    for p in r.proposed:
        typer.secho(f"PROPOSAL #{p['proposal']} {p['category']}: {p['from']} → {p['to']} "
                    f"({100 * p['precision']:.1f}% over {p['items']} items) awaiting {p['owner']}",
                    fg=typer.colors.GREEN)
    if not r.demoted and not r.proposed:
        typer.echo("No changes.")


@controller_app.command("apply")
def controller_apply(proposal: int, approved_by: str = typer.Option(..., help="Category owner's email")):
    """Owner approves a promotion: edits policy/autonomy.yaml for the PR and records the approval."""
    from hao.controller import autonomy as ctl
    from hao.policy.schema import load_policy

    s = get_settings()
    policy = load_policy(s.policy_dir)
    with _platform_sessions()() as session:
        try:
            ch = ctl.apply_promotion(session, policy, proposal, approved_by, policy_dir=s.policy_dir)
        except ctl.ControllerError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
    typer.secho(f"Applied: {ch.patch}. Open a PR with policy/autonomy.yaml, then POST /policy/reload after merge.",
                fg=typer.colors.GREEN)


@controller_app.command("clear")
def controller_clear(category: str, by: str = typer.Option(..., help="Category owner's email"), note: str = ""):
    """Owner lifts a demotion after fixing its cause. Evidence starts again from zero."""
    from hao.controller import autonomy as ctl
    from hao.policy.schema import load_policy

    policy = load_policy(get_settings().policy_dir)
    with _platform_sessions()() as session:
        try:
            n = ctl.clear_override(session, policy, category, by, note)
        except ctl.ControllerError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
    typer.echo(f"Cleared {n} demotion(s) on {category}.")


@pilot_app.command("simulate")
def pilot_simulate(
    days: int = typer.Option(28, help="Simulated days"),
    per_day: int = typer.Option(200, help="Tickets per day (Kaveri's real volume is ~600)"),
    seed: int = typer.Option(99),
    classifier: str = typer.Option("keyword", help="keyword | claude"),
    approval_lag_days: int = typer.Option(2, help="Days an owner takes to approve a promotion"),
    out: Path = typer.Option(REPO_ROOT / "docs" / "pilot-readout-simulated.md"),
):
    """Drive weeks of synthetic traffic through the whole system with simulated humans; write a readout."""
    from hao.pilot.simulate import PilotConfig, render_readout, run_pilot

    cfg = PilotConfig(days=days, per_day=per_day, seed=seed, classifier=classifier,
                      approval_lag_days=approval_lag_days)

    def progress(d, n, row):
        typer.echo(f"day {d:>2}/{n}: {row['tickets']} tickets · A3 {row['levels']['A3']} · A2 {row['levels']['A2']}"
                   f" · AI-resolved {row['ai_resolved']} · approved {row['approved']} · reopened {row['reopened']}")

    result = run_pilot(cfg, progress=progress)
    out.write_text(render_readout(result), encoding="utf-8")
    for e in result.events:
        typer.secho(f"day {e['day']}: {e['type']} {e['category']} {e['from']} → {e['to']}", fg=typer.colors.CYAN)
    typer.secho(f"Wrote {out}", fg=typer.colors.GREEN)


@killswitch_app.command("on")
def killswitch_on(by: str = typer.Option(..., help="Your email (a named owner)"),
                  reason: str = typer.Option(..., help="Why: goes in the audit log"),
                  cap: str = typer.Option("A1", help="A1: AI still triages and hands off · A0: AI does nothing")):
    """Stop the line: every category is capped at once, and pending approvals can no longer execute."""
    from hao.controller import autonomy as ctl
    from hao.policy.schema import load_policy

    with _platform_sessions()() as session:
        try:
            state = ctl.kill_switch_on(session, load_policy(get_settings().policy_dir), by, reason, cap)
        except ctl.ControllerError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
    typer.secho(f"KILL SWITCH ON: all categories capped at {state['cap']}.", fg=typer.colors.RED)


@killswitch_app.command("off")
def killswitch_off(by: str = typer.Option(..., help="Sponsor's email"), note: str = ""):
    """Resume AI actions. Sponsor only."""
    from hao.controller import autonomy as ctl
    from hao.policy.schema import load_policy

    with _platform_sessions()() as session:
        try:
            ctl.kill_switch_off(session, load_policy(get_settings().policy_dir), by, note)
        except ctl.ControllerError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
    typer.secho("Kill switch off: policy levels apply again.", fg=typer.colors.GREEN)


@killswitch_app.command("status")
def killswitch_status():
    from hao.controller import autonomy as ctl

    with _platform_sessions()() as session:
        state = ctl.kill_switch_state(session)
    typer.echo("off" if state is None else f"ON since {state['since']} by {state['by']}: {state['reason']} "
                                           f"(cap {state['cap']})")


@platform_app.command("sweep")
def platform_sweep():
    """Expire A2 drafts nobody reviewed within approval_timeout_hours (schedule every 15 minutes)."""
    from hao.adapters.servicenow import ServiceNowAdapter
    from hao.agents.drafter import TemplateDrafter
    from hao.agents.lanes import LaneDeps, sweep_expired
    from hao.agents.playbooks import load_playbooks
    from hao.policy.schema import load_policy

    s = get_settings()
    with ServiceNowAdapter.from_settings(s) as adapter:
        deps = LaneDeps(session_factory=_platform_sessions(), adapter=adapter, policy=load_policy(s.policy_dir),
                        playbooks=load_playbooks(s.policy_dir), drafter=TemplateDrafter(), actor="ai:hao-agent")
        expired = sweep_expired(deps)
    typer.echo(f"Expired {len(expired)} unreviewed draft(s): {expired}")


@db_app.command("init")
def db_init():
    """Create all tables in DATABASE_URL."""
    from hao.db.session import init_db, make_engine

    s = get_settings()
    init_db(make_engine(s.database_url), prefix="client_")
    init_db(make_engine(s.platform_database_url or s.database_url), prefix="hao_")
    typer.echo(f"Initialised client schema at {s.database_url.split('@')[-1]}")
    typer.echo(f"Initialised platform schema at {(s.platform_database_url or s.database_url).split('@')[-1]}")


@mock_app.command("serve")
def mock_serve(host: str = "0.0.0.0", port: int = 8001, reload: bool = False):
    """Run the mock ticketing API (OpenAPI docs at /docs)."""
    import uvicorn

    uvicorn.run("hao.mock_ticketing.app:create_app", factory=True, host=host, port=port, reload=reload)


GOLDEN_DIR = REPO_ROOT / "data" / "golden"
REPORT_DIR = REPO_ROOT / "docs" / "eval-reports"


@golden_app.command("build")
def golden_build(per_category: int = 20, data: Path = typer.Option(REPO_ROOT / "data" / "synthetic")):
    """Build the stratified golden set (category x style) from the synthetic dataset."""
    from hao.evals.golden import build_golden, write_golden

    rows = build_golden(_read_jsonl(data / "tickets.jsonl"), _read_jsonl(data / "labels.jsonl"), per_category)
    write_golden(rows, GOLDEN_DIR / "golden_v1.jsonl")
    typer.echo(f"Wrote {len(rows)} tickets to {GOLDEN_DIR / 'golden_v1.jsonl'}")


@eval_app.command("redaction")
def eval_redaction(
    data: Path = typer.Option(REPO_ROOT / "data" / "synthetic"),
    write: bool = True,
    min_recall: float = typer.Option(0.99, help="Exit 1 if overall recall falls below this (CI gate)"),
):
    """Measure PII leakage (recall), over-redaction (precision) and utility on the full dataset."""
    from hao.evals.redaction_eval import evaluate_redaction

    rep = evaluate_redaction(_read_jsonl(data / "tickets.jsonl"), _read_jsonl(data / "labels.jsonl"))
    lines = ["# Redaction eval", "", f"{rep.n_tickets} tickets · {sum(rep.found.values())} PII values present "
             f"verbatim in text · {rep.spans_total} redacted spans", "",
             "| PII type | Present | Leaked | Recall |", "|---|---|---|---|"]
    lines += [f"| {t} | {rep.found.get(t, sum(rep.found.values()))} | "
              f"{rep.leaked.get(t, sum(rep.leaked.values()))} | {100 * r:.2f}% |" for t, r in rep.recall.items()]
    lines += ["", f"- **Precision** (redacted spans that were real PII, typo-tolerant): {100 * rep.precision:.2f}%",
              f"- **Utility** (application name still readable after redaction): {100 * rep.utility:.2f}%", ""]
    if rep.leak_examples:
        lines += ["## Leaks", "", *[f"- {e['number']}: {e['type']}" for e in rep.leak_examples], ""]
    if rep.false_positive_examples:
        lines += ["## Over-redaction examples", "", *[f"- {e}" for e in rep.false_positive_examples], ""]
    md = "\n".join(lines)
    typer.echo(md)
    if write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "redaction-synthetic.md").write_text(md + "\n", encoding="utf-8")
    if rep.recall["ALL"] < min_recall:
        typer.secho(f"Redaction recall {rep.recall['ALL']:.4f} is below {min_recall}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@eval_app.command("classify")
def eval_classify(
    classifier: str = typer.Option("keyword", help="keyword | claude"),
    golden: str = typer.Option("golden_v1", help="golden_v1 (synthetic, 300) | hard_v1 (hand-written, 45)"),
    limit: int = typer.Option(0, help="Only the first N tickets (0 = all)"),
    workers: int = typer.Option(4, help="Parallel requests for LLM classifiers"),
    write: bool = typer.Option(True, help="Write docs/eval-reports/<classifier>-<golden>.md"),
):
    """Run a classifier over a golden set and report accuracy, risk-downgrades, calibration and cost."""
    from hao.evals.classification_eval import evaluate_classification, render_markdown, run_classifier
    from hao.evals.golden import read_golden
    from hao.intake.classifiers import make_classifier

    rows = read_golden(GOLDEN_DIR / f"{golden}.jsonl")
    rows = rows[:limit] if limit else rows
    clf = make_classifier(classifier)
    preds = run_classifier(clf, rows, workers=workers if classifier != "keyword" else 1)
    report = evaluate_classification(rows, preds)
    md = render_markdown(report, golden, get_settings().usd_to_inr)
    typer.echo(md)
    if write and not limit:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{classifier}-{golden}.md"
        (REPORT_DIR / name).write_text(md, encoding="utf-8")
        typer.secho(f"\nWrote {REPORT_DIR / name}", fg=typer.colors.GREEN)


@eval_app.command("gate")
def eval_gate(update: bool = typer.Option(False, help="Write current values as the new baselines"),
              classifier: str = typer.Option("keyword")):
    """CI gate: fail if redaction, classification or simulated over-autonomy got worse than the baselines."""
    from hao.evals.gate import BASELINES, compare, load_baselines, measure, write_baselines

    current = measure(classifier)
    if update or not BASELINES.exists():
        tol = load_baselines()[1] if BASELINES.exists() else 0.005
        write_baselines(current, tol)
        typer.secho(f"Baselines written to {BASELINES}", fg=typer.colors.GREEN)
        return
    baseline, tol = load_baselines()
    rows = compare(baseline, current, tol)
    typer.echo(f"{'metric':<52}{'baseline':>10}{'current':>10}  result")
    for r in rows:
        mark = "ok " if r["ok"] else "FAIL"
        typer.echo(f"{r['metric']:<52}{str(r['baseline']):>10}{str(r['current']):>10}  {mark} {r['note']}")
    if not all(r["ok"] for r in rows):
        typer.secho("Eval gate FAILED: this change makes the system measurably worse.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho("Eval gate passed.", fg=typer.colors.GREEN)


@platform_app.command("serve")
def platform_serve(host: str = "0.0.0.0", port: int = 8002, classifier: str = "keyword", drafter: str = "template"):
    """Run the platform: webhooks → intake → policy decision → agent lane. Review console at /console."""
    import os

    import uvicorn

    os.environ["HAO_CLASSIFIER"] = classifier
    os.environ["HAO_DRAFTER"] = drafter
    uvicorn.run("hao.platform.app:create_app_from_env", factory=True, host=host, port=port)


@platform_app.command("subscribe")
def platform_subscribe():
    """Register the platform's webhook URL with the client ticketing system."""
    from hao.adapters.servicenow import ServiceNowAdapter

    s = get_settings()
    with ServiceNowAdapter.from_settings(s) as adapter:
        sub = adapter.subscribe_webhook(f"{s.platform_public_url}/webhooks/ticketing")
    typer.echo(f"Subscribed: {sub}")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


if __name__ == "__main__":
    app()
