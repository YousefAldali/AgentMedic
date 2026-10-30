# MONITOR: reads everything the fleet recorded in a time window.
import time
from dataclasses import dataclass

from common import db


@dataclass
class Snapshot:
    since: float
    now: float
    tool_spans: list[dict]
    llm_spans: list[dict]
    error_spans: list[dict]
    runs: list[dict]


def snapshot(since: float) -> Snapshot:
    return Snapshot(
        since=since,
        now=time.time(),
        tool_spans=db.query(
            "SELECT run_id, agent, tool_name, error, latency_ms, ts FROM spans "
            "WHERE kind = 'tool' AND ts >= ? ORDER BY ts", (since,)),
        llm_spans=db.query(
            "SELECT run_id, agent, latency_ms, tokens_in, tokens_out, model, ts FROM spans "
            "WHERE kind = 'llm' AND ts >= ? ORDER BY ts", (since,)),
        error_spans=db.query(
            "SELECT run_id, agent, error, ts FROM spans WHERE kind = 'error' AND ts >= ? ORDER BY ts",
            (since,)),
        runs=db.query(
            "SELECT run_id, agent, status, total_tokens, total_steps, started_at, ended_at FROM runs "
            "WHERE started_at >= ? AND status != 'running'", (since,)),
    )
