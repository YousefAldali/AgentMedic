# Phase 4: new detectors, new actions, approvals, cascade and multi-fault diagnosis.
import json
import time

import pytest

from common import db, registry
from doctor import actions, diagnoser
from doctor.correlator import Candidate
from doctor.detectors import detect
from doctor.diagnoser import cause_ids, diagnose, root_causes
from doctor.doctor import order_fixes
from doctor.monitor import snapshot
from tests.conftest import add_tool_span, symptom


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(diagnoser, "read_state", lambda dep: {"service": dep, "status": "up"})


def types(found):
    return {s.type for s in found}


# ---------------------------------------------------------------- detectors
def test_loop_detector_fires_on_same_call_repeated_in_one_run():
    start = time.time() - 1
    for _ in range(5):
        add_tool_span(run_id="loop1", input='{"item": "printers", "quantity": 5}')
    found = detect(snapshot(start))
    assert "repeated_call_loop" in types(found)
    assert found[0].agent == "procurement" and found[0].dependency is None


def test_loop_detector_ignores_repeats_across_different_runs():
    start = time.time() - 1
    for _ in range(6):
        add_tool_span(input='{"item": "printers", "quantity": 5}')
    assert "repeated_call_loop" not in types(detect(snapshot(start)))


def test_token_spike_detector():
    start = time.time() - 1
    for _ in range(4):
        db.execute("INSERT INTO spans(run_id, agent, step, kind, tokens_in, tokens_out, latency_ms, ts) "
                   "VALUES('big', 'procurement', 1, 'llm', 5000, 100, 100, ?)", (time.time(),))
    assert "token_spike" in types(detect(snapshot(start)))


def test_empty_results_detector_points_at_knowledge_base():
    start = time.time() - 1
    empty = json.dumps({"query": "x", "results": []})
    junk = json.dumps({"query": "x", "results": [{"title": "t", "snippet": "", "score": 0.0}]})
    for out in (empty, junk, empty):
        add_tool_span(agent="support", tool="search_knowledge_base", output=out)
    found = [s for s in detect(snapshot(start)) if s.type == "empty_results_rate"]
    assert found and found[0].dependency == "knowledge_base"


def test_empty_results_detector_quiet_on_good_results():
    start = time.time() - 1
    good = json.dumps({"results": [{"title": "Refund Policy", "snippet": "14 days", "score": 1.2}]})
    for _ in range(4):
        add_tool_span(agent="support", tool="search_knowledge_base", output=good)
    assert "empty_results_rate" not in types(detect(snapshot(start)))


def test_health_drop_uses_latest_score_only():
    start = time.time() - 1
    db.execute("INSERT INTO health_checks(agent, score, details, ts) VALUES('hr', 0.25, '{}', ?)", (time.time(),))
    assert "health_score_drop" in types(detect(snapshot(start)))
    db.execute("INSERT INTO health_checks(agent, score, details, ts) VALUES('hr', 1.0, '{}', ?)", (time.time() + 1,))
    assert "health_score_drop" not in types(detect(snapshot(start)))


def test_invalid_purchase_orders_flag_finance_with_procurement_as_cause():
    start = time.time() - 1
    for i, valid in enumerate((0, 0, 1)):
        db.execute("INSERT INTO purchase_orders(po_id, run_id, valid, ts) VALUES(?, 'r', ?, ?)",
                   (f"PO-{i}", valid, time.time()))
    found = [s for s in detect(snapshot(start)) if s.type == "upstream_bad_output"]
    assert [(s.agent, s.dependency) for s in found] == [("finance", "procurement")]


# ---------------------------------------------------------------- diagnosis
def bad_prompt(agent):
    registry.activate(agent, registry.add_version(agent, "bad", known_good=False))


def agent_cand(agent, *syms):
    return Candidate(f"agent:{agent}", "agent", [agent], [], list(syms), None)


def test_bad_prompt_is_root_cause_when_agent_answers_wrong():
    bad_prompt("hr")
    board = diagnose([agent_cand("hr", symptom("health_score_drop", "hr", None, 0.25, 0.75))], time.time() - 30)
    assert {i for h in root_causes(board) for i in cause_ids(h)} == {"prompt:hr"}


def test_known_good_prompt_and_wrong_answers_escalates():
    board = diagnose([agent_cand("hr", symptom("health_score_drop", "hr", None, 0.25, 0.75))], time.time() - 30)
    assert root_causes(board) == []


def test_runaway_loop_gets_kill_and_cap():
    board = diagnose([agent_cand("procurement", symptom("repeated_call_loop", "procurement", None, 8, 5))],
                     time.time() - 30)
    assert {i for h in root_causes(board) for i in cause_ids(h)} == {"loop:procurement"}
    assert {f.action for h in root_causes(board) for f in h.proposed_fix} == {"kill_run", "set_step_cap"}


def test_corrupted_index_gets_rebuild_not_breaker():
    kb = Candidate("knowledge_base", "shared", ["hr", "support"], [],
                   [symptom("empty_results_rate", "support", "knowledge_base", 0.8, 0.5)],
                   {"status": "up", "index_integrity": "failed"}, {"total": 0, "4xx": 0, "5xx": 0, "other": 0})
    board = diagnose([kb], time.time() - 30)
    fixes = {(f.action, f.target) for h in root_causes(board) for f in h.proposed_fix}
    assert fixes == {("rebuild_index", "knowledge_base")}


def test_slow_llm_on_all_agents_switches_model():
    llm = Candidate("llm", "shared", ["finance", "hr", "procurement", "support"], [],
                    [symptom("latency_high", a, "llm", 6200, 5000) for a in ("hr", "support")], None)
    board = diagnose([llm], time.time() - 30)
    assert {i for h in root_causes(board) for i in cause_ids(h)} == {"llm"}


def test_cascade_blames_upstream_prompt_and_replays_both():
    bad_prompt("procurement")
    cascade = Candidate("procurement", "cascade", ["finance"], [],
                        [symptom("upstream_bad_output", "finance", "procurement", 0.67, 0.5)], None)
    board = diagnose([cascade], time.time() - 30)
    causes = root_causes(board)
    assert {i for h in causes for i in cause_ids(h)} == {"prompt:procurement"}
    replays = {f.target for h in causes for f in h.proposed_fix if f.action == "replay_run"}
    assert replays == {"procurement", "finance"}


def test_two_independent_faults_both_found():
    bad_prompt("hr")
    vendor = Candidate("vendor_api", "independent", ["procurement"], [],
                       [symptom()], {"status": "down"}, {"total": 3, "4xx": 0, "5xx": 3, "other": 0})
    hr = agent_cand("hr", symptom("health_score_drop", "hr", None, 0.0, 0.75))
    board = diagnose([vendor, hr], time.time() - 30)
    assert {i for h in root_causes(board) for i in cause_ids(h)} == {"vendor_api", "prompt:hr"}
    llm = next(h for h in board.hypotheses if h.cause == "LLM endpoint degraded")
    assert llm.confidence < 0.6 and any("healthy" in e for e in llm.evidence_against)


# ---------------------------------------------------------------- actions and approvals
def test_fix_order_auto_then_approval_then_replay():
    fixes = [("replay_run", "finance"), ("rollback_prompt", "procurement"), ("kill_run", "procurement"),
             ("replay_run", "procurement"), ("set_step_cap", "procurement")]
    ordered = [a for a, _ in order_fixes(fixes)]
    assert ordered[:2] == ["kill_run", "set_step_cap"]
    assert ordered[2] == "rollback_prompt"
    assert ordered[3:] == ["replay_run", "replay_run"]


def test_rollback_prompt_and_undo():
    bad_prompt("hr")
    out = actions.execute("rollback_prompt", "hr", 1, approved_by="tester")
    assert out["status"] == "done" and registry.active_info("hr")["version"] == 1
    actions.undo("rollback_prompt", "hr", out["saved"], 1)
    assert registry.active_info("hr")["version"] == 2


def test_rollback_needs_approval():
    bad_prompt("hr")
    assert actions.execute("rollback_prompt", "hr", 1)["status"] == "pending"
    assert registry.active_info("hr")["version"] == 2


def test_switch_model_and_undo():
    out = actions.execute("switch_model", "llm", 1)
    assert db.get_control("llm.route_to_backup") is True
    actions.undo("switch_model", "llm", out["saved"], 1)
    assert db.get_control("llm.route_to_backup") is False


def test_step_cap_and_undo_restore_defaults():
    out = actions.execute("set_step_cap", "procurement", 1)
    assert db.get_control("agent.procurement.max_steps") == 4
    actions.undo("set_step_cap", "procurement", out["saved"], 1)
    assert db.get_control("agent.procurement.max_steps") is None


def test_kill_run_stops_a_running_agent(monkeypatch):
    from fleet.procurement_agent import ProcurementAgent
    db.set_control("chaos.procurement.stuck_item", "printers")
    calls = {"n": 0}
    real_chat = diagnoser.llm.chat

    def chat_and_kill(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            actions.execute("kill_run", "procurement", 1)
        return real_chat(*a, **k)
    monkeypatch.setattr("fleet.base_agent.llm.chat", chat_and_kill)
    result = ProcurementAgent().run("Compare quotes for 5 printers.")
    assert result["status"] == "killed"
    assert calls["n"] == 3


def test_approval_lifecycle():
    aid = actions.request_approval("rollback_prompt", "hr", 7, "bad prompt deployed for hr")
    assert [r["id"] for r in actions.pending_approvals()] == [aid]
    actions.decide(aid, approve=True, by="demo")
    row = actions.wait_for_decision(aid, timeout_s=1)
    assert row["status"] == "approved" and row["decided_by"] == "demo"
    assert actions.pending_approvals() == []


def test_unanswered_approval_times_out_as_pending():
    aid = actions.request_approval("rebuild_index", "knowledge_base", 7, "corrupted index")
    assert actions.wait_for_decision(aid, timeout_s=0.2, poll_s=0.05)["status"] == "pending"


def test_rejected_approval_is_not_overwritten():
    aid = actions.request_approval("rebuild_index", "knowledge_base", 7, "x")
    actions.decide(aid, approve=False, by="demo")
    actions.decide(aid, approve=True, by="someone else")
    assert actions.wait_for_decision(aid, 0.1)["status"] == "rejected"
