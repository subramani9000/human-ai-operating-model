import pytest

from hao.config import REPO_ROOT
from hao.intake.classifiers import Classification
from hao.policy.schema import Level, load_policy
from hao.policy.simulate import simulate, with_overrides

POLICY = load_policy(REPO_ROOT / "policy")


def row(number, true_cat, app, text="x", risk="low"):
    return {"ticket": {"number": number, "short_description": text, "description": "", "priority": "3",
                       "u_vip": "false", "caller_id": "", "caller_email": ""},
            "label": {"true_category": true_cat, "true_application": app, "risk_tier": risk}}


def pred(cat, app, conf):
    return Classification(category=cat, application=app, confidence=conf, classifier="t")


def test_with_overrides_respects_max_level():
    promoted = with_overrides(POLICY, {"password_reset": "A3"})
    assert promoted.categories["password_reset"].current_level == Level.A3
    with pytest.raises(ValueError, match="max_level"):
        with_overrides(POLICY, {"payroll_access": "A3"})
    assert with_overrides(POLICY, no_rules=True).rules == []


def test_simulation_flags_over_autonomy_on_high_risk_work():
    golden = [row("1", "payroll_discrepancy", "PeopleDesk", "salary wrong", risk="high"),
              row("2", "vpn_access", "GlobalVPN", "vpn down")]
    preds = [pred("password_reset", "SSO", 0.95), pred("vpn_access", "GlobalVPN", 0.95)]
    res = simulate("t", with_overrides(POLICY, {"password_reset": "A3"}), golden, preds)
    assert res.over == 1 and res.over_high_risk == 1 and res.exact == 1
    assert res.examples[0]["intended"] == "A1" and res.examples[0]["got"] == "A3"


def test_low_confidence_trades_automation_for_safety():
    golden = [row("1", "password_reset", "SSO", "pwd reset")]
    res = simulate("t", POLICY, golden, [pred("password_reset", "SSO", 0.6)])
    assert res.under == 1 and res.over == 0
