from datetime import UTC, datetime

from hao import clock
from hao.pilot.simulate import PilotConfig, render_readout, run_pilot


def test_short_pilot_runs_end_to_end_and_restores_the_clock(tmp_path):
    r = run_pilot(PilotConfig(days=3, per_day=25), workdir=tmp_path)
    assert len(r.daily) == 3 and r.final["volume"]["tickets"] > 0
    assert r.final["governance"]["audit_chain_ok"]
    assert r.final["governance"]["decisions_with_audit_record"] == 1.0
    assert (tmp_path / "policy" / "autonomy.yaml").exists()  # owners edit the copy, never the repo
    text = render_readout(r)
    assert text.startswith("# Pilot readout (simulated)") and "Simulated, not measured" in text
    assert abs((clock.now() - datetime.now(UTC)).total_seconds()) < 5


def test_pilot_is_deterministic(tmp_path):
    a = run_pilot(PilotConfig(days=2, per_day=20), workdir=tmp_path / "a")
    b = run_pilot(PilotConfig(days=2, per_day=20), workdir=tmp_path / "b")
    assert a.daily == b.daily
