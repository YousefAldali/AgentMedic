# VERIFY: proves recovery instead of trusting it.
# Each round: rerun the golden health checks of every affected agent, and check that
# no new symptoms appeared in live traffic since the fix.
# Recovered = several consecutive passing rounds.
import time
from typing import Callable

from common.config import get_config
from doctor.detectors import detect
from doctor.monitor import run_health_check, snapshot


def golden_round(agent: str) -> tuple[bool, str]:
    r = run_health_check(agent, source="verify")
    ok = r["score"] >= get_config()["health"]["healthy_score"]
    detail = f"{agent} golden checks {r['passed']}/{r['total']}"
    if r["failed"]:
        detail += f" (failed: {'; '.join(r['failed'])})"
    return ok, detail


def verify(agents: list[str], since: float, on_round: Callable[[dict], None]) -> tuple[bool, list[dict]]:
    cfg = get_config()["doctor"]
    passes, history = 0, []
    for rnd in range(1, cfg["verify_max_rounds"] + 1):
        checks = {a: golden_round(a) for a in agents}
        live = [s for s in detect(snapshot(since)) if s.agent in agents]
        ok = all(c[0] for c in checks.values()) and not live
        passes = passes + 1 if ok else 0
        entry = {"round": rnd, "ok": ok, "passes": passes,
                 "probes": {a: d for a, (_, d) in checks.items()},
                 "live_symptoms": [s.describe() for s in live]}
        history.append(entry)
        on_round(entry)
        if passes >= cfg["verify_passes"]:
            return True, history
        time.sleep(cfg["verify_interval_s"])
    return False, history
