# Correlation: grouping symptoms by dependency and counting error types.
import time

from doctor import correlator
from tests.conftest import add_tool_span, symptom


def test_error_breakdown_splits_bad_requests_from_server_failures():
    start = time.time() - 1
    add_tool_span(error="HTTP 404: not found")
    add_tool_span(error="HTTP 503: unavailable")
    add_tool_span(error="HTTP 500: internal error")
    add_tool_span(error="vendor_api circuit open")
    add_tool_span(error=None)
    out = correlator.error_breakdown("vendor_api", start)
    assert out == {"total": 4, "4xx": 1, "5xx": 2, "other": 1}


def test_error_breakdown_only_counts_tools_of_that_dependency():
    start = time.time() - 1
    add_tool_span(tool="get_leave_balance", agent="hr", error="HTTP 503: x")
    assert correlator.error_breakdown("vendor_api", start)["total"] == 0


def test_correlate_marks_independent_cause_and_healthy_agents(monkeypatch):
    monkeypatch.setattr(correlator, "read_state", lambda dep: {"service": dep, "status": "down"})
    found = correlator.correlate([symptom()], time.time() - 30)
    assert len(found) == 1
    c = found[0]
    assert c.dependency == "vendor_api"
    assert c.kind == "independent"
    assert c.agents == ["procurement"]
    assert c.state["status"] == "down"


def test_correlate_marks_shared_cause_when_two_agents_hit_same_dependency(monkeypatch):
    monkeypatch.setattr(correlator, "read_state", lambda dep: None)
    found = correlator.correlate([symptom(agent="hr", dependency="llm", type="latency_high"),
                                  symptom(agent="procurement", dependency="llm", type="latency_high")],
                                 time.time() - 30)
    assert found[0].kind == "shared"
    assert found[0].agents == ["hr", "procurement"]


def test_symptom_without_dependency_becomes_agent_candidate(monkeypatch):
    monkeypatch.setattr(correlator, "read_state", lambda dep: None)
    found = correlator.correlate([symptom(dependency=None, type="run_failure_rate")], time.time() - 30)
    assert found[0].kind == "agent"
    assert found[0].dependency == "agent:procurement"
