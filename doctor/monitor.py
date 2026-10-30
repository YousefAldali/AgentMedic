# MONITOR: reads everything the fleet recorded in a time window,
# and runs the golden health checks.
import json
import re
import threading
import time
from dataclasses import dataclass, field

from common import db
from common.config import ROOT, get_config


@dataclass
class Snapshot:
    since: float
    now: float
    tool_spans: list[dict]
    llm_spans: list[dict]
    error_spans: list[dict]
    runs: list[dict]
    health: list[dict] = field(default_factory=list)
    purchase_orders: list[dict] = field(default_factory=list)


def snapshot(since: float) -> Snapshot:
    return Snapshot(
        since=since,
        now=time.time(),
        tool_spans=db.query(
            "SELECT run_id, agent, tool_name, input, output, error, latency_ms, ts FROM spans "
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
        health=db.query("SELECT agent, score, ts FROM health_checks WHERE ts >= ? ORDER BY ts", (since,)),
        purchase_orders=db.query(
            "SELECT po_id, valid, ts FROM purchase_orders WHERE ts >= ? AND run_id != 'seed' ORDER BY ts",
            (since,)),
    )


# ---------------------------------------------------------- golden health checks
# A few questions per agent with known correct answers (fleet/golden/<agent>.json).
# They catch silent failures: nothing errors, but the answers are wrong.
# Score = fraction passed. They run on a schedule and after every fix.
GOLDEN_DIR = ROOT / "fleet" / "golden"


def golden_set(agent: str) -> list[dict]:
    path = GOLDEN_DIR / f"{agent}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _json_field(data, path: str):
    for key in path.split("."):
        data = data.get(key) if isinstance(data, dict) else None
    return data


def check_answer(answer: str | None, expect: dict) -> bool:
    text = answer or ""
    low = text.lower()
    if "contains" in expect and not all(s.lower() in low for s in expect["contains"]):
        return False
    if "any" in expect and not any(s.lower() in low for s in expect["any"]):
        return False
    if "regex" in expect and not re.search(expect["regex"], text, re.IGNORECASE):
        return False
    if "json" in expect:
        try:
            data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        except ValueError:
            return False
        for path, want in expect["json"].items():
            got = _json_field(data, path)
            if isinstance(want, (int, float)) and not isinstance(want, bool):
                try:
                    if abs(float(got) - want) > 0.01:
                        return False
                except (TypeError, ValueError):
                    return False
            elif str(got).strip().lower() != str(want).strip().lower():
                return False
    return True


def run_health_check(agent: str, source: str = "schedule", stop: threading.Event | None = None) -> dict | None:
    # Returns None (and saves nothing) if the system is shutting down mid-check,
    # so a half-finished check never shows up as a fake low score.
    from fleet.workload import build_fleet
    runner = build_fleet()[agent]
    results = []
    for case in golden_set(agent):
        if stop is not None and stop.is_set():
            return None
        run = runner.run(case["question"])
        passed = run["status"] == "ok" and check_answer(run["answer"], case["expect"])
        results.append({"question": case["question"], "passed": passed, "answer": (run["answer"] or "")[:200]})
    if stop is not None and stop.is_set():
        return None
    total = len(results)
    score = sum(r["passed"] for r in results) / total if total else 1.0
    db.execute("INSERT INTO health_checks(agent, score, details, ts) VALUES(?, ?, ?, ?)",
               (agent, score, json.dumps({"source": source, "results": results}, ensure_ascii=False), time.time()))
    return {"agent": agent, "score": score, "passed": sum(r["passed"] for r in results), "total": total,
            "failed": [r["question"] for r in results if not r["passed"]]}


def latest_health() -> dict[str, dict]:
    rows = db.query("SELECT agent, score, ts FROM health_checks WHERE id IN "
                    "(SELECT MAX(id) FROM health_checks GROUP BY agent)")
    return {r["agent"]: r for r in rows}


def run_health_forever(stop: threading.Event) -> None:
    from fleet.workload import build_fleet
    interval = get_config()["health"]["interval_s"]
    stop.wait(get_config()["health"]["first_run_after_s"])
    while not stop.is_set():
        for agent in build_fleet():
            if stop.is_set():
                break
            try:
                r = run_health_check(agent, stop=stop)
                if r is None:
                    break
                failed = f"  failed: {'; '.join(r['failed'])}" if r["failed"] else ""
                print(f"[health] {agent:<11} score {r['score']:.2f} ({r['passed']}/{r['total']}){failed}", flush=True)
            except Exception as e:  # a broken check must never stop the others
                print(f"[health] {agent:<11} check crashed: {type(e).__name__}: {e}", flush=True)
        stop.wait(interval)
