# Baseline for the benchmark: what most teams have today.
# Classic monitoring (errors and slowness only, no answer-quality checks) and two remedies:
# restart the agent (stop its running work) and retry the work that failed.
# No diagnosis, no rollback, no backup data, no model switch, no index rebuild.
import threading
import time

from common.config import get_config
from doctor import actions
from doctor.detectors import detect_latency, detect_llm_errors, detect_run_failures, detect_tool_errors
from doctor.monitor import snapshot

CLASSIC_DETECTORS = [detect_tool_errors, detect_run_failures, detect_llm_errors, detect_latency]


class NaiveRemediator:
    name = "naive (retry + restart)"

    def __init__(self, cooldown_s: float = 20):
        self.cfg = get_config()["doctor"]
        self.cooldown_s = cooldown_s
        self.watch_from = time.time()
        self.last_action: dict[str, float] = {}

    def reset(self) -> None:
        self.watch_from = time.time()
        self.last_action = {}

    def failing_agents(self) -> list[str]:
        since = max(time.time() - self.cfg["window_s"], self.watch_from)
        snap = snapshot(since)
        return sorted({s.agent for d in CLASSIC_DETECTORS for s in d(snap, self.cfg)})

    def tick(self) -> None:
        now = time.time()
        for agent in self.failing_agents():
            if now - self.last_action.get(agent, 0) < self.cooldown_s:
                continue
            self.last_action[agent] = now
            # "Restart": stop whatever the agent is doing now. "Retry": run the failed work again.
            actions.execute("kill_run", agent, None, approved_by="naive baseline")
            actions.execute("replay_run", agent, None, approved_by="naive baseline")


def run_forever(stop: threading.Event, remediator: NaiveRemediator) -> None:
    while not stop.wait(remediator.cfg["poll_interval_s"]):
        try:
            remediator.tick()
        except Exception as e:  # the baseline must keep running like the doctor does
            print(f"[naive] tick failed: {type(e).__name__}: {e}", flush=True)
