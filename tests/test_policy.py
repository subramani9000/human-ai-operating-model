import shutil
from pathlib import Path

import pytest
import yaml

from hao.config import REPO_ROOT
from hao.policy.schema import Level, load_policy

POLICY_DIR = REPO_ROOT / "policy"


@pytest.fixture
def policy_copy(tmp_path: Path) -> Path:
    for f in ("autonomy.yaml", "ownership.yaml"):
        shutil.copy(POLICY_DIR / f, tmp_path / f)
    return tmp_path


def _edit(path: Path, fn) -> None:
    data = yaml.safe_load((path / "autonomy.yaml").read_text())
    fn(data)
    (path / "autonomy.yaml").write_text(yaml.safe_dump(data))


def test_repo_policy_is_valid():
    policy = load_policy(POLICY_DIR)
    assert len(policy.version) == 12
    assert policy.categories["password_reset"].owner in policy.owners


def test_finance_and_hr_never_exceed_a2():
    policy = load_policy(POLICY_DIR)
    for name in ("payroll_access", "payroll_discrepancy", "expense_claim_issue"):
        assert policy.categories[name].max_level <= Level.A2
    for app in ("FinCore", "PeopleDesk"):
        assert policy.applications[app].max_level <= Level.A2


def test_critical_work_is_human_only():
    policy = load_policy(POLICY_DIR)
    for name, cat in policy.categories.items():
        if cat.risk_tier == "critical":
            assert cat.max_level == Level.A0, name


def test_rejects_high_risk_category_with_ai_acts(policy_copy):
    _edit(policy_copy, lambda d: d["categories"]["payroll_access"].update(max_level="A3"))
    with pytest.raises(ValueError, match="exceeds tier cap"):
        load_policy(policy_copy)


def test_rejects_category_without_known_owner(policy_copy):
    _edit(policy_copy, lambda d: d["categories"]["vpn_access"].update(owner="nobody"))
    with pytest.raises(ValueError, match="owner 'nobody' not defined"):
        load_policy(policy_copy)


def test_rejects_current_level_above_max(policy_copy):
    _edit(policy_copy, lambda d: d["categories"]["software_install"].update(current_level="A3"))
    with pytest.raises(ValueError, match="above max_level"):
        load_policy(policy_copy)


def test_rejects_flapping_controller_thresholds(policy_copy):
    _edit(policy_copy, lambda d: d["controller"].update(demote_precision=0.96))
    with pytest.raises(ValueError, match="flapping"):
        load_policy(policy_copy)


def test_rejects_ambiguous_rule_effect(policy_copy):
    _edit(policy_copy, lambda d: d["rules"][0]["effect"].update(cap="A1"))
    with pytest.raises(ValueError, match="exactly one"):
        load_policy(policy_copy)


def test_version_changes_with_content(policy_copy):
    before = load_policy(policy_copy).version
    _edit(policy_copy, lambda d: d["categories"]["password_reset"].update(current_level="A3"))
    assert load_policy(policy_copy).version != before


def test_level_ordering():
    assert Level.A0 < Level.A1 < Level.A2 < Level.A3
    assert Level.A2.rank == 2


def test_rejects_invalid_tripwire_regex(policy_copy):
    _edit(policy_copy, lambda d: d["rules"].append({"id": "bad", "when": {"text_matches": "(unclosed"},
                                                     "effect": {"cap": "A0"}}))
    with pytest.raises(ValueError, match="valid regex"):
        load_policy(policy_copy)


def test_unknown_category_must_be_human_only(policy_copy):
    _edit(policy_copy, lambda d: d["unknown_category"].update(level="A2"))
    with pytest.raises(ValueError, match="must be A0"):
        load_policy(policy_copy)
