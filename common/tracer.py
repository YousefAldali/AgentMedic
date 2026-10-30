"""Writes agent runs and spans (every LLM call and tool call) to SQLite."""
import json
import time
import uuid
from typing import Any

from common import db

MAX_FIELD_CHARS = 4000


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text[:MAX_FIELD_CHARS]


def start_run(agent: str, task: str) -> str:
    run_id = uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO runs(run_id, agent, task, status, started_at) VALUES(?, ?, ?, 'running', ?)",
        (run_id, agent, task, time.time()),
    )
    return run_id


def log_span(run_id: str, agent: str, step: int, kind: str, *, tool_name: str | None = None,
             input: Any = None, output: Any = None, error: str | None = None,
             latency_ms: float = 0, tokens_in: int = 0, tokens_out: int = 0,
             model: str | None = None, prompt_version: int | None = None) -> None:
    db.execute(
        "INSERT INTO spans(run_id, agent, step, kind, tool_name, input, output, error, latency_ms, "
        "tokens_in, tokens_out, model, prompt_version, ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, agent, step, kind, tool_name, _dump(input), _dump(output), error,
         round(latency_ms, 1), tokens_in, tokens_out, model, prompt_version, time.time()),
    )


def end_run(run_id: str, status: str, total_tokens: int, total_steps: int) -> None:
    db.execute(
        "UPDATE runs SET status = ?, total_tokens = ?, total_steps = ?, ended_at = ? WHERE run_id = ?",
        (status, total_tokens, total_steps, time.time(), run_id),
    )
