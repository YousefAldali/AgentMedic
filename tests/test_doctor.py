# The doctor's decisions end to end: fix, escalate, stay quiet after escalating,
# and only trust a service again after real test requests succeed.
import time

import httpx
import pytest

from common import db
from doctor import diagnoser
from doctor import doctor as doctor_mod
from doctor.diagnoser import Board, Hypothesis
from tests.conftest import candidate, symptom

MIXED = {"total": 6, "4xx": 2, "5xx": 3, "other": 1}


@pytest.fixture
def doc(monkeypatch):
    monkeypatch.setattr(diagnoser, "read_state", lambda dep: {"service": dep, "status": "up"})
    monkeypatch.setattr(doctor_mod, "read_state", lambda dep: {"service": dep, "status": "down"})
    verify_calls = []

    def fake_verify(agents, since, on_round):
        verify_calls.append(agents)
        return True, [{"round": 1, "ok": True, "passes": 1, "probes": {"procurement": "ok"}, "live_symptoms": []}]
    monkeypatch.setattr(doctor_mod, "verify", fake_verify)
    d = doctor_mod.Doctor()
    d.verify_calls = verify_calls
    monkeypatch.setattr(d, "check_everyone", lambda incident_id: None)
    return d


def use_candidates(monkeypatch, *cands):
    monkeypatch.setattr(doctor_mod, "correlate", lambda symptoms, since: list(cands))


def incident():
    return db.query("SELECT * FROM incidents ORDER BY id DESC LIMIT 1")[0]


def test_f1_is_fixed_and_verified(doc, monkeypatch):
    use_candidates(monkeypatch, candidate(status="down"))
    doc.handle([symptom()], time.time() - 30)
    assert incident()["status"] == "resolved"
    assert db.get_control("circuit.vendor_api") == "open"
    assert db.get_control("fallback.vendor_api") is True
    assert doc.verify_calls == [["procurement"]]
    assert "Resolved" in incident()["report_md"]


def test_f10_escalates_without_touching_anything(doc, monkeypatch):
    use_candidates(monkeypatch, candidate(status="up", errors=MIXED))
    doc.handle([symptom()], time.time() - 30)
    inc = incident()
    assert inc["status"] == "escalated"
    assert db.query("SELECT * FROM actions_log") == []
    assert db.get_control("circuit.vendor_api") is None
    assert doc.verify_calls == []
    assert "Escalated" in inc["report_md"] and "تم التصعيد" in inc["report_md_ar"]
    assert "None. No hypothesis was confident enough" in inc["report_md"]


def test_escalates_at_once_when_root_cause_has_no_safe_fix(doc, monkeypatch):
    use_candidates(monkeypatch, candidate(status="up", errors=MIXED))
    no_fix = Board(hypotheses=[
        Hypothesis(cause="tool sends a garbled item name", affected_agents=["procurement"], confidence=0.8),
        Hypothesis(cause="noise", affected_agents=["procurement"], confidence=0.1)])
    monkeypatch.setattr(doctor_mod, "diagnose", lambda c, s: no_fix)
    doc.handle([symptom()], time.time() - 30)
    assert incident()["status"] == "escalated"
    assert doc.verify_calls == []
    events = [e["message"] for e in db.query("SELECT message FROM doctor_events WHERE stage = 'ESCALATE'")]
    assert "no safe automatic fix" in events[0]


def test_same_problem_does_not_reopen_during_cooldown(doc, monkeypatch):
    use_candidates(monkeypatch, candidate(status="up", errors=MIXED))
    doc.handle([symptom()], time.time() - 30)
    handled = []
    monkeypatch.setattr(doctor_mod, "detect", lambda snap: [symptom()])
    monkeypatch.setattr(doc, "handle", lambda s, since: handled.append(s))
    monkeypatch.setattr(doc, "restore_dependencies", lambda: None)
    doc.tick()
    assert handled == []


def test_a_different_problem_still_opens_during_cooldown(doc, monkeypatch):
    use_candidates(monkeypatch, candidate(status="up", errors=MIXED))
    doc.handle([symptom()], time.time() - 30)
    handled = []
    monkeypatch.setattr(doctor_mod, "detect", lambda snap: [symptom(agent="hr", dependency="hr_db")])
    monkeypatch.setattr(doc, "handle", lambda s, since: handled.append(s))
    monkeypatch.setattr(doc, "restore_dependencies", lambda: None)
    doc.tick()
    assert len(handled) == 1


# ---------------------------------------------------------------- half-open check
def fake_http(monkeypatch, codes):
    codes = iter(codes)
    monkeypatch.setattr(doctor_mod.httpx, "get", lambda url, timeout: httpx.Response(next(codes)))


def test_trial_passes_only_if_all_five_requests_succeed(doc, monkeypatch):
    fake_http(monkeypatch, [200] * 5)
    assert doc.trial_requests("vendor_api") is True
    fake_http(monkeypatch, [200, 200, 200, 200, 503])
    assert doc.trial_requests("vendor_api") is False


def test_trial_fails_on_connection_error(doc, monkeypatch):
    def refuse(url, timeout):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(doctor_mod.httpx, "get", refuse)
    assert doc.trial_requests("vendor_api") is False


def test_breaker_stays_open_when_health_lies(doc, monkeypatch):
    db.set_control("circuit.vendor_api", "open")
    db.set_control("fallback.vendor_api", True)
    monkeypatch.setattr(doctor_mod, "read_state", lambda dep: {"service": dep, "status": "up"})
    fake_http(monkeypatch, [200, 503, 200, 503, 200])
    doc.restore_dependencies()
    assert db.get_control("circuit.vendor_api") == "open"
    assert db.get_control("fallback.vendor_api") is True


def test_breaker_closes_after_real_requests_succeed(doc, monkeypatch):
    db.set_control("circuit.vendor_api", "open")
    db.set_control("fallback.vendor_api", True)
    monkeypatch.setattr(doctor_mod, "read_state", lambda dep: {"service": dep, "status": "up"})
    fake_http(monkeypatch, [200] * 5)
    doc.restore_dependencies()
    assert db.get_control("circuit.vendor_api") == "closed"
    assert db.get_control("fallback.vendor_api") is False


def test_breaker_stays_open_while_health_says_down(doc, monkeypatch):
    db.set_control("circuit.vendor_api", "open")
    monkeypatch.setattr(doctor_mod, "read_state", lambda dep: {"service": dep, "status": "down"})
    fake_http(monkeypatch, [])
    doc.restore_dependencies()
    assert db.get_control("circuit.vendor_api") == "open"
