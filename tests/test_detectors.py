# Detection rules: when a symptom must fire, and when it must stay quiet.
import time

from common import db
from doctor.detectors import detect
from doctor.monitor import snapshot
from tests.conftest import add_tool_span

E503 = 'HTTP 503: {"detail":"vendor_api unavailable"}'
E404 = "HTTP 404: {\"detail\":\"No vendors for item 'monitor-x'\"}"


def types(symptoms):
    return {s.type for s in symptoms}


def test_quiet_when_all_calls_succeed():
    start = time.time() - 1
    for _ in range(5):
        add_tool_span()
    assert detect(snapshot(start)) == []


def test_error_rate_fires_at_half_failed():
    start = time.time() - 1
    for err in (E503, None, E503, None):
        add_tool_span(error=err)
    found = detect(snapshot(start))
    assert "tool_error_rate" in types(found)
    assert found[0].dependency == "vendor_api"


def test_error_rate_needs_minimum_calls():
    start = time.time() - 1
    add_tool_span(error=E503)
    add_tool_span(error=E503)
    assert "tool_error_rate" not in types(detect(snapshot(start)))


def test_streak_fires_on_three_failures_in_a_row():
    start = time.time() - 1
    for err in (None, E503, E404, E503):
        add_tool_span(error=err)
    assert "tool_error_streak" in types(detect(snapshot(start)))


def test_streak_resets_after_a_success():
    start = time.time() - 1
    for err in (E503, E503, E503, None):
        add_tool_span(error=err)
    assert "tool_error_streak" not in types(detect(snapshot(start)))


def test_mixed_404_and_503_still_detected():
    start = time.time() - 1
    for err in (E404, E503, None, E503, E404, None):
        add_tool_span(error=err)
    assert "tool_error_rate" in types(detect(snapshot(start)))


def test_old_spans_outside_window_are_ignored():
    for _ in range(5):
        add_tool_span(error=E503, ts=time.time() - 300)
    assert detect(snapshot(time.time() - 30)) == []


def test_llm_errors_ignore_step_cap_and_budget_messages():
    start = time.time() - 1
    for msg in ("step cap 50 reached", "token budget 1000 exceeded"):
        db.execute("INSERT INTO spans(run_id, agent, step, kind, error, ts) VALUES('r', 'hr', 1, 'error', ?, ?)",
                   (msg, time.time()))
    assert "llm_error_rate" not in types(detect(snapshot(start)))
    for _ in range(2):
        db.execute("INSERT INTO spans(run_id, agent, step, kind, error, ts) VALUES('r', 'hr', 1, 'error', ?, ?)",
                   ("429 Too Many Requests", time.time()))
    assert "llm_error_rate" in types(detect(snapshot(start)))
