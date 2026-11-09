# Old records get cleaned up, the approval CLI rejects typos, and the doctor keeps
# working while it waits for a human.
import threading
import time

from common import db
from doctor import actions, approvals


def test_prune_removes_only_old_rows():
    old, new = time.time() - 7200, time.time()
    for ts in (old, new):
        db.execute("INSERT INTO spans(run_id, agent, kind, ts) VALUES('r', 'hr', 'llm', ?)", (ts,))
        db.execute("INSERT INTO doctor_events(stage, message, ts) VALUES('DETECT', 'x', ?)", (ts,))
    db.execute("INSERT INTO purchase_orders(po_id, run_id, ts) VALUES('PO-seed', 'seed', ?)", (old,))
    db.execute("INSERT INTO incidents(status, opened_at) VALUES('resolved', ?)", (old,))
    assert db.prune(3600) == 2
    assert len(db.query("SELECT * FROM spans")) == 1
    assert len(db.query("SELECT * FROM doctor_events")) == 1
    assert db.query("SELECT po_id FROM purchase_orders") == [{"po_id": "PO-seed"}]
    assert len(db.query("SELECT * FROM incidents")) == 1


def test_approval_cli_rejects_typos_and_missing_ids(capsys):
    aid = actions.request_approval("rollback_prompt", "hr", 1, "bad prompt")
    assert approvals.main(["aprove", str(aid)]) == 2
    assert approvals.main(["approve"]) == 2
    assert approvals.main(["approve", "999"]) == 1
    assert db.query("SELECT status FROM approvals WHERE id = ?", (aid,))[0]["status"] == "pending"
    assert approvals.main(["approve", str(aid)]) == 0
    assert db.query("SELECT status FROM approvals WHERE id = ?", (aid,))[0]["status"] == "approved"


def test_doctor_keeps_checking_while_waiting_for_approval():
    aid = actions.request_approval("rebuild_index", "knowledge_base", 1, "corrupt index")
    calls = []
    threading.Timer(0.35, lambda: actions.decide(aid, approve=True, by="test")).start()
    row = actions.wait_for_decision(aid, timeout_s=5, poll_s=0.05, every_s=0.1,
                                    while_waiting=lambda: calls.append(1))
    assert row["status"] == "approved"
    assert len(calls) >= 2
