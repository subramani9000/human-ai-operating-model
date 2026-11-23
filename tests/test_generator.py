import re

from hao.synthetic.catalog import CATALOG, CATALOG_BY_NAME
from hao.synthetic.generator import GeneratorConfig, generate


def _gen(n=800, seed=7):
    return generate(GeneratorConfig(n=n, seed=seed))


def test_deterministic_for_same_seed():
    assert _gen() == _gen()


def test_different_seed_differs():
    assert _gen(seed=1)[0] != _gen(seed=2)[0]


def test_catalog_weights_sum_to_one():
    assert abs(sum(c.weight for c in CATALOG) - 1.0) < 1e-9


def test_tickets_and_labels_aligned_and_chronological():
    tickets, labels = _gen()
    assert len(tickets) == len(labels) == 800
    assert [t["sys_id"] for t in tickets] == [lab["sys_id"] for lab in labels]
    opened = [t["opened_at"] for t in tickets]
    assert opened == sorted(opened)
    assert [t["number"] for t in tickets][:2] == ["INC0010001", "INC0010002"]


def test_tickets_are_servicenow_shaped_strings_without_label_leakage():
    tickets, _ = _gen(n=200)
    for t in tickets:
        assert all(isinstance(v, str) for v in t.values())
        assert not any(k.startswith("true_") for k in t)
        assert t["priority"] in {"1", "2", "3", "4", "5"}
        assert t["u_vip"] in {"true", "false"}


def test_labels_reference_valid_categories_and_risk():
    _, labels = _gen()
    for lab in labels:
        spec = CATALOG_BY_NAME[lab["true_category"]]
        assert lab["risk_tier"] == spec.risk_tier
        assert lab["true_application"] in spec.applications


def test_duplicates_point_to_existing_tickets_of_same_category():
    tickets, labels = _gen(n=3000)
    by_id = {lab["sys_id"]: lab for lab in labels}
    dups = [lab for lab in labels if lab["duplicate_of"]]
    assert dups, "expected some near-duplicates"
    for d in dups:
        assert by_id[d["duplicate_of"]]["true_category"] == d["true_category"]


def test_noise_is_realistic():
    tickets, labels = _gen(n=3000)
    blank = sum(1 for t in tickets if t["category"] == "") / len(tickets)
    pii = sum(1 for lab in labels if lab["pii_types"]) / len(labels)
    styles = {lab["style"] for lab in labels}
    assert 0.3 < blank < 0.5
    assert pii > 0.4
    assert {"tanglish", "hinglish", "angry", "terse", "formal"} <= styles


def test_signature_pii_is_actually_present_in_text():
    tickets, labels = _gen(n=500)
    phone = re.compile(r"\+91 \d{10}")
    hits = [t for t, lab in zip(tickets, labels, strict=True)
            if "phone_number" in lab["pii_types"] and "typos" not in lab["noise"]]
    assert hits
    assert all(phone.search(t["description"]) for t in hits)
