import threading

from sqlalchemy import delete, select, update

from hao.audit.log import GENESIS, append, head, trail, verify
from hao.db.models import HaoAuditEvent
from hao.db.session import init_db, make_engine, make_session_factory


def make_log(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    init_db(engine, prefix="hao_")
    return make_session_factory(engine)


def fill(sf, n=5):
    with sf() as s:
        for i in range(n):
            append(s, incident_sys_id=f"inc{i % 2}", actor="ai:test", action="autonomy.decided",
                   payload={"level": "A1", "i": i}, policy_version="abc123")


def test_chain_links_from_genesis(tmp_path):
    sf = make_log(tmp_path)
    fill(sf)
    with sf() as s:
        rows = s.scalars(select(HaoAuditEvent).order_by(HaoAuditEvent.id)).all()
        assert rows[0].prev_hash == GENESIS
        assert all(b.prev_hash == a.hash for a, b in zip(rows, rows[1:], strict=False))
        assert head(s).hash == rows[-1].hash
    res = verify(sf)
    assert res.ok and res.events == 5


def test_detects_edited_row(tmp_path):
    sf = make_log(tmp_path)
    fill(sf)
    with sf() as s:  # someone "fixes" a decision directly in the database
        s.execute(update(HaoAuditEvent).where(HaoAuditEvent.id == 3).values(payload={"level": "A3", "i": 2}))
        s.commit()
    res = verify(sf)
    assert not res.ok and res.broken_at_id == 3 and "edited" in res.reason


def test_detects_deleted_row(tmp_path):
    sf = make_log(tmp_path)
    fill(sf)
    with sf() as s:
        s.execute(delete(HaoAuditEvent).where(HaoAuditEvent.id == 2))
        s.commit()
    res = verify(sf)
    assert not res.ok and res.broken_at_id == 3 and "deleted" in res.reason


def test_detects_whole_chain_rewrite_via_anchor(tmp_path):
    sf = make_log(tmp_path)
    fill(sf)
    anchored = verify(sf).head_hash  # shipped to audit's mailbox / WORM storage
    with sf() as s:  # attacker wipes and rebuilds a "clean" chain
        s.execute(delete(HaoAuditEvent))
        s.commit()
    fill(sf, n=3)
    assert verify(sf).ok  # internally consistent...
    res = verify(sf, expected_head=anchored)
    assert not res.ok and "rewritten" in res.reason  # ...but doesn't contain the anchored head


def test_anchor_still_valid_after_new_events(tmp_path):
    sf = make_log(tmp_path)
    fill(sf, n=3)
    anchored = verify(sf).head_hash
    fill(sf, n=2)
    assert verify(sf, expected_head=anchored).ok


def test_concurrent_writers_do_not_fork_the_chain(tmp_path):
    sf = make_log(tmp_path)

    def writer(k):
        with sf() as s:
            for i in range(10):
                append(s, incident_sys_id=f"w{k}", actor=f"worker:{k}", action="x", payload={"i": i})

    threads = [threading.Thread(target=writer, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    res = verify(sf)
    assert res.ok and res.events == 40


def test_trail_per_incident(tmp_path):
    sf = make_log(tmp_path)
    fill(sf)
    with sf() as s:
        t = trail(s, "inc0")
    assert [e["payload"]["i"] for e in t] == [0, 2, 4]
    assert t[0]["ts"].endswith("Z") and len(t[0]["hash"]) == 64
