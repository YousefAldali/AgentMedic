# CORRELATE: groups symptoms by the dependency they point at, using the dependency map.
# One dependency hurting several agents -> shared-cause candidate.
# One dependency hurting one agent -> independent-cause candidate.
# Symptoms with no dependency -> agent-level candidate (prompt, logic, loop).
from collections import defaultdict
from dataclasses import asdict, dataclass

import httpx

from common import db, registry
from common.config import get_config, service_url
from doctor.detectors import Symptom


@dataclass
class Candidate:
    dependency: str
    kind: str
    agents: list[str]
    healthy_agents: list[str]
    symptoms: list[Symptom]
    state: dict | None
    errors: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def read_state(dependency: str) -> dict | None:
    # True state from the service's own health endpoint, never from an agent's answer.
    if dependency not in get_config()["services"]:
        return None
    try:
        return httpx.get(f"{service_url(dependency)}/admin/state", timeout=2).json()
    except httpx.HTTPError as e:
        return {"service": dependency, "status": "unreachable", "error": str(e)}


def error_breakdown(dependency: str, since: float) -> dict:
    # Splits a dependency's tool errors by kind: 4xx = bad requests from the caller,
    # 5xx = the service failing, other = timeouts, open breakers, ...
    tools = [t for t, d in (get_config().get("tools") or {}).items() if d == dependency]
    if not tools:
        return {"total": 0, "4xx": 0, "5xx": 0, "other": 0}
    marks = ",".join("?" * len(tools))
    rows = db.query(f"SELECT error FROM spans WHERE kind = 'tool' AND error IS NOT NULL "
                    f"AND ts >= ? AND tool_name IN ({marks})", (since, *tools))
    out = {"total": len(rows), "4xx": 0, "5xx": 0, "other": 0}
    for r in rows:
        kind = "4xx" if r["error"].startswith("HTTP 4") else "5xx" if r["error"].startswith("HTTP 5") else "other"
        out[kind] += 1
    return out


def correlate(symptoms: list[Symptom], since: float) -> list[Candidate]:
    deps = registry.dependencies()
    sick = {s.agent for s in symptoms}
    groups = defaultdict(list)
    for s in symptoms:
        groups[s.dependency or f"agent:{s.agent}"].append(s)

    out = []
    for key, syms in groups.items():
        agents = sorted({s.agent for s in syms})
        if key.startswith("agent:"):
            out.append(Candidate(key, "agent", agents, [], syms, None))
            continue
        users = sorted(a for a, d in deps.items() if key in d)
        healthy = [u for u in users if u not in sick]
        kind = "shared" if len(agents) > 1 else "independent"
        out.append(Candidate(key, kind, agents, healthy, syms, read_state(key), error_breakdown(key, since)))
    return out
