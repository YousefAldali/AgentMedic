# LLM gateway. Every agent and the doctor call `chat()`.
#
# - Real mode: any OpenAI-compatible endpoint, configured by env vars
#   (LLM_BASE_URL, LLM_API_KEY, FLEET_MODEL, DOCTOR_MODEL, BACKUP_MODEL).
# - MOCK_LLM=1: deterministic scripted replies from common/mock_llm.py.
# - Chaos hook: control key "llm.chaos" = {"mode": "none|slow|rate_limit", "latency_ms": N}
#   degrades the PRIMARY model only.
# - Fix hook: control key "llm.route_to_backup" = true sends fleet calls to BACKUP_MODEL.
#
# Messages and tools use the OpenAI chat format.
import json
import time
from dataclasses import dataclass, field

from common import db
from common.config import env, get_config, mock_llm


class LLMError(Exception):
    # The LLM call failed (rate limit, timeout, bad response...).
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    latency_ms: float = 0.0


_client = None


def _openai_client():
    global _client
    if _client is None:
        from openai import OpenAI
        base_url, api_key = env("LLM_BASE_URL"), env("LLM_API_KEY")
        if not api_key:
            raise LLMError("LLM_API_KEY is not set (or set MOCK_LLM=1)")
        _client = OpenAI(base_url=base_url, api_key=api_key,
                         timeout=get_config()["llm"]["timeout_s"], max_retries=0)
    return _client


def pick_model(role: str) -> tuple[str, bool]:
    # Return (model name, is_primary) for a caller role: 'fleet' or 'doctor'.
    if role == "doctor":
        return env("DOCTOR_MODEL", "mock-doctor"), False
    if db.get_control("llm.route_to_backup", False):
        return env("BACKUP_MODEL", "mock-backup"), False
    return env("FLEET_MODEL", "mock-primary"), True


def _apply_chaos(is_primary: bool) -> None:
    if not is_primary:
        return
    chaos = db.get_control("llm.chaos", {"mode": "none"})
    mode = chaos.get("mode", "none")
    if mode == "slow":
        time.sleep(chaos.get("latency_ms", 5000) / 1000)
    elif mode == "rate_limit":
        raise LLMError("429 Too Many Requests: primary model is rate limited")


def chat(messages: list[dict], tools: list[dict] | None = None, *,
         agent: str, role: str = "fleet") -> LLMResponse:
    model, is_primary = pick_model(role)
    start = time.perf_counter()
    _apply_chaos(is_primary)

    if mock_llm():
        from common.mock_llm import mock_reply
        time.sleep(get_config()["llm"]["mock_latency_ms"] / 1000)
        content, calls = mock_reply(agent, messages, tools or [])
        tokens_in = len(json.dumps(messages, ensure_ascii=False)) // 4
        tokens_out = len(json.dumps([content, [c.__dict__ for c in calls]], ensure_ascii=False)) // 4
        return LLMResponse(content, calls, tokens_in, tokens_out, model,
                           (time.perf_counter() - start) * 1000)

    try:
        kwargs = {"model": model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        resp = _openai_client().chat.completions.create(**kwargs)
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"{type(e).__name__}: {e}") from e

    msg = resp.choices[0].message
    calls = []
    for tc in msg.tool_calls or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {"_raw": tc.function.arguments}
        calls.append(ToolCall(tc.id, tc.function.name, args))
    usage = resp.usage
    return LLMResponse(
        msg.content, calls,
        getattr(usage, "prompt_tokens", 0) or 0,
        getattr(usage, "completion_tokens", 0) or 0,
        model, (time.perf_counter() - start) * 1000,
    )
