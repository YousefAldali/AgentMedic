# Named scenarios: single faults and several faults at once, with their ground truth.
#
#   python -m chaos.scenarios list
#   python -m chaos.scenarios inject S7        # while run.py is running (demo)
#   python -m chaos.scenarios reset S7
#   python -m chaos.scenarios check            # run every scenario unattended and score the doctor
#   python -m chaos.scenarios check S7 S9      # only some
#   python -m chaos.scenarios check --timeout 300   # give the doctor longer per scenario (real LLM)
#
# "check" starts its own services, fleet, doctor and health checks, approves approval-tier
# actions automatically, and for each scenario: injects, waits for the doctor, compares the
# root causes it acted on with the ground truth, then resets.
import json
import os
import sys
import threading
import time
from dataclasses import dataclass

from chaos.faults import FAULTS, reset_all
from common import db, registry
from common.config import get_config

SCENARIOS = {
    "F1": ["F1"], "F2": ["F2"], "F3": ["F3"], "F4": ["F4"], "F5": ["F5"], "F6": ["F6"], "F10": ["F10"],
    "S7": ["F1", "F2"],
    "S8": ["F4", "F3"],
    "S9": ["F6", "F5"],
    "S10": ["F1", "F2", "F3"],
}


def truth(sid: str) -> tuple[set[str], bool]:
    faults = [FAULTS[f] for f in SCENARIOS[sid]]
    if any(f.expect_escalation for f in faults):
        return set(), True
    return {c for f in faults for c in f.root_causes}, False


def inject(sid: str) -> None:
    for f in SCENARIOS[sid]:
        FAULTS[f].inject()


def reset(sid: str) -> None:
    for f in SCENARIOS[sid]:
        FAULTS[f].reset()


@dataclass
class Result:
    scenario: str
    expected: set[str]
    expect_escalation: bool
    identified: set[str]
    incidents: list[str]
    actions: list[str]
    detect_s: float | None
    recover_s: float | None

    @property
    def passed(self) -> bool:
        if self.expect_escalation:
            return "escalated" in self.incidents and not self.actions
        return bool(self.incidents) and all(s == "resolved" for s in self.incidents) and self.identified == self.expected


def _incidents_since(t0: float) -> list[dict]:
    return db.query("SELECT * FROM incidents WHERE opened_at >= ? ORDER BY id", (t0,))


def _identified(incidents: list[dict]) -> set[str]:
    from doctor.diagnoser import Board, cause_ids, root_causes
    ids = set()
    for inc in incidents:
        if inc["hypotheses"]:
            board = Board(**{k: v for k, v in json.loads(inc["hypotheses"]).items()})
            done = {(a["action"], a["target"]) for a in json.loads(inc["actions"] or "[]") if a["status"] == "done"}
            for h in root_causes(board):
                if any((f.action, f.target) in done for f in h.proposed_fix):
                    ids |= cause_ids(h)
    return ids


def wait_for_doctor(sid: str, t0: float, timeout_s: float) -> list[dict]:
    expected, escalate = truth(sid)
    deadline = t0 + timeout_s
    while time.time() < deadline:
        time.sleep(2)
        incs = _incidents_since(t0)
        if not incs or any(i["status"] == "open" for i in incs):
            continue
        if escalate and any(i["status"] == "escalated" for i in incs):
            return incs
        if expected <= _identified(incs):
            return incs
    return _incidents_since(t0)


def run_checks(ids: list[str], timeout_s: float = 180, settle_s: float = 12) -> list[Result]:
    os.environ["AUTO_APPROVE"] = "1"
    from doctor.doctor import Doctor, run_forever as run_doctor
    from doctor.monitor import run_health_forever
    from fleet.finance_agent import seed_golden_po
    from fleet.workload import run_forever as run_workload
    from run import start_services, stop_services

    db.init_db(reset=True)
    registry.seed_prompts()
    seed_golden_po()
    procs = start_services()
    reset_all()
    stop = threading.Event()
    doc = Doctor()
    threads = [threading.Thread(target=run_workload, args=(stop,), daemon=True),
               threading.Thread(target=run_doctor, args=(stop, doc), daemon=True),
               threading.Thread(target=run_health_forever, args=(stop,), daemon=True)]
    for t in threads:
        t.start()
    results = []
    try:
        for sid in ids:
            reset_all()
            doc.reset()
            time.sleep(settle_s)
            doc.reset()
            t0 = time.time()
            inject(sid)
            print(f"\n===== {sid}: injected {', '.join(SCENARIOS[sid])} =====", flush=True)
            incs = wait_for_doctor(sid, t0, timeout_s)
            expected, escalate = truth(sid)
            actions = [f"{a['action']}({a['target']})" for i in incs
                       for a in json.loads(i["actions"] or "[]") if a["status"] == "done"]
            resolved = [i["recovered_at"] for i in incs if i["recovered_at"]]
            r = Result(sid, expected, escalate, _identified(incs), [i["status"] for i in incs], actions,
                       round(incs[0]["opened_at"] - t0, 1) if incs else None,
                       round(max(resolved) - t0, 1) if resolved and len(resolved) == len(incs) else None)
            results.append(r)
            print(f"===== {sid}: {'PASS' if r.passed else 'FAIL'} =====", flush=True)
            reset(sid)
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=20)
        stop_services(procs)
    return results


def print_table(results: list[Result]) -> None:
    print("\n| Scenario | Faults | Expected root causes | Doctor found | Incidents | Detect (s) | Recover (s) | Result |")
    print("|---|---|---|---|---|---|---|---|")
    for r in results:
        expected = "escalate to a human" if r.expect_escalation else ", ".join(sorted(r.expected))
        found = ", ".join(sorted(r.identified)) or ("none (escalated)" if "escalated" in r.incidents else "none")
        print(f"| {r.scenario} | {' + '.join(SCENARIOS[r.scenario])} | {expected} | {found} | "
              f"{', '.join(r.incidents) or '-'} | {r.detect_s or '-'} | {r.recover_s or '-'} | "
              f"{'PASS' if r.passed else 'FAIL'} |")
    print(f"\n{sum(r.passed for r in results)}/{len(results)} scenarios passed")


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        for sid, faults in SCENARIOS.items():
            expected, escalate = truth(sid)
            print(f"{sid:<4} {' + '.join(faults):<12} expect: {'escalation' if escalate else ', '.join(sorted(expected))}")
    elif cmd == "inject":
        inject(argv[1])
        print(f"injected {argv[1]}: {' + '.join(SCENARIOS[argv[1]])}")
    elif cmd == "reset":
        reset(argv[1])
        print(f"reset {argv[1]}")
    elif cmd == "check":
        args, timeout = argv[1:], 180.0
        if "--timeout" in args:
            i = args.index("--timeout")
            timeout = float(args[i + 1])
            args = args[:i] + args[i + 2:]
        print_table(run_checks(args or list(SCENARIOS), timeout_s=timeout))
    else:
        sys.exit(f"unknown command '{cmd}'")


if __name__ == "__main__":
    main(sys.argv[1:])
