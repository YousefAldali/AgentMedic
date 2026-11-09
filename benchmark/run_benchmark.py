# Benchmark: naive retry/restart baseline vs AgentMedic on every scenario.
#
#   python -m benchmark.run_benchmark                    # all scenarios, both remediators (calls the AI model)
#   python -m benchmark.run_benchmark S7 S8              # only some scenarios
#   python -m benchmark.run_benchmark --timeout 150      # seconds each remediator gets per scenario
#   python -m benchmark.run_benchmark --only agentmedic  # rerun one side, keep the other side's last results
#
# A neutral judge decides recovery for both remediators the same way: every agent passes all
# its golden checks and no symptom of any kind is visible, twice in a row. Results go to
# benchmark/results/ as a markdown table, a CSV, a JSON file and a chart.
import argparse
import csv
import json
import os
import threading
import time
from dataclasses import asdict, dataclass

from benchmark.naive_agent import NaiveRemediator
from chaos.faults import reset_all
from chaos.scenarios import SCENARIOS, _identified, _incidents_since, inject, truth
from common import db, registry
from common.config import ROOT, get_config
from doctor.detectors import detect
from doctor.monitor import run_health_check, snapshot

RESULTS = ROOT / "benchmark" / "results"
CLEANUP = {"close_circuit_breaker", "disable_fallback"}


@dataclass
class Row:
    scenario: str
    faults: str
    remediator: str
    recovered: bool
    recover_s: float | None
    escalated: bool
    expected: str
    found: str
    correct_causes: int
    expected_causes: int
    actions: int
    wrong_actions: int
    doctor_tokens: int
    fault_seen: bool


# ---------------------------------------------------------------- the neutral judge
def judge_healthy(agents: list[str]) -> bool:
    for a in agents:
        if run_health_check(a, source="judge")["score"] < 1.0:
            return False
    return not detect(snapshot(time.time() - 15))


def action_cause(action: str, target: str) -> str | None:
    if action in ("open_circuit_breaker", "enable_fallback", "rebuild_index"):
        return target
    if action == "switch_model":
        return "llm"
    if action == "rollback_prompt":
        return f"prompt:{target}"
    if action in ("kill_run", "set_step_cap"):
        return f"loop:{target}"
    return None


def score_actions(rows: list[dict], expected: set[str], naive: bool) -> tuple[int, int]:
    done = [r for r in rows if '"done"' in (r["result"] or "") and not r["action"].startswith("undo:")
            and r["action"] not in CLEANUP]
    if naive:
        return len(done), len(done)
    wrong = sum(1 for r in done if (c := action_cause(r["action"], r["target"])) is not None and c not in expected)
    return len(done), wrong


# ---------------------------------------------------------------- one scenario, one remediator
class Switch:
    # The remediator thread calls whichever remediator is currently plugged in.
    def __init__(self):
        self.current = None

    def run(self, stop: threading.Event) -> None:
        while not stop.wait(get_config()["doctor"]["poll_interval_s"]):
            try:
                if self.current is not None:
                    self.current.tick()
            except Exception as e:  # keep the benchmark going
                print(f"[bench] remediator tick failed: {type(e).__name__}: {e}", flush=True)


def run_one(sid: str, remediator, label: str, timeout_s: float, settle_s: float) -> Row:
    expected, escalate = truth(sid)
    agents = list(registry.dependencies())
    reset_all()
    remediator.reset()
    time.sleep(settle_s)
    remediator.reset()
    t0 = time.time()
    inject(sid)
    remediator.reset()
    naive = isinstance(remediator, NaiveRemediator)
    seen_broken, recovered_at, good_rounds = False, None, 0
    limit = t0 + (min(timeout_s, 90) if escalate else timeout_s)
    while time.time() < limit:
        if escalate:
            if not naive and any(i["status"] == "escalated" for i in _incidents_since(t0)):
                break
            time.sleep(3)
            continue
        ok = judge_healthy(agents)
        if not ok:
            seen_broken, good_rounds = True, 0
        elif seen_broken:
            good_rounds += 1
            if good_rounds == 1:
                first_ok = time.time()
            if good_rounds >= 2:
                recovered_at = first_ok
                break
        time.sleep(2)
    incs = [] if naive else _incidents_since(t0)
    acts = db.query("SELECT action, target, result FROM actions_log WHERE ts >= ? AND "
                    + ("approved_by = 'naive baseline'" if naive else "incident_id IS NOT NULL"), (t0,))
    n_actions, wrong = score_actions(acts, expected, naive)
    found = set() if naive else _identified(incs)
    tokens = 0
    for i in incs:
        if i["hypotheses"]:
            tokens += json.loads(i["hypotheses"]).get("tokens", 0)
    escalated = any(i["status"] == "escalated" for i in incs)
    row = Row(sid, " + ".join(SCENARIOS[sid]), label, recovered_at is not None,
              round(recovered_at - t0, 1) if recovered_at else None, escalated,
              "escalate" if escalate else ", ".join(sorted(expected)),
              ", ".join(sorted(found)) or ("escalated" if escalated else "-"),
              len(found & expected), len(expected), n_actions, wrong, tokens, seen_broken or escalate)
    print(f"[bench] {sid:<4} {label:<26} recovered={row.recovered} t={row.recover_s} found={row.found} "
          f"actions={n_actions} wrong={wrong}", flush=True)
    return row


# ---------------------------------------------------------------- the whole benchmark
def run(ids: list[str], timeout_s: float, settle_s: float, only: str | None = None) -> list[Row]:
    os.environ["AUTO_APPROVE"] = "1"
    from doctor.doctor import Doctor
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
    switch = Switch()
    threads = [threading.Thread(target=run_workload, args=(stop,), daemon=True),
               threading.Thread(target=run_health_forever, args=(stop,), daemon=True),
               threading.Thread(target=switch.run, args=(stop,), daemon=True)]
    for t in threads:
        t.start()
    rows = []
    try:
        sides = (("AgentMedic", Doctor()), ("Naive retry + restart", NaiveRemediator()))
        for label, remediator in sides:
            if only and not label.lower().startswith(only):
                continue
            switch.current = remediator
            for sid in ids:
                rows.append(run_one(sid, remediator, label, timeout_s, settle_s))
            switch.current = None
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=20)
        reset_all()
        stop_services(procs)
    return rows


def summary(rows: list[Row]) -> dict:
    out = {}
    for label in dict.fromkeys(r.remediator for r in rows):
        mine = [r for r in rows if r.remediator == label]
        fixable = [r for r in mine if r.expected != "escalate"]
        recovered = [r for r in fixable if r.recovered]
        esc = [r for r in mine if r.expected == "escalate"]
        out[label] = {
            "recovery_rate": round(len(recovered) / len(fixable), 2) if fixable else None,
            "mean_recover_s": round(sum(r.recover_s for r in recovered) / len(recovered), 1) if recovered else None,
            "root_cause_accuracy": (round(sum(r.correct_causes for r in fixable) / sum(r.expected_causes for r in fixable), 2)
                                    if fixable else None),
            "wrong_actions": sum(r.wrong_actions for r in mine),
            "correct_escalations": f"{sum(r.escalated and r.actions == 0 for r in esc)}/{len(esc)}",
            "doctor_tokens": sum(r.doctor_tokens for r in mine),
        }
    return out


def keep_other_side(rows: list[Row], only: str) -> tuple[list[Row], str]:
    # Reuse the other remediator's rows from the last full run (its code did not change).
    previous = json.loads((RESULTS / "benchmark.json").read_text(encoding="utf-8"))
    kept = [Row(**r) for r in previous["rows"] if not r["remediator"].lower().startswith(only)]
    note = (f"{', '.join(sorted({r.remediator for r in kept}))} results are from the previous full run "
            f"({previous.get('finished_at', 'an earlier run')}); only {only} was rerun.")
    ordered = [r for r in rows + kept if r.remediator == "AgentMedic"] + [r for r in rows + kept if r.remediator != "AgentMedic"]
    return ordered, note


def write_outputs(rows: list[Row], note: str = "") -> dict:
    RESULTS.mkdir(parents=True, exist_ok=True)
    with open(RESULTS / "benchmark.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(rows[0])))
        w.writeheader()
        w.writerows(asdict(r) for r in rows)
    s = summary(rows)
    (RESULTS / "benchmark.json").write_text(json.dumps({"mode": "real", "summary": s,
                                                        "finished_at": time.strftime("%Y-%m-%d %H:%M"), "note": note,
                                                        "rows": [asdict(r) for r in rows]}, indent=2), encoding="utf-8")
    lines = ["# Benchmark: AgentMedic vs naive retry + restart", "",
             f"Mode: real models · scenarios: {len(set(r.scenario for r in rows))}"
             + (f"  \n{note}" if note else ""), "",
             "| Remediator | Recovery rate | Mean time to recover | Root causes found | Wrong / unnecessary actions | "
             "Correct escalations (F10) |", "|---|---|---|---|---|---|"]
    for label, m in s.items():
        rate = f"{m['recovery_rate']:.0%}" if m["recovery_rate"] is not None else "-"
        mean = f"{m['mean_recover_s']} s" if m["mean_recover_s"] is not None else "-"
        acc = f"{m['root_cause_accuracy']:.0%}" if m["root_cause_accuracy"] is not None else "-"
        lines.append(f"| {label} | {rate} | {mean} | {acc} | {m['wrong_actions']} | {m['correct_escalations']} |")
    lines += ["", "| Scenario | Faults | Remediator | Recovered | Time (s) | Expected | Found | Actions | Wrong |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r.scenario} | {r.faults} | {r.remediator} | {'yes' if r.recovered else 'escalated' if r.escalated else 'no'} | "
                     f"{r.recover_s or '-'} | {r.expected} | {r.found} | {r.actions} | {r.wrong_actions} |")
    (RESULTS / "benchmark.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description="AgentMedic benchmark")
    ap.add_argument("scenarios", nargs="*")
    ap.add_argument("--timeout", type=float, default=150)
    ap.add_argument("--settle", type=float, default=10)
    ap.add_argument("--only", choices=["agentmedic", "naive"])
    args = ap.parse_args()
    rows = run(args.scenarios or list(SCENARIOS), args.timeout, args.settle, args.only)
    note = ""
    if args.only:
        rows, note = keep_other_side(rows, args.only)
    write_outputs(rows, note)
    from benchmark.chart import draw
    draw(rows)


if __name__ == "__main__":
    main()
