# The doctor's state machine:
# MONITOR -> DETECT -> CORRELATE -> DIAGNOSE -> TREAT -> VERIFY -> REPORT
# If verification fails it diagnoses again with the new evidence (max_diagnose_loops),
# then escalates to a human. Every stage writes to doctor_events for the dashboard.
#
#   python -m doctor.doctor     # run the doctor on its own (services + fleet must be running)
import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, wait

import httpx

from common import db, registry
from common.config import env, get_config, service_url
from doctor import actions
from doctor.correlator import correlate, read_state
from doctor.detectors import Symptom, detect
from doctor.diagnoser import diagnose, root_causes
from doctor.monitor import run_health_check, snapshot
from doctor.reporter import facts, write_reports
from doctor.verifier import verify


def event(incident_id: int | None, stage: str, message: str) -> None:
    db.execute("INSERT INTO doctor_events(incident_id, stage, message, ts) VALUES(?, ?, ?, ?)",
               (incident_id, stage, message, time.time()))
    print(f"[doctor] {stage:<9} {message}", flush=True)


class Doctor:
    def __init__(self):
        self.cfg = get_config()["doctor"]
        self.watch_from = time.time()
        self.suppressed: dict[tuple, float] = {}

    def reset(self) -> None:
        # Forget past incidents (used between benchmark scenarios).
        self.watch_from = time.time()
        self.suppressed = {}

    def detect_now(self, since: float) -> list[Symptom]:
        now = time.time()
        return [s for s in detect(snapshot(since)) if self.suppressed.get((s.agent, s.dependency), 0) < now]

    def check_everyone(self, incident_id: int) -> None:
        # Something is wrong somewhere: health-check every agent now instead of waiting for the
        # schedule, so independent problems (e.g. a bad prompt) end up on the same board.
        from fleet.workload import build_fleet
        agents = list(build_fleet())
        pool = ThreadPoolExecutor(max_workers=len(agents))
        futures = {pool.submit(run_health_check, a, "incident"): a for a in agents}
        done, _ = wait(futures, timeout=self.cfg["on_demand_checks_s"])
        pool.shutdown(wait=False)
        scores = []
        for fut, agent in futures.items():
            if fut in done and fut.exception() is None and fut.result():
                scores.append(f"{agent} {fut.result()['score']:.2f}")
            else:
                scores.append(f"{agent} (still running)")
        event(incident_id, "MONITOR", "health-checked every agent: " + ", ".join(scores))

    def tick(self) -> None:
        self.restore_dependencies()
        since = max(time.time() - self.cfg["window_s"], self.watch_from)
        symptoms = self.detect_now(since)
        if symptoms:
            self.handle(symptoms, since)
            self.watch_from = time.time()

    def handle(self, symptoms: list[Symptom], since: float) -> None:
        first = min(s.first_seen for s in symptoms)
        incident_id = db.execute(
            "INSERT INTO incidents(status, symptoms, opened_at, first_symptom_at) VALUES('open', ?, ?, ?)",
            (json.dumps([s.to_dict() for s in symptoms]), time.time(), first))
        event(incident_id, "DETECT", f"{len(symptoms)} symptom(s): " + "; ".join(s.describe() for s in symptoms))
        self.check_everyone(incident_id)
        known = {(s.type, s.agent, s.dependency) for s in symptoms}
        extra = [s for s in self.detect_now(since) if (s.type, s.agent, s.dependency) not in known]
        if extra:
            symptoms += extra
            event(incident_id, "DETECT", f"{len(extra)} more symptom(s): " + "; ".join(s.describe() for s in extra))
            db.execute("UPDATE incidents SET symptoms = ? WHERE id = ?",
                       (json.dumps([s.to_dict() for s in symptoms]), incident_id))

        done_actions, verification, board, recovered = [], [], None, False
        escalation_reason = ""
        for loop in range(1, self.cfg["max_diagnose_loops"] + 1):
            candidates = correlate(symptoms, since)
            for c in candidates:
                state = f", health endpoint: {c.state.get('status')}" if c.state else ""
                healthy = f", healthy users: {', '.join(c.healthy_agents)}" if c.healthy_agents else ""
                event(incident_id, "CORRELATE",
                      f"{c.dependency} -> {c.kind} candidate, agents {', '.join(c.agents)}{state}{healthy}")

            board = diagnose(candidates, since)
            causes = root_causes(board)
            for h in board.hypotheses:
                verdict = "ROOT CAUSE" if h in causes else "inconclusive" if h.confidence >= 0.3 else "rejected"
                why = h.evidence_against[0] if h.evidence_against and h not in causes else (h.evidence_for or [""])[0]
                event(incident_id, "DIAGNOSE", f"[{h.confidence:.0%}] {h.cause} - {verdict}. {why}")
            db.execute("UPDATE incidents SET hypotheses = ?, root_causes = ? WHERE id = ?",
                       (board.model_dump_json(), json.dumps([h.cause for h in causes]), incident_id))
            if not causes:
                top = board.hypotheses[0]
                escalation_reason = (f"Evidence is ambiguous: the best hypothesis ('{top.cause}') is only "
                                     f"{top.confidence:.0%} confident and needs {self.cfg['confidence_threshold']:.0%}. "
                                     "No safe action fits, so AgentMedic escalates instead of guessing.")
                event(incident_id, "DIAGNOSE", "no hypothesis is confident enough to act on")
                break

            fixes = list(dict.fromkeys((f.action, f.target) for h in causes for f in h.proposed_fix))
            if not any(actions.tier_of(a) in (actions.AUTO, actions.APPROVAL) for a, _ in fixes):
                escalation_reason = (f"The root cause ('{causes[0].cause}', {causes[0].confidence:.0%}) has no "
                                     "safe automatic fix, so AgentMedic escalates instead of guessing.")
                event(incident_id, "DIAGNOSE", "no allowed action fits the root cause")
                break
            for name, target in order_fixes(fixes):
                if any(a["action"] == name and a["target"] == target and a["status"] == "done" for a in done_actions):
                    continue
                approved_by = None
                if actions.tier_of(name) == actions.APPROVAL and env("AUTO_APPROVE", "0") != "1":
                    why = next(h.cause for h in causes if (name, target) in {(f.action, f.target) for f in h.proposed_fix})
                    approval_id = actions.request_approval(name, target, incident_id, why)
                    event(incident_id, "TREAT", f"{name}({target}) [approval] -> waiting for a human to approve "
                          f"(#{approval_id}; dashboard, or: python -m doctor.approvals approve {approval_id})")
                    decision = actions.wait_for_decision(approval_id, self.cfg["approval_timeout_s"])
                    if decision["status"] != "approved":
                        status = "rejected" if decision["status"] == "rejected" else "not approved in time"
                        done_actions.append({"action": name, "target": target, "tier": actions.APPROVAL,
                                             "status": status, "ts": time.time()})
                        event(incident_id, "TREAT", f"{name}({target}) [approval] -> {status}")
                        continue
                    approved_by = decision["decided_by"]
                result = actions.execute(name, target, incident_id, approved_by=approved_by)
                result["ts"] = time.time()
                done_actions.append(result)
                event(incident_id, "TREAT", f"{name}({target}) [{result['tier']}] -> {result['status']}"
                      + (f": {result['detail']}" if result.get("detail") else ""))
            db.execute("UPDATE incidents SET actions = ? WHERE id = ?",
                       (json.dumps(done_actions, default=str), incident_id))

            fix_time = time.time()
            agents = sorted({a for h in causes for a in h.affected_agents})
            recovered, rounds = verify(agents, fix_time, lambda r: event(
                incident_id, "VERIFY", f"round {r['round']}: {'pass' if r['ok'] else 'fail'} "
                f"({r['passes']}/{self.cfg['verify_passes']}) " + "; ".join(r["probes"].values())
                + (f" | live: {'; '.join(r['live_symptoms'])}" if r["live_symptoms"] else "")))
            verification += rounds
            if recovered:
                break
            new = detect(snapshot(fix_time))
            symptoms = new or symptoms
            since = fix_time
            event(incident_id, "DIAGNOSE", f"not recovered after loop {loop}, diagnosing again with new evidence")

        status = "resolved" if recovered else "escalated"
        now = time.time()
        db.execute("UPDATE incidents SET status = ?, resolved_at = ?, recovered_at = ? WHERE id = ?",
                   (status, now, now if recovered else None, incident_id))
        if recovered:
            event(incident_id, "VERIFY", f"recovered in {now - first:.1f} s from first symptom")
        else:
            escalation_reason = escalation_reason or (
                f"Recovery could not be verified after {self.cfg['max_diagnose_loops']} diagnosis loops.")
            cooldown = self.cfg["escalation_cooldown_s"]
            for s in symptoms:
                self.suppressed[(s.agent, s.dependency)] = now + cooldown
            event(incident_id, "ESCALATE", f"{escalation_reason} Handing over to a human; "
                  f"the same problem won't reopen an incident for {cooldown} s.")
        self.report(incident_id, board, done_actions, verification, escalation_reason)

    def report(self, incident_id: int, board, done_actions: list[dict], verification: list[dict],
               escalation_reason: str = "") -> None:
        incident = db.query("SELECT * FROM incidents WHERE id = ?", (incident_id,))[0]
        board_dict = board.model_dump() if board else {"hypotheses": [], "source": "-"}
        board_dict["threshold"] = self.cfg["confidence_threshold"]
        follow_up = []
        for dep in get_config()["services"]:
            if db.get_control(f"circuit.{dep}") == "open":
                state = read_state(dep) or {}
                cached = " and agents are served cached data" if db.get_control(f"fallback.{dep}") else ""
                follow_up.append(f"{dep} is still {state.get('status', 'unknown')}; its circuit breaker stays "
                                 f"open{cached}. The doctor closes it automatically once {dep} is healthy.")
        if db.get_control("llm.route_to_backup"):
            follow_up.append("Fleet LLM calls still go to the backup model. Switch back once the primary "
                             "model's latency is normal again.")
        for agent in registry.dependencies():
            if db.get_control(f"agent.{agent}.max_steps") is not None:
                follow_up.append(f"{agent} runs are capped at {db.get_control(f'agent.{agent}.max_steps')} steps "
                                 "to contain the loop. A human should fix what makes the tool loop, then lift the cap.")
        en, ar = write_reports(facts(incident, board_dict, done_actions, verification, follow_up,
                                     escalation_reason))
        db.execute("UPDATE incidents SET report_md = ?, report_md_ar = ? WHERE id = ?", (en, ar, incident_id))
        event(incident_id, "REPORT", f"incident #{incident_id} report written (EN + AR)")

    def restore_dependencies(self) -> None:
        # Half-open check: once a dependency with an open breaker reports healthy again,
        # close the breaker and stop serving cached data.
        for dep in get_config()["services"]:
            if db.get_control(f"circuit.{dep}") != "open":
                continue
            state = read_state(dep) or {}
            integrity = state.get("data_integrity") or state.get("index_integrity") or "ok"
            if state.get("status") != "up" or integrity != "ok":
                continue
            if not self.trial_requests(dep):
                continue
            rows = db.query("SELECT incident_id FROM actions_log WHERE target = ? AND action = 'open_circuit_breaker' "
                            "ORDER BY id DESC LIMIT 1", (dep,))
            incident_id = rows[0]["incident_id"] if rows else None
            actions.execute("close_circuit_breaker", dep, incident_id)
            if db.get_control(f"fallback.{dep}"):
                actions.execute("disable_fallback", dep, incident_id)
            event(incident_id, "RESTORE", f"{dep} passed {self.cfg['half_open_requests']} real test requests: "
                                          "circuit breaker closed, live data restored")


    def trial_requests(self, dep: str) -> bool:
        # Half-open check: the health endpoint can be wrong, so send a few real requests
        # and only trust the dependency again if every one of them succeeds.
        url = service_url(dep) + get_config()["services"][dep]["probe"]
        for _ in range(self.cfg["half_open_requests"]):
            try:
                if httpx.get(url, timeout=3).status_code >= 400:
                    return False
            except httpx.HTTPError:
                return False
        return True


def order_fixes(fixes: list[tuple[str, str]]) -> list[tuple[str, str]]:
    # Automatic fixes first (contain the damage now), then the ones that need a human,
    # and replays last, once their cause is fixed.
    def rank(fix):
        if fix[0] == "replay_run":
            return 2
        return 1 if actions.tier_of(fix[0]) == actions.APPROVAL else 0
    return sorted(fixes, key=rank)


def run_forever(stop: threading.Event, doc: "Doctor | None" = None) -> None:
    doc = doc or Doctor()
    interval = doc.cfg["poll_interval_s"]
    print(f"[doctor] watching the fleet every {interval} s", flush=True)
    while not stop.wait(interval):
        try:
            doc.tick()
        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    stop_event = threading.Event()
    try:
        run_forever(stop_event)
    except KeyboardInterrupt:
        stop_event.set()
