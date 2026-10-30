# DIAGNOSE: builds the hypothesis board.
# Real mode: the doctor LLM returns strict JSON, validated with pydantic (one retry).
# MOCK_LLM=1, or if the LLM fails twice: a deterministic rule-based board.
# Either way the board keeps competing hypotheses with evidence for and against.
import json
import time

from pydantic import BaseModel, Field, ValidationError

from common import db, llm, registry
from common.config import get_config, mock_llm
from doctor.actions import REGISTRY
from doctor.correlator import Candidate


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
        active, _ = registry.get_active_prompt(a)
        ctx[a] = {"llm": {"calls": stats["calls"], "errors": errors, "avg_ms": round(stats["avg_ms"])},
                  "prompt": {"active": active, "last_known_good": registry.last_known_good(a)}}
    return ctx


def rule_board(candidates: list[Candidate], ctx: dict) -> Board:
    hyps = []
    affected = sorted({a for c in candidates for a in c.agents})
    for c in candidates:
        evidence = [s.describe() for s in c.symptoms] + [e for s in c.symptoms for e in s.evidence[:2]]
        if c.state is not None:
            status = c.state.get("status")
            integrity = c.state.get("data_integrity") or c.state.get("index_integrity")
            confirmed = status in SERVICE_FAILING or integrity == "failed"
            evidence.append(f"{c.dependency} health endpoint reports status={status}")
            against = [] if confirmed else [f"{c.dependency} health endpoint reports the service healthy"]
            if c.healthy_agents:
                against.append(f"other agents using {c.dependency} are healthy: {', '.join(c.healthy_agents)}")
            fix = ([Fix(action="open_circuit_breaker", target=c.dependency),
                    Fix(action="enable_fallback", target=c.dependency)] if status in SERVICE_FAILING else [])
            hyps.append(Hypothesis(
                cause=f"{c.dependency} outage (status={status})", affected_agents=c.agents,
                evidence_for=evidence, evidence_against=against,
                confidence=0.9 if confirmed else 0.4, proposed_fix=fix))
        elif c.dependency == "llm":
            hyps.append(Hypothesis(
                cause="LLM endpoint degraded", affected_agents=c.agents, evidence_for=evidence,
                evidence_against=[f"agents on the same LLM are healthy: {', '.join(c.healthy_agents)}"]
                if c.healthy_agents else [],
                confidence=0.8 if c.kind == "shared" else 0.5,
                proposed_fix=[Fix(action="switch_model", target="llm")]))
        else:
            hyps.append(Hypothesis(
                cause=f"agent-level fault in {', '.join(c.agents)}", affected_agents=c.agents,
                evidence_for=evidence, confidence=0.4))

    if not any(c.dependency == "llm" for c in candidates):
        against = [f"{a}: {ctx[a]['llm']['calls']} LLM calls, {ctx[a]['llm']['errors']} LLM errors, "
                   f"avg {ctx[a]['llm']['avg_ms']} ms - responding normally" for a in affected]
        healthy = [a for a in registry.dependencies() if a not in affected]
        if healthy:
            against.append(f"other agents on the same LLM are healthy: {', '.join(healthy)}")
        hyps.append(Hypothesis(cause="LLM endpoint degraded", affected_agents=affected,
                               evidence_against=against, confidence=0.05,
                               proposed_fix=[Fix(action="switch_model", target="llm")]))

    for a in affected:
        p = ctx[a]["prompt"]
        good = p["active"] == p["last_known_good"]
        hyps.append(Hypothesis(
            cause=f"bad prompt deployed for {a}", affected_agents=[a],
            evidence_for=[] if good else [f"active prompt v{p['active']} is not the last known-good "
                                          f"v{p['last_known_good']}"],
            evidence_against=[f"active prompt v{p['active']} is the last known-good version"] if good else [],
            confidence=0.05 if good else 0.6,
            proposed_fix=[Fix(action="rollback_prompt", target=a)]))

    hyps.sort(key=lambda h: -h.confidence)
    return Board(hypotheses=hyps, source="rules")


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


def diagnose(candidates: list[Candidate], since: float) -> Board:
    agents = sorted({a for c in candidates for a in c.agents})
    ctx = agent_context(agents, since)
    if mock_llm():
        return rule_board(candidates, ctx)
    try:
        board = llm_board(candidates, ctx)
    except llm.LLMError:
        board = None
    if board is None:
        board = rule_board(candidates, ctx)
        board.source = "rules (LLM fallback)"
    return board


def root_causes(board: Board) -> list[Hypothesis]:
    threshold = get_config()["doctor"]["confidence_threshold"]
    return [h for h in board.hypotheses if h.confidence >= threshold]
