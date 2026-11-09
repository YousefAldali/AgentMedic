# DIAGNOSE: builds the hypothesis board.
# The doctor LLM returns strict JSON, validated with pydantic (one retry).
# If the LLM fails twice: a deterministic rule-based board.
# Either way the board keeps competing hypotheses with evidence for and against.
import json
import time

from pydantic import BaseModel, Field, ValidationError

from common import db, llm, registry
from common.config import get_config
from doctor.actions import REGISTRY
from doctor.correlator import Candidate, read_state


class Fix(BaseModel):
    action: str
    target: str


class Hypothesis(BaseModel):
    cause: str
    affected_agents: list[str]
    evidence_for: list[str] = []
    evidence_against: list[str] = []
    confidence: float = Field(ge=0, le=1)
    proposed_fix: list[Fix] = []


class Board(BaseModel):
    hypotheses: list[Hypothesis] = Field(min_length=1)
    source: str = "rules"
    tokens: int = 0


SERVICE_FAILING = {"down", "erroring", "unreachable", "degraded"}

SYSTEM_PROMPT = (
    "You are the diagnosis stage of AgentMedic, a reliability agent for a fleet of enterprise AI agents. "
    "You receive symptoms grouped by dependency (candidates), the true state of each service from its "
    "health endpoint, and per-agent context (LLM call stats, prompt versions). "
    "Build a hypothesis board: several competing root causes, each with evidence for and against and a "
    "confidence from 0 to 1. Rules: keep at least 2 hypotheses; only lower a hypothesis's confidence when "
    "you have evidence against it; several independent faults can be true at the same time; never trust an "
    "agent's own description of its success; proposed_fix may only use the allowed actions listed. "
    "Reply with ONLY a JSON object of the form "
    '{"hypotheses": [{"cause": str, "affected_agents": [str], "evidence_for": [str], '
    '"evidence_against": [str], "confidence": float, "proposed_fix": [{"action": str, "target": str}]}]}'
)


def agent_context(agents: list[str], since: float) -> dict:
    ctx = {}
    for a in agents:
        stats = db.query(
            "SELECT COUNT(*) AS calls, COALESCE(AVG(latency_ms), 0) AS avg_ms FROM spans "
            "WHERE kind = 'llm' AND agent = ? AND ts >= ?", (a, since))[0]
        errors = db.query(
            "SELECT COUNT(*) AS n FROM spans WHERE kind = 'error' AND agent = ? AND ts >= ?",
            (a, since))[0]["n"]
        prompt = registry.active_info(a)
        health = db.query("SELECT score, ts FROM health_checks WHERE agent = ? ORDER BY id DESC LIMIT 1", (a,))
        ctx[a] = {"llm": {"calls": stats["calls"], "errors": errors, "avg_ms": round(stats["avg_ms"])},
                  "prompt": {"active": prompt["version"], "last_known_good": registry.last_known_good(a),
                             "deployed_at": prompt["created_at"]},
                  "health_score": health[0]["score"] if health else None}
    return ctx


def _service_hypotheses(c: Candidate, evidence: list[str]) -> list[Hypothesis]:
    hyps = []
    status = c.state.get("status")
    integrity = c.state.get("data_integrity") or c.state.get("index_integrity")
    confirmed = status in SERVICE_FAILING or integrity == "failed"
    evidence.append(f"{c.dependency} health endpoint reports status={status}"
                    + (f", integrity={integrity}" if integrity else ""))
    against = [] if confirmed else [f"{c.dependency} health endpoint reports the service healthy"]
    if c.healthy_agents:
        against.append(f"other agents using {c.dependency} are healthy: {', '.join(c.healthy_agents)}")
    err = c.errors or {"total": 0, "4xx": 0, "5xx": 0, "other": 0}
    if err["5xx"]:
        evidence.append(f"{err['5xx']} of {err['total']} errors are 5xx server errors")
    if err["4xx"]:
        against.append(f"{err['4xx']} of {err['total']} errors are 4xx (requests the service rejected), "
                       "which an outage does not cause")
    # The health endpoint is the strongest evidence. Without it, errors alone can
    # never push an outage hypothesis to the action threshold.
    share_5xx = err["5xx"] / err["total"] if err["total"] else 0
    confidence = 0.9 if confirmed else round(min(0.55, 0.25 + 0.3 * share_5xx), 2)
    if status in SERVICE_FAILING:
        cause = f"{c.dependency} outage (status={status})"
        fix = [Fix(action="open_circuit_breaker", target=c.dependency),
               Fix(action="enable_fallback", target=c.dependency)]
    elif integrity == "failed":
        cause = f"{c.dependency} is serving corrupted data (integrity check failed)"
        fix = ([Fix(action="rebuild_index", target=c.dependency)] if c.dependency == "knowledge_base" else
               [Fix(action="open_circuit_breaker", target=c.dependency),
                Fix(action="enable_fallback", target=c.dependency)])
    else:
        cause, fix = f"{c.dependency} outage (status={status})", []
    hyps.append(Hypothesis(cause=cause, affected_agents=c.agents, evidence_for=evidence,
                           evidence_against=against, confidence=confidence, proposed_fix=fix))
    if err["4xx"]:
        share_4xx = err["4xx"] / err["total"]
        bad_against = []
        if err["5xx"]:
            bad_against.append(f"{err['5xx']} errors are 5xx server errors, which bad requests do not explain")
        if confirmed:
            bad_against.append(f"{c.dependency} health endpoint confirms a real failure (status={status})")
        hyps.append(Hypothesis(
            cause=f"{', '.join(c.agents)} tool sending bad requests to {c.dependency}",
            affected_agents=c.agents,
            evidence_for=[f"{err['4xx']} of {err['total']} errors are 4xx: the service rejected the requests"]
            + [e for s in c.symptoms for e in s.evidence if "HTTP 4" in e][:1],
            evidence_against=bad_against,
            confidence=round(min(0.55, 0.25 + 0.3 * share_4xx), 2),
            proposed_fix=[]))
    return hyps


LOOP_SYMPTOMS = {"repeated_call_loop", "token_spike"}


def rule_board(candidates: list[Candidate], ctx: dict) -> Board:
    hyps = []
    affected = sorted({a for c in candidates for a in c.agents})
    upstream = sorted({c.dependency for c in candidates if c.kind == "cascade"})
    first_symptom = min((s.first_seen for c in candidates for s in c.symptoms), default=time.time())
    for c in candidates:
        evidence = [s.describe() for s in c.symptoms] + [e for s in c.symptoms for e in s.evidence[:2]]
        types = {s.type for s in c.symptoms}
        if c.state is not None:
            hyps += _service_hypotheses(c, evidence)
        elif c.dependency == "llm":
            hyps.append(Hypothesis(
                cause="LLM endpoint degraded", affected_agents=c.agents, evidence_for=evidence,
                evidence_against=[f"agents on the same LLM are healthy: {', '.join(c.healthy_agents)}"]
                if c.healthy_agents else [],
                confidence=0.8 if c.kind == "shared" else 0.5,
                proposed_fix=[Fix(action="switch_model", target="llm")]))
        elif c.kind == "cascade":
            hyps.append(Hypothesis(
                cause=f"cascade: {c.dependency} is producing invalid output that {', '.join(c.agents)} consumes",
                affected_agents=sorted(set(c.agents) | {c.dependency}), evidence_for=evidence,
                evidence_against=[], confidence=0.8,
                proposed_fix=[Fix(action="replay_run", target=c.dependency)]
                + [Fix(action="replay_run", target=a) for a in c.agents]))
        else:
            agent = c.agents[0]
            if types & LOOP_SYMPTOMS:
                hyps.append(Hypothesis(
                    cause=f"runaway tool loop in {agent}", affected_agents=[agent],
                    evidence_for=[e for e in evidence if any(k in e for k in ("loop", "token", "called", "used"))],
                    confidence=0.85,
                    proposed_fix=[Fix(action="kill_run", target=agent), Fix(action="set_step_cap", target=agent)]))
            if types - LOOP_SYMPTOMS:
                hyps.append(Hypothesis(
                    cause=f"{agent} gives wrong answers or fails without a dependency error",
                    affected_agents=[agent],
                    evidence_for=[e for e in evidence if "loop" not in e and "token" not in e],
                    confidence=0.4))

    if not any(c.dependency == "llm" for c in candidates):
        against = [f"{a}: {ctx[a]['llm']['calls']} LLM calls, {ctx[a]['llm']['errors']} LLM errors, "
                   f"avg {ctx[a]['llm']['avg_ms']} ms - responding normally" for a in affected if a in ctx]
        healthy = [a for a in registry.dependencies() if a not in affected]
        if healthy:
            against.append(f"other agents on the same LLM are healthy: {', '.join(healthy)}")
        hyps.append(Hypothesis(cause="LLM endpoint degraded", affected_agents=affected,
                               evidence_against=against, confidence=0.05,
                               proposed_fix=[Fix(action="switch_model", target="llm")]))

    for a in sorted(set(affected) | set(upstream)):
        p = ctx[a]["prompt"]
        good = p["active"] == p["last_known_good"]
        evidence_for = []
        if not good:
            evidence_for.append(f"active prompt v{p['active']} is not the last known-good v{p['last_known_good']}")
            if p.get("deployed_at"):
                evidence_for.append(f"v{p['active']} went live {first_symptom - p['deployed_at']:.0f} s "
                                    "before the first symptom")
            if a in upstream:
                evidence_for.append(f"{a}'s output is what the downstream agents fail on")
        hyps.append(Hypothesis(
            cause=f"bad prompt deployed for {a}", affected_agents=[a],
            evidence_for=evidence_for,
            evidence_against=[f"active prompt v{p['active']} is the last known-good version"] if good else [],
            confidence=0.05 if good else 0.8,
            proposed_fix=[Fix(action="rollback_prompt", target=a)]))

    hyps.sort(key=lambda h: -h.confidence)
    return Board(hypotheses=hyps, source="rules")


def cause_ids(h: Hypothesis) -> set[str]:
    # Turns a hypothesis into the root-cause ids used for scoring, based on what its fix touches.
    ids = set()
    for f in h.proposed_fix:
        if f.action in ("open_circuit_breaker", "enable_fallback", "rebuild_index"):
            ids.add(f.target)
        elif f.action == "switch_model":
            ids.add("llm")
        elif f.action == "rollback_prompt":
            ids.add(f"prompt:{f.target}")
        elif f.action in ("kill_run", "set_step_cap"):
            ids.add(f"loop:{f.target}")
    return ids


def _extract_json(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    return text[start:end + 1]


def llm_board(candidates: list[Candidate], ctx: dict) -> Board | None:
    payload = {
        "candidates": [c.to_dict() for c in candidates],
        "agent_context": ctx,
        "allowed_actions": {a.name: {"tier": a.tier, "description": a.description} for a in REGISTRY.values()},
        "now": time.time(),
    }
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}]
    tokens = 0
    for _ in range(2):
        resp = llm.chat(messages, agent="doctor", role="doctor")
        tokens += resp.tokens_in + resp.tokens_out
        try:
            data = json.loads(_extract_json(resp.content or ""))
            data.pop("source", None)
            data.pop("tokens", None)
            board = Board(**data, source="llm", tokens=tokens)
            if len(board.hypotheses) < 2:
                raise ValueError("keep at least 2 hypotheses")
            board.hypotheses.sort(key=lambda h: -h.confidence)
            return board
        except (ValueError, ValidationError) as e:
            messages += [{"role": "assistant", "content": resp.content or ""},
                         {"role": "user", "content": f"That reply was invalid ({e}). "
                                                     "Reply with ONLY the JSON object."}]
    return None


GUARD_CAP = 0.55


def known_targets() -> set[str]:
    cfg = get_config()
    return set(cfg["services"]) | set(registry.dependencies()) | {"llm"}


def canonical_target(target: str) -> str:
    # Maps loose names from the LLM ("vendor_api.get_quotes", "Vendor API") to a real target.
    known = known_targets()
    if target in known:
        return target
    flat = target.lower().replace(" ", "_")
    for name in sorted(known, key=len, reverse=True):
        if name in flat:
            return name
    return target


def _state(target: str, by_dep: dict) -> dict:
    c = by_dep.get(target)
    return (c.state if c else None) or read_state(target) or {}


def evidence_for_fix(fix: Fix, candidates: list[Candidate], by_dep: dict) -> str | None:
    # Every fix needs matching evidence. Returns why a fix is NOT supported, or None if it is.
    a, t = fix.action, fix.target
    symptoms = [s for c in candidates for s in c.symptoms]
    if a in ("kill_run", "set_step_cap"):
        if not any(s.agent == t and s.type in LOOP_SYMPTOMS for s in symptoms):
            return f"no loop or token spike was detected for {t}"
    elif a == "rollback_prompt":
        info = registry.active_info(t)
        if info["version"] is None or info["version"] == registry.last_known_good(t):
            return f"{t}'s active prompt is already the last known-good version"
    elif a == "switch_model":
        if not any(s.dependency == "llm" for s in symptoms):
            return "no LLM latency or LLM errors were detected"
    elif a in ("open_circuit_breaker", "enable_fallback"):
        state = _state(t, by_dep)
        integrity = state.get("data_integrity") or state.get("index_integrity")
        if state.get("status") not in SERVICE_FAILING and integrity != "failed":
            return f"{t}'s health endpoint does not report a failure"
    elif a == "rebuild_index":
        if _state(t, by_dep).get("index_integrity") != "failed":
            return f"{t}'s index integrity check did not fail"
    elif a in ("close_circuit_breaker", "disable_fallback"):
        if db.get_control(f"circuit.{t}") != "open":
            return f"{t}'s circuit breaker is not open"
    return None


def apply_guards(board: Board, candidates: list[Candidate]) -> Board:
    # Safety rules in code, applied after every diagnosis (LLM or rules), because an LLM's
    # confidence numbers are not precise enough to decide on their own whether to act.
    # A hypothesis whose fix touches a service is capped below the action threshold when
    #   - that service's health endpoint says it is healthy, or
    #   - its errors are a mix of bad requests (4xx) and server failures (5xx).
    # The cap is keyed on the fix target, not on how the hypothesis is worded.
    by_dep = {c.dependency: c for c in candidates}
    services = get_config()["services"]
    cascades = {a for c in candidates if c.kind == "cascade" for a in c.agents + [c.dependency]}
    for h in board.hypotheses:
        for fix in h.proposed_fix:
            fix.target = canonical_target(fix.target)
        touched = sorted({f.target for f in h.proposed_fix if f.target in services})
        kept = []
        for fix in h.proposed_fix:
            why_not = evidence_for_fix(fix, candidates, by_dep)
            if why_not:
                h.evidence_against.append(f"safety rule: dropped {fix.action}({fix.target}): {why_not}")
            else:
                kept.append(fix)
        # A replay only makes sense next to a real fix, or to clean up after a cascade.
        has_real_fix = any(f.action != "replay_run" for f in kept)
        for fix in [f for f in kept if f.action == "replay_run"]:
            if not has_real_fix and fix.target not in cascades:
                kept.remove(fix)
                h.evidence_against.append(f"safety rule: dropped replay_run({fix.target}): nothing was fixed to replay after")
        h.proposed_fix = kept
        for target in touched:
            c = by_dep.get(target)
            state = (c.state if c else None) or read_state(target) or {}
            status = state.get("status")
            integrity = state.get("data_integrity") or state.get("index_integrity") or "ok"
            healthy = status == "up" and integrity == "ok"
            err = (c.errors if c else None) or {}
            mixed = err.get("4xx", 0) > 0 and err.get("5xx", 0) > 0
            if (healthy or mixed) and h.confidence > GUARD_CAP:
                why = (f"{target} health endpoint reports it healthy" if healthy else
                       f"{target} errors are mixed ({err['4xx']} bad requests, {err['5xx']} server errors)")
                h.evidence_against.append(f"safety rule: {why}, so confidence is capped at {GUARD_CAP:.0%}")
                h.confidence = GUARD_CAP
    board.hypotheses.sort(key=lambda h: -h.confidence)
    return board


def diagnose(candidates: list[Candidate], since: float) -> Board:
    agents = sorted({a for c in candidates for a in c.agents}
                    | {c.dependency for c in candidates if c.kind == "cascade"})
    ctx = agent_context(agents, since)
    try:
        board = llm_board(candidates, ctx)
    except llm.LLMError:
        board = None
    if board is None:
        board = rule_board(candidates, ctx)
        board.source = "rules (LLM fallback)"
        return apply_guards(board, candidates)
    return apply_guards(add_proof_floor(board, rule_board(candidates, ctx)), candidates)


def add_proof_floor(board: Board, rules: Board) -> Board:
    # When the rules have direct proof of a cause (health endpoint says down, the same call
    # repeated N times, a new prompt + failing golden checks, every agent slow), the LLM
    # can't talk it below the action threshold: the rule's hypothesis is added to the board.
    # The safety caps in apply_guards still apply afterwards.
    threshold = get_config()["doctor"]["confidence_threshold"]
    covered = {i for h in board.hypotheses if h.confidence >= threshold for i in cause_ids(h)}
    for h in rules.hypotheses:
        ids = cause_ids(h)
        if h.confidence >= threshold and ids and not ids <= covered:
            h.cause = f"{h.cause} (direct proof from the rules)"
            board.hypotheses.append(h)
            covered |= ids
    board.hypotheses.sort(key=lambda h: -h.confidence)
    return board


def root_causes(board: Board) -> list[Hypothesis]:
    threshold = get_config()["doctor"]["confidence_threshold"]
    return [h for h in board.hypotheses if h.confidence >= threshold]
