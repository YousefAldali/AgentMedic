# Golden health checks, the finance agent and the procurement -> finance hand-off.
import json

import pytest

from common import db
from doctor import monitor
from doctor.monitor import check_answer, golden_set
from fleet.finance_agent import FinanceAgent, get_purchase_order, seed_golden_po
from fleet.base_agent import ToolError
from fleet.procurement_agent import save_po


@pytest.mark.parametrize("answer, expect, ok", [
    ("E002 has 21 leave days left.", {"regex": "\\b21\\b"}, True),
    ("E002 has 210 leave days left.", {"regex": "\\b21\\b"}, False),
    ("رصيد الإجازات هو 9 يومًا", {"regex": "\\b9\\b|٩"}, True),
    ("Click Forgot Password on the login page", {"any": ["forgot password"]}, True),
    ("Contact support", {"any": ["forgot password"]}, False),
    ("Up to 2 days with approval", {"contains": ["2 days", "approval"]}, True),
    ("Up to 2 days", {"contains": ["2 days", "approval"]}, False),
    ('{"status": "ok", "po": {"vendor": "eXtra Enterprise", "total": 39500.0}}',
     {"json": {"status": "ok", "po.vendor": "extra enterprise", "po.total": 39500}}, True),
    ('{"status": "ok", "po": {"vendor": "eXtra Enterprise", "total": "39,500"}}',
     {"json": {"po.total": 39500}}, False),
    ('Here you go: {"decision": "approved"} thanks', {"json": {"decision": "approved"}}, True),
    ("not json", {"json": {"decision": "approved"}}, False),
    (None, {"regex": "x"}, False),
])
def test_check_answer(answer, expect, ok):
    assert check_answer(answer, expect) is ok


@pytest.mark.parametrize("agent", ["hr", "procurement", "finance", "support"])
def test_every_agent_has_golden_checks_in_both_languages(agent):
    cases = golden_set(agent)
    assert len(cases) >= 3
    has_arabic = any(any("؀" <= ch <= "ۿ" for ch in c["question"]) for c in cases)
    assert has_arabic


def test_health_check_score_is_fraction_passed_and_saved(monkeypatch):
    class FakeAgent:
        def run(self, task):
            return {"status": "ok", "answer": "21" if "E002" in task else "no idea"}
    monkeypatch.setattr("fleet.workload.build_fleet", lambda: {"hr": FakeAgent()})
    r = monitor.run_health_check("hr")
    assert r["total"] == 4 and r["passed"] == 1 and r["score"] == 0.25
    row = db.query("SELECT agent, score, details FROM health_checks")[0]
    assert row["agent"] == "hr" and row["score"] == 0.25
    assert json.loads(row["details"])["source"] == "schedule"


def test_failed_run_never_passes_a_health_check(monkeypatch):
    class BrokenAgent:
        def run(self, task):
            return {"status": "failed", "answer": "21"}
    monkeypatch.setattr("fleet.workload.build_fleet", lambda: {"hr": BrokenAgent()})
    assert monitor.run_health_check("hr")["score"] == 0


def test_valid_po_from_procurement_is_stored():
    answer = json.dumps({"status": "ok", "po": {"po_id": "po-abc123", "item": "laptops", "quantity": 10,
                                                 "vendor": "eXtra Enterprise", "unit_price": 3950, "total": 39500}})
    save_po("run1", answer)
    po = get_purchase_order("PO-ABC123")
    assert po["valid"] == 1 and po["total"] == 39500


def test_malformed_po_is_stored_as_invalid():
    answer = json.dumps({"status": "ok", "po": {"po_id": "PO-BAD1", "quantity": 10, "vendor": "X",
                                                 "unit_price": 100, "total": 999}})
    save_po("run2", answer)
    assert get_purchase_order("PO-BAD1")["valid"] == 0


def test_answer_without_po_is_ignored():
    save_po("run3", '{"status": "failed", "summary": "no quotes"}')
    save_po("run4", "not json")
    assert db.query("SELECT COUNT(*) AS n FROM purchase_orders")[0]["n"] == 0


def test_missing_po_is_a_tool_error():
    with pytest.raises(ToolError, match="not found"):
        get_purchase_order("PO-NOPE")


@pytest.mark.parametrize("task, decision", [
    ("Validate invoice INV-1 from eXtra Enterprise for 39500 SAR against PO-GOLD01.", "approved"),
    ("Validate invoice INV-2 from eXtra Enterprise for 45000 SAR against PO-GOLD01.", "rejected"),
    ("Validate invoice INV-3 from Jarir Business for 39500 SAR against PO-GOLD01.", "rejected"),
    ("تحقق من الفاتورة INV-4 من eXtra Enterprise بمبلغ 39500 ريال مقابل أمر الشراء PO-GOLD01.", "approved"),
    ("Validate invoice INV-5 from eXtra Enterprise for 39500 SAR against PO-MISSING.", "error"),
])
def test_finance_agent_decisions_in_mock_mode(task, decision):
    seed_golden_po()
    result = FinanceAgent().run(task)
    assert result["status"] == "ok"
    assert json.loads(result["answer"])["decision"] == decision


def test_finance_rejects_po_that_procurement_got_wrong():
    save_po("run5", json.dumps({"status": "ok", "po": {"po_id": "PO-BAD2", "quantity": 5, "vendor": "Gulf IT Supply",
                                                      "unit_price": 100, "total": 1}}))
    result = FinanceAgent().run("Validate invoice INV-6 from Gulf IT Supply for 1 SAR against PO-BAD2.")
    assert json.loads(result["answer"])["decision"] == "error"


def test_health_check_stopped_mid_run_saves_nothing(monkeypatch):
    import threading
    stop = threading.Event()

    class SlowAgent:
        def run(self, task):
            stop.set()
            return {"status": "failed", "answer": ""}
    monkeypatch.setattr("fleet.workload.build_fleet", lambda: {"hr": SlowAgent()})
    assert monitor.run_health_check("hr", stop=stop) is None
    assert db.query("SELECT COUNT(*) AS n FROM health_checks")[0]["n"] == 0
