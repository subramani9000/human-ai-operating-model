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
eval_app = typer.Typer(help="Evaluations", no_args_is_help=True)
platform_app = typer.Typer(help="Human+AI platform service (our side of the integration)", no_args_is_help=True)
app.add_typer(golden_app, name="golden")
app.add_typer(audit_app, name="audit")
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
