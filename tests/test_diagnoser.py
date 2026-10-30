# Diagnosis: the hypothesis board and the safety rules that decide when the doctor may act.
import json
import time

import pytest

from common import llm
from doctor import diagnoser
from doctor.diagnoser import Board, Fix, Hypothesis, apply_guards, diagnose, root_causes
from tests.conftest import candidate

MIXED = {"total": 6, "4xx": 2, "5xx": 3, "other": 1}
ONLY_5XX = {"total": 3, "4xx": 0, "5xx": 3, "other": 0}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(diagnoser, "read_state", lambda dep: {"service": dep, "status": "up"})


def board_of(*hyps) -> Board:
    return Board(hypotheses=list(hyps), source="llm")


def hyp(cause, conf, target="vendor_api", action="open_circuit_breaker") -> Hypothesis:
    return Hypothesis(cause=cause, affected_agents=["procurement"], confidence=conf,
                      proposed_fix=[Fix(action=action, target=target)] if target else [])


# ---------------------------------------------------------------- rule board (MOCK mode)
def test_f1_outage_confirmed_by_health_endpoint_is_root_cause():
    board = diagnose([candidate(status="down", errors=ONLY_5XX)], time.time() - 30)
    causes = root_causes(board)
    assert [h.cause for h in causes] == ["vendor_api outage (status=down)"]
    assert causes[0].confidence == 0.9
    assert {f.action for f in causes[0].proposed_fix} == {"open_circuit_breaker", "enable_fallback"}


def test_f1_keeps_rejected_alternatives_on_the_board():
    board = diagnose([candidate(status="down", errors=ONLY_5XX)], time.time() - 30)
    causes = [h.cause for h in board.hypotheses]
    assert "LLM endpoint degraded" in causes
    assert "bad prompt deployed for procurement" in causes
    assert len(board.hypotheses) >= 2


def test_f10_mixed_errors_and_healthy_endpoint_give_no_root_cause():
    board = diagnose([candidate(status="up", errors=MIXED)], time.time() - 30)
    assert root_causes(board) == []
    causes = {h.cause for h in board.hypotheses}
    assert "procurement tool sending bad requests to vendor_api" in causes


def test_f10_without_any_404s_still_no_root_cause():
    board = diagnose([candidate(status="up", errors=ONLY_5XX)], time.time() - 30)
    assert root_causes(board) == []
    assert max(h.confidence for h in board.hypotheses) <= 0.55


def test_f10_only_404s_still_no_root_cause():
    board = diagnose([candidate(status="up", errors={"total": 3, "4xx": 3, "5xx": 0, "other": 0})],
                     time.time() - 30)
    assert root_causes(board) == []


# ---------------------------------------------------------------- safety rules on any board
def test_guard_caps_confident_outage_when_health_says_up():
    out = apply_guards(board_of(hyp("Intermittent 503 outage", 0.95)), [candidate(status="up", errors=ONLY_5XX)])
    assert out.hypotheses[0].confidence == 0.55
    assert any("safety rule" in e for e in out.hypotheses[0].evidence_against)


@pytest.mark.parametrize("wording", [
    "vendor_api outage", "Intermittent 503 blip on get_quotes path", "Partial degradation", "Something odd",
])
def test_guard_does_not_depend_on_wording(wording):
    out = apply_guards(board_of(hyp(wording, 0.8)), [candidate(status="up", errors=ONLY_5XX)])
    assert root_causes(out) == []


@pytest.mark.parametrize("loose_target", ["vendor_api.get_quotes", "Vendor API", "the vendor_api service"])
def test_guard_understands_loose_target_names(loose_target):
    out = apply_guards(board_of(hyp("outage", 0.9, target=loose_target)), [candidate(status="up")])
    assert out.hypotheses[0].proposed_fix[0].target == "vendor_api"
    assert out.hypotheses[0].confidence == 0.55


def test_guard_caps_when_errors_are_mixed_even_if_health_is_down():
    out = apply_guards(board_of(hyp("outage", 0.9)), [candidate(status="down", errors=MIXED)])
    assert out.hypotheses[0].confidence == 0.55


def test_guard_leaves_real_outage_alone():
    out = apply_guards(board_of(hyp("outage", 0.9)), [candidate(status="down", errors=ONLY_5XX)])
    assert out.hypotheses[0].confidence == 0.9


def test_guard_ignores_hypotheses_without_a_service_fix():
    out = apply_guards(board_of(hyp("tool sends bad names", 0.7, target=None)), [candidate(status="up")])
    assert out.hypotheses[0].confidence == 0.7


def test_guard_reads_health_for_services_not_in_candidates(monkeypatch):
    monkeypatch.setattr(diagnoser, "read_state", lambda dep: {"service": dep, "status": "up"})
    out = apply_guards(board_of(hyp("hr db problem", 0.9, target="hr_db")), [candidate(status="down")])
    assert out.hypotheses[0].confidence == 0.55


# ---------------------------------------------------------------- real-mode (LLM) board
def fake_llm(monkeypatch, replies):
    replies = iter(replies)
    monkeypatch.setattr(diagnoser, "mock_llm", lambda: False)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: llm.LLMResponse(next(replies), [], 100, 50, "fake"))


def llm_json(*hyps):
    return json.dumps({"hypotheses": [
        {"cause": c, "affected_agents": ["procurement"], "evidence_for": [], "evidence_against": [],
         "confidence": conf, "proposed_fix": [{"action": "open_circuit_breaker", "target": "vendor_api"}]}
        for c, conf in hyps]})


def test_llm_board_is_used_and_still_guarded(monkeypatch):
    fake_llm(monkeypatch, [llm_json(("Intermittent outage", 0.6), ("noise", 0.2))])
    board = diagnose([candidate(status="up", errors=ONLY_5XX)], time.time() - 30)
    assert board.source == "llm"
    assert root_causes(board) == []


def test_llm_board_with_one_hypothesis_is_retried(monkeypatch):
    fake_llm(monkeypatch, [llm_json(("only one", 0.9)), llm_json(("outage", 0.9), ("other", 0.1))])
    board = diagnose([candidate(status="down", errors=ONLY_5XX)], time.time() - 30)
    assert board.source == "llm"
    assert [h.cause for h in root_causes(board)] == ["outage"]


def test_invalid_llm_replies_fall_back_to_rules(monkeypatch):
    fake_llm(monkeypatch, ["not json at all", '{"hypotheses": [{"cause": "x", "confidence": 7}]}'])
    board = diagnose([candidate(status="down", errors=ONLY_5XX)], time.time() - 30)
    assert board.source == "rules (LLM fallback)"
    assert root_causes(board)[0].cause == "vendor_api outage (status=down)"


def test_llm_error_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr(diagnoser, "mock_llm", lambda: False)

    def boom(*a, **k):
        raise llm.LLMError("429 rate limited")
    monkeypatch.setattr(llm, "chat", boom)
    board = diagnose([candidate(status="up", errors=MIXED)], time.time() - 30)
    assert board.source == "rules (LLM fallback)"
    assert root_causes(board) == []
