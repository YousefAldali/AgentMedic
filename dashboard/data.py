# Everything the dashboard shows, read from the live database and the services.
# Kept separate from the Streamlit code so it can be tested and reused (office view).
import json
import time

import httpx

from common import db, registry
from common.config import get_config, service_url
from doctor.correlator import read_state
from doctor.detectors import detect
from doctor.monitor import snapshot

WINDOW_S = 60


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[int(0.95 * (len(values) - 1))]


def open_incident_agents() -> set[str]:
    agents = set()
    for inc in db.query("SELECT symptoms FROM incidents WHERE status = 'open'"):
        agents |= {s["agent"] for s in json.loads(inc["symptoms"] or "[]")}
    return agents


def agent_cards() -> list[dict]:
    now = time.time()
    since = now - WINDOW_S
    symptoms = detect(snapshot(now - get_config()["doctor"]["window_s"]))
    treating = open_incident_agents()
    deps = registry.dependencies()
    cards = []
    for agent in deps:
        tools = db.query("SELECT error FROM spans WHERE kind = 'tool' AND agent = ? AND ts >= ?", (agent, since))
        llm = db.query("SELECT latency_ms, tokens_in, tokens_out, model FROM spans "
                       "WHERE kind = 'llm' AND agent = ? AND ts >= ? ORDER BY ts", (agent, since))
        runs = db.query("SELECT status FROM runs WHERE agent = ? AND started_at >= ? AND status != 'running'",
                        (agent, since))
        health = db.query("SELECT score, ts FROM health_checks WHERE agent = ? ORDER BY id DESC LIMIT 1", (agent,))
        prompt = registry.active_info(agent)
        good = registry.last_known_good(agent)
        mine = [s for s in symptoms if s.agent == agent]
        score = health[0]["score"] if health else None
        tool_err = sum(1 for t in tools if t["error"]) / len(tools) if tools else 0.0
        run_err = sum(1 for r in runs if r["status"] != "ok") / len(runs) if runs else 0.0
        degraded = (any(db.get_control(f"fallback.{d}") for d in deps[agent])
                    or db.get_control(f"agent.{agent}.max_steps") is not None
                    or db.get_control("llm.route_to_backup"))
        if mine or (score is not None and score < get_config()["doctor"]["health_drop_below"]):
            status = "red"
        elif agent in treating or degraded or (score is not None and score < 1.0):
            status = "yellow"
        else:
            status = "green"
        cards.append({
            "agent": agent, "status": status, "health": score,
            "error_rate": round(max(tool_err, run_err), 2),
            "p95_ms": round(_p95([s["latency_ms"] for s in llm])),
            "tokens_per_min": round(sum((s["tokens_in"] or 0) + (s["tokens_out"] or 0) for s in llm) * 60 / WINDOW_S),
            "prompt_version": prompt["version"], "prompt_known_good": prompt["version"] == good,
            "model": llm[-1]["model"] if llm else "-",
            "symptoms": [s.describe() for s in mine],
            "under_treatment": agent in treating,
            "runs_per_min": len(runs),
        })
    return cards


def services() -> list[dict]:
    out = []
    for name in get_config()["services"]:
        state = read_state(name) or {}
        integrity = state.get("data_integrity") or state.get("index_integrity") or "ok"
        out.append({"name": name, "status": state.get("status", "unknown"), "integrity": integrity,
                    "breaker": db.get_control(f"circuit.{name}") or "closed",
                    "fallback": bool(db.get_control(f"fallback.{name}"))})
    out.append({"name": "llm", "status": "backup" if db.get_control("llm.route_to_backup") else "primary",
                "integrity": "ok", "breaker": "closed", "fallback": bool(db.get_control("llm.route_to_backup"))})
    return out


def doctor_feed(limit: int = 30) -> list[dict]:
    return db.query("SELECT incident_id, stage, message, ts FROM doctor_events ORDER BY id DESC LIMIT ?", (limit,))


def incidents(limit: int = 20) -> list[dict]:
    rows = db.query("SELECT id, status, opened_at, first_symptom_at, recovered_at, resolved_at, root_causes "
                    "FROM incidents ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["root_causes"] = json.loads(r["root_causes"] or "[]")
        r["detect_s"] = round(r["opened_at"] - r["first_symptom_at"], 1) if r["first_symptom_at"] else None
        r["recover_s"] = (round(r["recovered_at"] - r["first_symptom_at"], 1)
                          if r["recovered_at"] and r["first_symptom_at"] else None)
    return rows


def incident(incident_id: int) -> dict | None:
    rows = db.query("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    if not rows:
        return None
    inc = rows[0]
    inc["board"] = json.loads(inc["hypotheses"]) if inc["hypotheses"] else None
    inc["actions_list"] = json.loads(inc["actions"] or "[]")
    return inc


def hypothesis_rows(inc: dict) -> list[dict]:
    if not inc or not inc.get("board"):
        return []
    threshold = get_config()["doctor"]["confidence_threshold"]
    rows = []
    for h in inc["board"]["hypotheses"]:
        if h["confidence"] >= threshold:
            verdict = "root cause"
        elif h["confidence"] >= 0.3:
            verdict = "inconclusive"
        else:
            verdict = "rejected"
        rows.append({"Cause": h["cause"], "Confidence": h["confidence"], "Verdict": verdict,
                     "Evidence for": "\n".join(h["evidence_for"]) or "-",
                     "Evidence against": "\n".join(h["evidence_against"]) or "-",
                     "Fix": ", ".join(f"{f['action']}({f['target']})" for f in h["proposed_fix"]) or "-"})
    return rows


ACTIVE_WORDS = {"down": "down", "slow": "slow", "error_500": "returning errors", "corrupt": "returning wrong data",
                "flaky": "failing now and then", "unreachable": "not reachable"}


def active_faults() -> list[str]:
    # Which failures are switched on right now (shown in the sidebar only; the doctor never reads this).
    faults = []
    for name in get_config()["services"]:
        try:
            mode = httpx.get(f"{service_url(name)}/admin/chaos", timeout=2).json()["mode"]
        except (httpx.HTTPError, KeyError, ValueError):
            mode = "unreachable"
        if mode != "none":
            faults.append(f"{name}: {ACTIVE_WORDS.get(mode, mode)}")
    for r in db.query("SELECT key, value FROM control WHERE key LIKE 'chaos.%' OR key = 'llm.chaos'"):
        faults.append(f"{r['key']} = {r['value']}")
    for agent in registry.dependencies():
        info = registry.active_info(agent)
        if info["version"] is not None and info["version"] != registry.last_known_good(agent):
            faults.append(f"{agent}: running an untested instructions update (v{info['version']})")
    return faults



# ---------------------------------------------------------- live office view
SERVICE_NAMES = ("hr_db", "vendor_api", "knowledge_base", "llm")


def _doctor_target(message: str, candidates: list[str]) -> str | None:
    # The first agent or service named in the doctor's latest message is where it walks to.
    hits = [(message.find(name), name) for name in candidates if name in message]
    return min(hits)[1] if hits else None


def cooldown_s() -> float:
    return get_config()["doctor"]["escalation_cooldown_s"]


def _short_symptom(described: str) -> str:
    from dashboard.names import SYMPTOMS_SHORT, symptom_type
    if not described:
        return ""
    kind = symptom_type(described)
    return SYMPTOMS_SHORT.get(kind, kind.replace("_", " "))


def office_state() -> dict:
    # Everything the animated office needs, in one small JSON document.
    now = time.time()
    agents = registry.dependencies()
    cards = {c["agent"]: c for c in agent_cards()}
    started = {r["agent"]: r["n"] for r in db.query(
        "SELECT agent, COUNT(*) AS n FROM runs WHERE started_at >= ? GROUP BY agent", (now - 3,))}
    open_inc = db.query("SELECT id, symptoms FROM incidents WHERE status = 'open' ORDER BY id DESC LIMIT 1")
    events = db.query("SELECT incident_id, stage, message, ts FROM doctor_events ORDER BY id DESC LIMIT 1")
    last = events[0] if events else None
    doctor = {"target": None, "stage": "IDLE", "message": "All agents healthy. Making rounds.", "incident_id": None,
              "working": False}
    if open_inc:
        sick = sorted({s["agent"] for s in json.loads(open_inc[0]["symptoms"] or "[]")})
        doctor.update(working=True, incident_id=open_inc[0]["id"], target=sick[0] if sick else None)
    if last and (open_inc or now - last["ts"] < 8):
        names = list(agents) + list(SERVICE_NAMES)
        from dashboard.names import ticker
        doctor.update(stage=last["stage"], message=ticker(last["message"])[:200],
                      target=_doctor_target(last["message"], names) or doctor["target"])
        if last["stage"] in ("VERIFY", "REPORT", "RESTORE") and not open_inc:
            doctor["target"] = None
    if not open_inc and last and last["stage"] == "ESCALATE" and now - last["ts"] < cooldown_s():
        # Handed to a human: the doctor stays with the agent and waits instead of going home.
        inc = db.query("SELECT symptoms FROM incidents WHERE id = ?", (last["incident_id"],))
        sick = sorted({s["agent"] for s in json.loads(inc[0]["symptoms"] or "[]")}) if inc else []
        left = int(cooldown_s() - (now - last["ts"]))
        doctor.update(stage="ESCALATE", target=sick[0] if sick else None, working=False,
                      message=f"Handed to a person. Not reopening this problem for {left} s.")
    pending = db.query("SELECT action, target FROM approvals WHERE status = 'pending' ORDER BY id LIMIT 1")
    return {
        "now": now,
        "agents": [{"name": a, "status": cards[a]["status"], "health": cards[a]["health"],
                    "symptom": _short_symptom((cards[a]["symptoms"] or [""])[0]), "new_requests": started.get(a, 0),
                    "deps": agents[a]} for a in agents],
        "services": [{"name": s["name"], "status": s["status"], "breaker": s["breaker"], "fallback": s["fallback"],
                      "integrity": s["integrity"]} for s in services()],
        "doctor": doctor,
        "pending_approval": f"{pending[0]['action']}({pending[0]['target']})" if pending else None,
    }


# ---------------------------------------------------------- doctor steps and timeline
STEPS = [("DETECT", "Detect"), ("DIAGNOSE", "Diagnose"), ("TREAT", "Treat"), ("VERIFY", "Verify"), ("REPORT", "Report")]


def doctor_steps(incident_id: int) -> dict:
    # Which of the doctor's steps are done for an incident, when each started, and
    # whether it is waiting for a human right now.
    inc = db.query("SELECT status, first_symptom_at, opened_at, recovered_at FROM incidents WHERE id = ?",
                   (incident_id,))
    if not inc:
        return {}
    inc = inc[0]
    events = db.query("SELECT stage, message, ts FROM doctor_events WHERE incident_id = ? ORDER BY id", (incident_id,))
    first_ts = {}
    for e in events:
        first_ts.setdefault(e["stage"], e["ts"])
    waiting = bool(db.query("SELECT 1 FROM approvals WHERE incident_id = ? AND status = 'pending'", (incident_id,)))
    start = inc["first_symptom_at"] or inc["opened_at"]
    steps, reached = [], True
    for key, label in STEPS:
        ts = first_ts.get(key)
        if key == "VERIFY" and inc["recovered_at"]:
            done = True
        elif key == "VERIFY":
            done = False
        else:
            done = ts is not None and (key != "TREAT" or not waiting)
        current = not done and reached
        steps.append({"key": key, "label": label, "done": done, "current": current and inc["status"] == "open",
                      "at": round(ts - start, 1) if ts else None})
        reached = reached and done
    last = events[-1] if events else None
    return {"status": inc["status"], "steps": steps, "waiting_approval": waiting,
            "last_message": last["message"] if last else "", "last_stage": last["stage"] if last else "",
            "escalated": inc["status"] == "escalated"}


def timeline(incident_id: int) -> list[dict]:
    # Key moments of an incident in seconds since the first symptom.
    inc = db.query("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    if not inc:
        return []
    inc = inc[0]
    start = inc["first_symptom_at"] or inc["opened_at"]
    points = [{"label": "First symptom", "t": 0.0, "kind": "bad"},
              {"label": "Detected", "t": inc["opened_at"] - start, "kind": "info"}]
    acts = db.query("SELECT action, target, ts FROM actions_log WHERE incident_id = ? AND result LIKE '%\"done\"%' "
                    "AND action NOT LIKE 'undo:%' ORDER BY id", (incident_id,))
    if acts:
        points.append({"label": f"First fix: {acts[0]['action']}", "t": acts[0]["ts"] - start, "kind": "fix"})
    approved = db.query("SELECT decided_at FROM approvals WHERE incident_id = ? AND status = 'approved' ORDER BY id",
                        (incident_id,))
    if approved:
        points.append({"label": "Approved by a human", "t": approved[-1]["decided_at"] - start, "kind": "human"})
    if inc["recovered_at"]:
        points.append({"label": "Recovery verified", "t": inc["recovered_at"] - start, "kind": "good"})
    elif inc["status"] == "escalated" and inc["resolved_at"]:
        points.append({"label": "Escalated to a human", "t": inc["resolved_at"] - start, "kind": "human"})
    return [dict(p, t=round(max(0.0, p["t"]), 1)) for p in points]


# ---------------------------------------------------------- trends for the small live charts
def trends(minutes: int = 15, bucket_s: int = 30) -> dict:
    # Per-bucket numbers for the last few minutes: finished tasks, failed tasks, and the
    # average AI response time, for the whole fleet and for each agent.
    now = time.time()
    start = now - minutes * 60
    n = int(minutes * 60 / bucket_s)

    def idx(ts: float) -> int:
        return min(n - 1, max(0, int((ts - start) / bucket_s)))

    tasks, failed = [0] * n, [0] * n
    for r in db.query("SELECT status, ended_at FROM runs WHERE ended_at >= ? AND status != 'running'", (start,)):
        i = idx(r["ended_at"])
        tasks[i] += 1
        failed[i] += r["status"] != "ok"
    lat_sum, lat_n = [0.0] * n, [0] * n
    per_agent: dict[str, list[list[float]]] = {}
    for s in db.query("SELECT agent, latency_ms, ts FROM spans WHERE kind = 'llm' AND ts >= ?", (start,)):
        i = idx(s["ts"])
        lat_sum[i] += s["latency_ms"] or 0
        lat_n[i] += 1
        a = per_agent.setdefault(s["agent"], [[0.0] * n, [0] * n])
        a[0][i] += s["latency_ms"] or 0
        a[1][i] += 1

    def avg(total: list[float], count: list[int]) -> list[float | None]:
        return [round(t / c) if c else None for t, c in zip(total, count)]

    # Charts start where the data starts, so a fresh start does not squeeze everything to the right edge.
    first = next((i for i in range(n) if tasks[i] or lat_n[i]), n - 1)
    first = min(first, n - 2)
    return {"tasks": tasks[first:], "failed": failed[first:], "latency": avg(lat_sum, lat_n)[first:],
            "agent_latency": {a: avg(v[0], v[1])[first:] for a, v in per_agent.items()}, "bucket_s": bucket_s}


def health_rounds() -> dict:
    # When the doctor last tested the agents with its known-answer questions, how they did,
    # and when the next round is due.
    rows = db.query("SELECT agent, score, ts FROM health_checks WHERE details NOT LIKE '%\"source\": \"judge\"%' "
                    "AND id IN (SELECT MAX(id) FROM health_checks GROUP BY agent)")
    if not rows:
        return {"last": None, "next_in": None, "passed": 0, "total": 0}
    last = max(r["ts"] for r in rows)
    every = get_config()["health"]["interval_s"]
    return {"last": last, "next_in": max(0, int(min(r["ts"] for r in rows) + every - time.time())),
            "passed": sum(r["score"] >= 1.0 for r in rows), "total": len(rows)}
