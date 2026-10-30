# DETECT: cheap, deterministic rules that turn a Snapshot into Symptoms.
# No LLM here, so detection behaves the same every time (important on stage).
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from common.config import get_config
from doctor.monitor import Snapshot


@dataclass
class Symptom:
    type: str
    agent: str
    dependency: str | None
    value: float
    threshold: float
    first_seen: float
    evidence: list[str] = field(default_factory=list)

    def describe(self) -> str:
        where = f" on {self.dependency}" if self.dependency else ""
        return f"{self.agent}: {self.type}{where} ({self.value:g}, threshold {self.threshold:g})"

    def to_dict(self) -> dict:
        return asdict(self)


def tool_dependency(tool: str) -> str | None:
    return (get_config().get("tools") or {}).get(tool)


def detect_tool_errors(snap: Snapshot, cfg: dict) -> list[Symptom]:
    out = []
    groups = defaultdict(list)
    for s in snap.tool_spans:
        groups[(s["agent"], s["tool_name"])].append(s)
    for (agent, tool), spans in groups.items():
        errors = [s for s in spans if s["error"]]
        if not errors:
            continue
        dep = tool_dependency(tool)
        samples = sorted({e["error"][:120] for e in errors})[:2]
        rate = len(errors) / len(spans)
        if len(spans) >= cfg["min_tool_calls"] and rate >= cfg["tool_error_rate"]:
            out.append(Symptom("tool_error_rate", agent, dep, round(rate, 2), cfg["tool_error_rate"],
                               errors[0]["ts"], [f"{len(errors)}/{len(spans)} {tool} calls failed"] + samples))
        streak = 0
        for s in reversed(spans):
            if not s["error"]:
                break
            streak += 1
        if streak >= cfg["tool_error_streak"]:
            out.append(Symptom("tool_error_streak", agent, dep, streak, cfg["tool_error_streak"],
                               errors[-streak]["ts"], [f"last {streak} {tool} calls failed in a row"]))
    return out


def detect_latency(snap: Snapshot, cfg: dict) -> list[Symptom]:
    out = []
    by_agent = defaultdict(list)
    for s in snap.llm_spans:
        by_agent[s["agent"]].append(s)
    for agent, spans in by_agent.items():
        if len(spans) < cfg["min_tool_calls"]:
            continue
        lat = sorted(s["latency_ms"] for s in spans)
        p95 = lat[int(0.95 * (len(lat) - 1))]
        if p95 >= cfg["latency_high_ms"]:
            slow = [s for s in spans if s["latency_ms"] >= cfg["latency_high_ms"]]
            out.append(Symptom("latency_high", agent, "llm", round(p95), cfg["latency_high_ms"],
                               slow[0]["ts"], [f"p95 LLM latency {p95:.0f} ms over {len(spans)} calls"]))
    return out


def detect_llm_errors(snap: Snapshot, cfg: dict) -> list[Symptom]:
    out = []
    by_agent = defaultdict(list)
    for s in snap.error_spans:
        if s["error"].startswith(("step cap", "token budget")):
            continue
        by_agent[s["agent"]].append(s)
    for agent, errs in by_agent.items():
        if len(errs) >= cfg["llm_error_count"]:
            out.append(Symptom("llm_error_rate", agent, "llm", len(errs), cfg["llm_error_count"],
                               errs[0]["ts"], sorted({e["error"][:120] for e in errs})[:2]))
    return out


def detect_run_failures(snap: Snapshot, cfg: dict) -> list[Symptom]:
    out = []
    by_agent = defaultdict(list)
    for r in snap.runs:
        by_agent[r["agent"]].append(r)
    for agent, runs in by_agent.items():
        if len(runs) < cfg["min_runs"]:
            continue
        failed = [r for r in runs if r["status"] != "ok"]
        rate = len(failed) / len(runs)
        if rate >= cfg["run_failure_rate"]:
            out.append(Symptom("run_failure_rate", agent, None, round(rate, 2), cfg["run_failure_rate"],
                               min(r["started_at"] for r in failed),
                               [f"{len(failed)}/{len(runs)} runs ended {sorted({r['status'] for r in failed})}"]))
    return out


DETECTORS = [detect_tool_errors, detect_latency, detect_llm_errors, detect_run_failures]


def detect(snap: Snapshot) -> list[Symptom]:
    cfg = get_config()["doctor"]
    return [s for d in DETECTORS for s in d(snap, cfg)]
