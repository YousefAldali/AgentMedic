# DETECT: cheap, deterministic rules that turn a Snapshot into Symptoms.
# No LLM here, so detection behaves the same every time (important on stage).
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

from common import registry
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
        if len(spans) < cfg["min_llm_calls"]:
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


def detect_loops(snap: Snapshot, cfg: dict) -> list[Symptom]:
    # The same tool called with the same arguments again and again inside one run.
    out, seen = [], set()
    counts = Counter((s["run_id"], s["agent"], s["tool_name"], s["input"]) for s in snap.tool_spans)
    for (run_id, agent, tool, args), n in counts.items():
        if n >= cfg["loop_repeat"] and agent not in seen:
            seen.add(agent)
            first = min(s["ts"] for s in snap.tool_spans if s["run_id"] == run_id)
            out.append(Symptom("repeated_call_loop", agent, None, n, cfg["loop_repeat"], first,
                               [f"run {run_id} called {tool}{args} {n} times"]))
    return out


def detect_token_spike(snap: Snapshot, cfg: dict) -> list[Symptom]:
    per_run = defaultdict(int)
    agent_of, first = {}, {}
    for s in snap.llm_spans:
        per_run[s["run_id"]] += (s["tokens_in"] or 0) + (s["tokens_out"] or 0)
        agent_of[s["run_id"]] = s["agent"]
        first.setdefault(s["run_id"], s["ts"])
    out, seen = [], set()
    for run_id, tokens in sorted(per_run.items(), key=lambda x: -x[1]):
        agent = agent_of[run_id]
        if tokens >= cfg["token_spike_per_run"] and agent not in seen:
            seen.add(agent)
            out.append(Symptom("token_spike", agent, None, tokens, cfg["token_spike_per_run"], first[run_id],
                               [f"run {run_id} used {tokens} tokens"]))
    return out


def _empty_result(output: str | None) -> bool | None:
    # True = search returned nothing useful, None = can't tell (not a search result).
    try:
        data = json.loads(output or "")
    except ValueError:
        return None
    if not isinstance(data, dict) or "results" not in data:
        return None
    hits = data["results"] or []
    return not hits or all(not h.get("snippet") or not h.get("score") for h in hits)


def detect_empty_results(snap: Snapshot, cfg: dict) -> list[Symptom]:
    # Search tools that answer, but with nothing useful: the index behind them is broken.
    out = []
    groups = defaultdict(list)
    for s in snap.tool_spans:
        if s["error"]:
            continue
        empty = _empty_result(s["output"])
        if empty is not None:
            groups[(s["agent"], s["tool_name"])].append((empty, s["ts"]))
    for (agent, tool), items in groups.items():
        empties = [ts for e, ts in items if e]
        rate = len(empties) / len(items)
        if len(items) >= cfg["min_tool_calls"] and rate >= cfg["empty_results_rate"]:
            out.append(Symptom("empty_results_rate", agent, tool_dependency(tool), round(rate, 2),
                               cfg["empty_results_rate"], empties[0],
                               [f"{len(empties)}/{len(items)} {tool} calls returned nothing useful"]))
    return out


def detect_health_drop(snap: Snapshot, cfg: dict) -> list[Symptom]:
    # Golden checks failing: the agent answers, nothing errors, but the answers are wrong.
    latest = {}
    for h in snap.health:
        latest[h["agent"]] = h
    return [Symptom("health_score_drop", agent, None, round(h["score"], 2), cfg["health_drop_below"], h["ts"],
                    [f"golden checks score {h['score']:.2f}"])
            for agent, h in latest.items() if h["score"] < cfg["health_drop_below"]]


def detect_upstream_bad_output(snap: Snapshot, cfg: dict) -> list[Symptom]:
    # An upstream agent producing invalid output that downstream agents consume
    # (today: procurement's purchase orders, read by finance).
    pos = snap.purchase_orders
    if len(pos) < cfg["min_outputs"]:
        return []
    bad = [p for p in pos if not p["valid"]]
    rate = len(bad) / len(pos)
    if rate < cfg["bad_output_rate"]:
        return []
    downstream = [a for a, ups in registry.upstream().items() if "procurement" in ups]
    return [Symptom("upstream_bad_output", agent, "procurement", round(rate, 2), cfg["bad_output_rate"],
                    bad[0]["ts"], [f"{len(bad)}/{len(pos)} recent purchase orders from procurement are invalid",
                                   f"e.g. {bad[0]['po_id']}"])
            for agent in downstream]


DETECTORS = [detect_tool_errors, detect_latency, detect_llm_errors, detect_run_failures, detect_loops,
             detect_token_spike, detect_empty_results, detect_health_drop, detect_upstream_bad_output]


def detect(snap: Snapshot) -> list[Symptom]:
    cfg = get_config()["doctor"]
    return [s for d in DETECTORS for s in d(snap, cfg)]
