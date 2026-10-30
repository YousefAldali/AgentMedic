# TREAT: the only things the doctor is allowed to do. No shell, no free-form code.
# Every action has a safety tier, saves the previous state so it can be undone,
# and is written to actions_log.
#   auto      -> runs immediately
#   approval  -> waits for a human click (or AUTO_APPROVE=1)
#   forbidden -> refused and logged (deleting data, editing records, credentials)
import json
import time
from dataclasses import dataclass
from typing import Callable

from common import db, registry
from common.config import env, get_config

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


ACTIONS = [
    Action("open_circuit_breaker", AUTO, "Stop calling a failing dependency (fail fast, no retry storm).",
           lambda t: _swap(f"circuit.{t}", "open"), _restore("circuit.{}", "closed")),
    Action("close_circuit_breaker", AUTO, "Resume calling a dependency that is healthy again.",
           lambda t: _swap(f"circuit.{t}", "closed"), _restore("circuit.{}", "closed")),
    Action("enable_fallback", AUTO, "Serve the last good cached data while a dependency's breaker is open.",
           lambda t: _swap(f"fallback.{t}", True), _restore("fallback.{}", False)),
    Action("disable_fallback", AUTO, "Stop serving cached data for a dependency.",
           lambda t: _swap(f"fallback.{t}", False), _restore("fallback.{}", False)),
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
