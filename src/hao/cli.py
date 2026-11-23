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
    levels = Counter(c.current_level.value for c in policy.categories.values())
    typer.secho(f"Policy OK — {policy.version_label} (version {policy.version})", fg=typer.colors.GREEN)
    typer.echo(f"  {len(policy.categories)} categories, {len(policy.applications)} applications, "
               f"{len(policy.rules)} rules, {len(policy.owners)} owners")
    typer.echo("  current levels: " + ", ".join(f"{lvl}={levels.get(lvl, 0)}" for lvl in ("A0", "A1", "A2", "A3")))


@db_app.command("init")
def db_init():
    """Create all tables in DATABASE_URL."""
    from hao.db.session import init_db, make_engine

    url = get_settings().database_url
    init_db(make_engine(url))
    typer.echo(f"Initialised schema at {url.split('@')[-1]}")


@mock_app.command("serve")
def mock_serve(host: str = "0.0.0.0", port: int = 8001, reload: bool = False):
    """Run the mock ticketing API (OpenAPI docs at /docs)."""
    import uvicorn

    uvicorn.run("hao.mock_ticketing.app:create_app", factory=True, host=host, port=port, reload=reload)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


if __name__ == "__main__":
    app()
