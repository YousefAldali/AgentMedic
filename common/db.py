# SQLite schema and small helpers. One short-lived connection per call keeps
# it safe to use from many threads and processes at once (WAL mode).
import json
import sqlite3
from typing import Any

from common.config import db_path

# runs.status: running | ok | failed | timeout | killed
# spans.kind: llm | tool | final | error
# control: shared runtime switches (LLM routing, circuit breakers, step caps, ...) that chaos
# and the doctor change from their own processes and the agents read on every run.
# cache: last good response per dependency and key, served when a fallback is enabled.
# purchase_orders: procurement's output, read by the finance agent (the cascade path).
# approvals: approval-tier actions waiting for a human click (dashboard or CLI).
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, agent TEXT, task TEXT,
    status TEXT,
    total_tokens INTEGER DEFAULT 0, total_steps INTEGER DEFAULT 0,
    started_at REAL, ended_at REAL
);
CREATE TABLE IF NOT EXISTS spans (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, agent TEXT, step INTEGER,
    kind TEXT,
    tool_name TEXT, input TEXT, output TEXT, error TEXT,
    latency_ms REAL, tokens_in INTEGER, tokens_out INTEGER,
    model TEXT, prompt_version INTEGER, ts REAL
);
CREATE INDEX IF NOT EXISTS idx_spans_agent_ts ON spans(agent, ts);
CREATE INDEX IF NOT EXISTS idx_runs_agent_ts ON runs(agent, started_at);
CREATE TABLE IF NOT EXISTS health_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, score REAL, details TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT,
    symptoms TEXT, hypotheses TEXT, actions TEXT, root_causes TEXT,
    opened_at REAL, resolved_at REAL, report_md TEXT, report_md_ar TEXT
);
CREATE TABLE IF NOT EXISTS actions_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INTEGER, action TEXT,
    target TEXT, tier TEXT, approved_by TEXT, result TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS doctor_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INTEGER, stage TEXT,
    message TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS prompts (
    agent TEXT, version INTEGER, prompt TEXT, created_at REAL,
    is_active INTEGER DEFAULT 0, known_good INTEGER DEFAULT 0,
    PRIMARY KEY (agent, version)
);
CREATE TABLE IF NOT EXISTS control (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id TEXT PRIMARY KEY, run_id TEXT, item TEXT, quantity INTEGER, vendor TEXT,
    unit_price REAL, total REAL, currency TEXT, raw TEXT, valid INTEGER, ts REAL
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INTEGER, action TEXT, target TEXT,
    reason TEXT, status TEXT, decided_by TEXT, requested_at REAL, decided_at REAL
);
CREATE TABLE IF NOT EXISTS cache (
    dependency TEXT, key TEXT, value TEXT, ts REAL,
    PRIMARY KEY (dependency, key)
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(reset: bool = False) -> None:
    if reset and db_path().exists():
        for suffix in ("", "-wal", "-shm"):
            p = db_path().with_name(db_path().name + suffix)
            if p.exists():
                p.unlink()
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Columns added after Phase 1; add them to older databases too.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(incidents)")}
        for col in ("first_symptom_at", "recovered_at"):
            if col not in cols:
                conn.execute(f"ALTER TABLE incidents ADD COLUMN {col} REAL")


def execute(sql: str, params: tuple = ()) -> int:
    # Run a write statement and return the last row id.
    conn = connect()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def query(sql: str, params: tuple = ()) -> list[dict]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def get_control(key: str, default: Any = None) -> Any:
    rows = query("SELECT value FROM control WHERE key = ?", (key,))
    return json.loads(rows[0]["value"]) if rows else default


def set_control(key: str, value: Any) -> None:
    execute(
        "INSERT INTO control(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )
