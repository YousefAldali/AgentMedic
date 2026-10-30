# Shared test setup: every test gets its own empty database, MOCK_LLM is forced on,
# and helpers build symptoms and candidates without running the real services.
import time

import pytest

from common import db, registry
from doctor.correlator import Candidate
from doctor.detectors import Symptom


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "1")
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


def add_tool_span(agent="procurement", tool="get_quotes", error=None, ts=None, run_id="r1") -> None:
    db.execute("INSERT INTO spans(run_id, agent, step, kind, tool_name, error, latency_ms, ts) "
               "VALUES(?, ?, 1, 'tool', ?, ?, 10, ?)", (run_id, agent, tool, error, ts or time.time()))
