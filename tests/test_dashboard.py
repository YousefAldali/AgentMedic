# The dashboard renders with and without data, shows approvals, and the Approve button works.
import json
import time

import pytest
from streamlit.testing.v1 import AppTest

from common import db
from common.config import ROOT
from dashboard import data
from doctor.actions import request_approval


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(data, "read_state", lambda name: {"service": name, "status": "up"})
    monkeypatch.setattr(data, "active_faults", lambda: [])


def app() -> AppTest:
    return AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=30).run()


def add_incident(status="resolved") -> int:
    board = {"hypotheses": [
        {"cause": "vendor_api outage (status=down)", "affected_agents": ["procurement"], "evidence_for": ["503"],
         "evidence_against": [], "confidence": 0.9,
         "proposed_fix": [{"action": "open_circuit_breaker", "target": "vendor_api"}]},
        {"cause": "LLM endpoint degraded", "affected_agents": ["procurement"], "evidence_for": [],
         "evidence_against": ["hr is healthy"], "confidence": 0.05, "proposed_fix": []}],
        "source": "rules", "tokens": 0}
    now = time.time()
    return db.execute(
        "INSERT INTO incidents(status, symptoms, hypotheses, actions, root_causes, opened_at, first_symptom_at, "
        "recovered_at, report_md, report_md_ar) VALUES(?, ?, ?, '[]', ?, ?, ?, ?, ?, ?)",
        (status, json.dumps([{"agent": "procurement"}]), json.dumps(board), json.dumps(["vendor_api outage"]),
         now - 10, now - 15, now if status == "resolved" else None, "# Incident #1\n\nDone.", "# حادثة #1\n\nتم."))


def html_text(at) -> str:
    return " ".join(m.value for m in at.markdown)


def test_empty_dashboard_renders():
    at = app()
    assert not at.exception
    assert "Agents healthy" in html_text(at)
    assert "All quiet" in html_text(at)
    assert any("No incidents yet" in i.value for i in at.info)


def test_incident_shows_board_metrics_and_both_reports():
    add_incident()
    at = app()
    assert not at.exception
    page = html_text(at)
    assert "Time to detect</div><div class='v'>5.0s" in page
    assert "Time to recover</div><div class='v'>15.0s" in page
    assert "vendor_api outage (status=down)" in page and "90%" in page
    assert "INCIDENT #1 TIMELINE" in page and "Recovery verified" in page
    assert [t.label for t in at.tabs] == ["English", "العربية"]


def test_hypothesis_rows_mark_root_cause_and_rejected():
    rows = data.hypothesis_rows(data.incident(add_incident()))
    assert rows[0]["Verdict"] == "✅ root cause" and rows[1]["Verdict"] == "❌ rejected"


def test_approve_button_approves_the_pending_action():
    incident_id = add_incident(status="open")
    approval_id = request_approval("rollback_prompt", "hr", incident_id, "bad prompt deployed for hr")
    at = app()
    assert "Approve `rollback_prompt(hr)`?" in html_text(at)
    next(b for b in at.button if b.label == "Approve").click().run()
    row = db.query("SELECT status, decided_by FROM approvals WHERE id = ?", (approval_id,))[0]
    assert row == {"status": "approved", "decided_by": "dashboard"}


def test_reject_button_rejects():
    approval_id = request_approval("rebuild_index", "knowledge_base", add_incident(status="open"), "x")
    at = app()
    next(b for b in at.button if b.label == "Reject").click().run()
    assert db.query("SELECT status FROM approvals WHERE id = ?", (approval_id,))[0]["status"] == "rejected"


def test_agent_with_failing_health_is_red():
    db.execute("INSERT INTO health_checks(agent, score, details, ts) VALUES('hr', 0.0, '{}', ?)", (time.time(),))
    cards = {c["agent"]: c for c in data.agent_cards()}
    assert cards["hr"]["status"] == "red"
    assert cards["finance"]["status"] == "green"


def test_doctor_steps_show_waiting_for_approval():
    incident_id = add_incident(status="open")
    for stage in ("DETECT", "DIAGNOSE", "TREAT"):
        db.execute("INSERT INTO doctor_events(incident_id, stage, message, ts) VALUES(?, ?, 'x', ?)",
                   (incident_id, stage, time.time()))
    request_approval("rollback_prompt", "hr", incident_id, "bad prompt")
    steps = {s["key"]: s for s in data.doctor_steps(incident_id)["steps"]}
    assert steps["DETECT"]["done"] and steps["DIAGNOSE"]["done"]
    assert not steps["TREAT"]["done"] and steps["TREAT"]["current"]
    assert data.doctor_steps(incident_id)["waiting_approval"]


def test_timeline_points_are_in_order():
    points = data.timeline(add_incident())
    assert [p["label"] for p in points][0] == "First symptom"
    assert points[-1]["label"] == "Recovery verified"
    assert [p["t"] for p in points] == sorted(p["t"] for p in points)


