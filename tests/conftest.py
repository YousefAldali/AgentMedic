# Shared test setup: every test gets its own empty database, the AI model is replaced by
# scripted replies (tests/fake_llm.py, no API key or network needed),
# and helpers build symptoms and candidates without running the real services.
import json
import time
import uuid

import pytest

from common import db, llm, registry
from doctor.correlator import Candidate
from doctor.detectors import Symptom


def fake_complete(model, messages, tools, agent):
    # Scripted agents answer like a real model; the doctor's own calls fail, so it uses
    # its rule-based board and report templates, which is what the tests check.
    from tests.fake_llm import fake_reply
    if agent == "doctor":
        raise llm.LLMError("no AI model in tests")
    content, calls = fake_reply(agent, messages, tools or [])
    tokens_in = len(json.dumps(messages, ensure_ascii=False)) // 4
    tokens_out = len(json.dumps([content, [c.__dict__ for c in calls]], ensure_ascii=False)) // 4
    return llm.LLMResponse(content, calls, tokens_in, tokens_out, model)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_complete", fake_complete)
    monkeypatch.setenv("FLEET_MODEL", "test-primary")
    monkeypatch.setenv("DOCTOR_MODEL", "test-doctor")
    monkeypatch.setenv("BACKUP_MODEL", "test-backup")
    monkeypatch.setenv("AUTO_APPROVE", "0")
    path = tmp_path / "test.db"
    monkeypatch.setattr(db, "db_path", lambda: path)
    db.init_db()
    registry.seed_prompts()
    yield path


def symptom(type="tool_error_rate", agent="procurement", dependency="vendor_api", value=0.6,
            threshold=0.5, evidence=None) -> Symptom:
    return Symptom(type, agent, dependency, value, threshold, time.time() - 5, evidence or ["3/5 get_quotes calls failed"])


def candidate(status="down", errors=None, dependency="vendor_api", agents=("procurement",),
              healthy=("hr",), integrity="ok") -> Candidate:
    state = {"service": dependency, "status": status, "data_integrity": integrity}
    errors = errors or {"total": 3, "4xx": 0, "5xx": 3, "other": 0}
    return Candidate(dependency, "independent", list(agents), list(healthy),
                     [symptom(dependency=dependency)], state, errors)


def add_tool_span(agent="procurement", tool="get_quotes", error=None, ts=None, run_id=None,
                  input='{"item": "laptops"}', output=None) -> None:
    db.execute("INSERT INTO spans(run_id, agent, step, kind, tool_name, input, output, error, latency_ms, ts) "
               "VALUES(?, ?, 1, 'tool', ?, ?, ?, ?, 10, ?)",
               (run_id or uuid.uuid4().hex[:8], agent, tool, input, output, error, ts or time.time()))
