# TREAT: the only things the doctor is allowed to do. No shell, no free-form code.
# Every action has a safety tier, saves the previous state so it can be undone,
# and is written to actions_log.
#   auto      -> runs immediately
#   approval  -> waits for a human click (or AUTO_APPROVE=1)
#   forbidden -> refused and logged (deleting data, editing records, credentials)
import json
import time

import httpx
from dataclasses import dataclass
from typing import Callable

from common import db, registry
from common.config import env, get_config, service_url

AUTO, APPROVAL, FORBIDDEN = "auto", "approval", "forbidden"
FORBIDDEN_WORDS = ("delete", "drop", "truncate", "wipe", "purge", "credential", "password", "edit_record")


@dataclass
class Action:
    name: str
    tier: str
    description: str
    do: Callable[[str], dict]
    undo: Callable[[str, dict], None]


def _swap(key: str, value) -> dict:
    previous = db.get_control(key)
    db.set_control(key, value)
    return {"previous": previous}


def _restore(key: str, default):
    return lambda target, saved: db.set_control(key.format(target), saved.get("previous") or default)


def _set_caps(agent: str) -> dict:
    cfg = get_config()["doctor"]
    return {"max_steps": _swap(f"agent.{agent}.max_steps", cfg["safe_max_steps"])["previous"],
            "token_budget": _swap(f"agent.{agent}.token_budget", cfg["safe_token_budget"])["previous"]}


def _unset_caps(agent: str, saved: dict) -> None:
    for key in ("max_steps", "token_budget"):
        if saved.get(key) is None:
            db.execute("DELETE FROM control WHERE key = ?", (f"agent.{agent}.{key}",))
        else:
            db.set_control(f"agent.{agent}.{key}", saved[key])


def _rollback_prompt(agent: str) -> dict:
    previous = registry.active_info(agent)["version"]
    good = registry.last_known_good(agent)
    if good is None:
        raise ValueError(f"no known-good prompt for {agent}")
    registry.activate(agent, good)
    return {"previous": previous, "now": good}


def _rebuild_index(target: str) -> dict:
    r = httpx.post(f"{service_url(target)}/admin/rebuild", timeout=10)
    r.raise_for_status()
    return {"rebuilt": r.json()}


def _replay(agent: str) -> dict:
    # Re-runs the work that failed during the incident, once its cause is fixed.
    # procurement: regenerates the invalid POs (same PO ids, so invoices still match).
    # other agents: re-runs failed runs, or answers flagged as errors.
    since = time.time() - get_config()["doctor"]["replay_window_s"]
    fixed, tried = 0, 0
    if agent == "procurement":
        from fleet.procurement_agent import regenerate_po
        for row in db.query("SELECT po_id FROM purchase_orders WHERE valid = 0 AND ts >= ? AND run_id != 'seed' "
                            "ORDER BY ts DESC LIMIT 10", (since,)):
            tried += 1
            fixed += regenerate_po(row["po_id"])
        return {"replayed": tried, "fixed": fixed}
    from fleet.workload import build_fleet
    runner = build_fleet()[agent]
    rows = db.query("SELECT r.task, r.status, s.output FROM runs r LEFT JOIN spans s "
                    "ON s.run_id = r.run_id AND s.kind = 'final' WHERE r.agent = ? AND r.started_at >= ? "
                    "AND r.task NOT LIKE '[replay]%' ORDER BY r.started_at DESC LIMIT 30", (agent, since))
    failed = [r["task"] for r in rows if r["status"] != "ok" or '"decision": "error"' in (r["output"] or "")]
    for task in list(dict.fromkeys(failed))[:10]:
        tried += 1
        result = runner.run(f"[replay] {task}")
        fixed += result["status"] == "ok" and '"decision": "error"' not in (result["answer"] or "")
    return {"replayed": tried, "fixed": fixed}


def _noop(target: str, saved: dict) -> None:
    pass


ACTIONS = [
    Action("open_circuit_breaker", AUTO, "Stop calling a failing dependency (fail fast, no retry storm).",
           lambda t: _swap(f"circuit.{t}", "open"), _restore("circuit.{}", "closed")),
    Action("close_circuit_breaker", AUTO, "Resume calling a dependency that is healthy again.",
           lambda t: _swap(f"circuit.{t}", "closed"), _restore("circuit.{}", "closed")),
    Action("enable_fallback", AUTO, "Serve the last good cached data while a dependency's breaker is open.",
           lambda t: _swap(f"fallback.{t}", True), _restore("fallback.{}", False)),
    Action("disable_fallback", AUTO, "Stop serving cached data for a dependency.",
           lambda t: _swap(f"fallback.{t}", False), _restore("fallback.{}", False)),
    Action("switch_model", AUTO, "Route fleet LLM calls to the backup model (target: llm).",
           lambda t: _swap("llm.route_to_backup", True), _restore("llm.route_to_backup", False)),
    Action("kill_run", AUTO, "Stop every run of an agent that is in progress now (target: agent name).",
           lambda t: _swap(f"agent.{t}.kill_before", time.time()), _restore("agent.{}.kill_before", 0)),
    Action("set_step_cap", AUTO, "Cap an agent's steps and tokens per run so loops end quickly (target: agent).",
           _set_caps, _unset_caps),
    Action("replay_run", AUTO, "Re-run an agent's work that failed during the incident (target: agent).",
           _replay, _noop),
    Action("rollback_prompt", APPROVAL, "Activate the agent's last known-good prompt version (target: agent).",
           _rollback_prompt, lambda t, s: registry.activate(t, s["previous"]) if s.get("previous") else None),
    Action("rebuild_index", APPROVAL, "Rebuild the knowledge base search index from the source documents.",
           _rebuild_index, _noop),
]
REGISTRY = {a.name: a for a in ACTIONS}


def tier_of(name: str) -> str | None:
    if any(w in name.lower() for w in FORBIDDEN_WORDS):
        return FORBIDDEN
    action = REGISTRY.get(name)
    return action.tier if action else None


def _log(incident_id, name, target, tier, approved_by, result: dict) -> int:
    return db.execute(
        "INSERT INTO actions_log(incident_id, action, target, tier, approved_by, result, ts) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (incident_id, name, target, tier, approved_by, json.dumps(result, default=str), time.time()))


def execute(name: str, target: str, incident_id: int | None, approved_by: str | None = None) -> dict:
    tier = tier_of(name)
    out = {"action": name, "target": target, "tier": tier}
    if tier == FORBIDDEN:
        out.update(status="refused", detail="forbidden action: never destructive")
    elif tier is None:
        out.update(status="unavailable", detail="not a registered action")
    elif target not in set(get_config()["services"]) | set(registry.dependencies()) | {"llm"}:
        out.update(status="unavailable", detail=f"unknown target '{target}'")
    elif tier == APPROVAL and not approved_by:
        if env("AUTO_APPROVE", "0") == "1":
            approved_by = "auto-approve"
        else:
            out.update(status="pending", detail="waiting for human approval")
    if "status" in out:
        _log(incident_id, name, target, tier or "unknown", approved_by, out)
        return out
    try:
        saved = REGISTRY[name].do(target)
        out.update(status="done", saved=saved)
    except Exception as e:
        out.update(status="failed", detail=f"{type(e).__name__}: {e}")
    out["approved_by"] = approved_by or "doctor (auto tier)"
    _log(incident_id, name, target, tier, out["approved_by"], out)
    return out


def undo(name: str, target: str, saved: dict, incident_id: int | None) -> None:
    REGISTRY[name].undo(target, saved)
    _log(incident_id, f"undo:{name}", target, tier_of(name), "doctor", {"status": "done"})


# ---------------------------------------------------------- approvals
# Approval-tier actions wait here until a human approves or rejects them
# (dashboard button, or: python -m doctor.approvals approve <id>).
def request_approval(name: str, target: str, incident_id: int | None, reason: str) -> int:
    return db.execute(
        "INSERT INTO approvals(incident_id, action, target, reason, status, requested_at) "
        "VALUES(?, ?, ?, ?, 'pending', ?)", (incident_id, name, target, reason, time.time()))


def decide(approval_id: int, approve: bool, by: str = "human") -> None:
    db.execute("UPDATE approvals SET status = ?, decided_by = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
               ("approved" if approve else "rejected", by, time.time(), approval_id))


def wait_for_decision(approval_id: int, timeout_s: float, poll_s: float = 1.0) -> dict:
    deadline = time.time() + timeout_s
    while True:
        row = db.query("SELECT * FROM approvals WHERE id = ?", (approval_id,))[0]
        if row["status"] != "pending" or time.time() >= deadline:
            return row
        time.sleep(poll_s)


def pending_approvals() -> list[dict]:
    return db.query("SELECT * FROM approvals WHERE status = 'pending' ORDER BY id")
