# VERIFY: proves recovery instead of trusting it.
# Each round: run a probe task on every affected agent and check its trace and answer,
# and check that no new symptoms appeared in live traffic since the fix.
# Recovered = several consecutive passing rounds.
# (Phase 3 replaces these probes with the golden health checks.)
import json
import time
from typing import Callable

from common import db
from common.config import get_config
from doctor.detectors import detect
from doctor.monitor import snapshot


def _valid_po(answer: str | None) -> bool:
    try:
        text = answer or ""
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        return data.get("status") == "ok" and float(data["po"]["total"]) > 0
    except (ValueError, KeyError, TypeError):
        return False


def _probe_spec(agent: str):
    if agent == "procurement":
        rows = db.query("SELECT key FROM cache WHERE dependency = 'vendor_api' ORDER BY key LIMIT 1")
        item = rows[0]["key"] if rows else "printers"
        return f"Compare quotes for 5 {item}.", _valid_po
    if agent == "hr":
        return "How many leave days does E002 have left?", lambda a: "21" in (a or "")
    return None


def probe(agent: str) -> tuple[bool, str]:
    from fleet.workload import build_fleet
    spec = _probe_spec(agent)
    if spec is None:
        return True, "no probe defined"
    task, check = spec
    result = build_fleet()[agent].run(task)
    errors = db.query("SELECT error FROM spans WHERE run_id = ? AND error IS NOT NULL", (result["run_id"],))
    answer_ok = check(result["answer"])
    ok = result["status"] == "ok" and not errors and answer_ok
    return ok, (f"'{task}' -> run {result['status']}, {len(errors)} span errors, "
                f"answer check {'pass' if answer_ok else 'fail'}")


def verify(agents: list[str], since: float, on_round: Callable[[dict], None]) -> tuple[bool, list[dict]]:
    cfg = get_config()["doctor"]
    passes, history = 0, []
    for rnd in range(1, cfg["verify_max_rounds"] + 1):
        probes = {a: probe(a) for a in agents}
        live = [s for s in detect(snapshot(since)) if s.agent in agents]
        ok = all(p[0] for p in probes.values()) and not live
        passes = passes + 1 if ok else 0
        entry = {"round": rnd, "ok": ok, "passes": passes,
                 "probes": {a: d for a, (_, d) in probes.items()},
                 "live_symptoms": [s.describe() for s in live]}
        history.append(entry)
        on_round(entry)
        if passes >= cfg["verify_passes"]:
            return True, history
        time.sleep(cfg["verify_interval_s"])
    return False, history
