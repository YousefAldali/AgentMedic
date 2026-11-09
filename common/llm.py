# LLM gateway. Every agent and the doctor call `chat()`.
#
# - Any provider: LLM_PROVIDER picks a built-in address (openai, gemini, groq, mistral, openrouter,
#   deepseek, together, xai, ollama, lmstudio, azure), or LLM_BASE_URL points at any other
#   OpenAI-compatible server. Models: FLEET_MODEL, DOCTOR_MODEL, BACKUP_MODEL.
# - Chaos hook: control key "llm.chaos" = {"mode": "none|slow|rate_limit", "latency_ms": N}
#   degrades the PRIMARY model only.
# - Fix hook: control key "llm.route_to_backup" = true sends fleet calls to BACKUP_MODEL.
#
# Messages and tools use the OpenAI chat format.
import json
import time
from dataclasses import dataclass, field

from common import db
from common.config import env, get_config


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


# Built-in addresses, so LLM_PROVIDER=groq is enough. Any other OpenAI-compatible server works
# with LLM_BASE_URL. Local servers need no key.
PROVIDERS = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "together": "https://api.together.xyz/v1",
    "xai": "https://api.x.ai/v1",
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
}
LOCAL = ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal")


def endpoint() -> dict:
    # Which server and key to use, from LLM_PROVIDER / LLM_BASE_URL / LLM_API_KEY.
    provider = (env("LLM_PROVIDER") or "").strip().lower()
    if provider and provider not in PROVIDERS and provider not in ("azure", "custom"):
        raise LLMError(f"unknown LLM_PROVIDER '{provider}'. Use one of: {', '.join(sorted(PROVIDERS))}, azure, "
                       "or leave it empty and set LLM_BASE_URL")
    base_url = env("LLM_BASE_URL") or PROVIDERS.get(provider)
    local = bool(base_url) and any(h in base_url for h in LOCAL)
    return {"provider": provider or ("custom" if base_url else "openai"), "base_url": base_url,
            "api_key": env("LLM_API_KEY") or ("local" if local else None), "local": local}


def key_needed() -> bool:
    return not endpoint()["local"]


_client = None


def _openai_client():
    global _client
    if _client is None:
        from openai import AzureOpenAI, OpenAI
        ep = endpoint()
        if not ep["api_key"]:
            raise LLMError("LLM_API_KEY is not set: add it to .env (local servers like Ollama don't need one)")
        timeout = get_config()["llm"]["timeout_s"]
        if ep["provider"] == "azure":
            if not ep["base_url"]:
                raise LLMError("LLM_PROVIDER=azure needs LLM_BASE_URL set to your Azure OpenAI endpoint")
            _client = AzureOpenAI(azure_endpoint=ep["base_url"], api_key=ep["api_key"],
                                  api_version=env("LLM_API_VERSION", "2024-10-21"), timeout=timeout, max_retries=0)
        else:
            _client = OpenAI(base_url=ep["base_url"], api_key=ep["api_key"], timeout=timeout, max_retries=0)
    return _client


def pick_model(role: str) -> tuple[str, bool]:
    # Return (model name, is_primary) for a caller role: 'fleet' or 'doctor'.
    if role == "doctor":
        name, primary = env("DOCTOR_MODEL") or env("FLEET_MODEL"), False
    elif db.get_control("llm.route_to_backup", False):
        name, primary = env("BACKUP_MODEL") or env("FLEET_MODEL"), False
    else:
        name, primary = env("FLEET_MODEL"), True
    if not name:
        raise LLMError("FLEET_MODEL is not set: add the model names to .env")
    return name, primary


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
    resp = _complete(model, messages, tools, agent)
    resp.latency_ms = (time.perf_counter() - start) * 1000
    return resp


def _complete(model: str, messages: list[dict], tools: list[dict] | None, agent: str) -> LLMResponse:
    # One call to the model. The tests replace this with scripted replies.
    try:
        kwargs = {"model": model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        resp = _openai_client().chat.completions.create(**kwargs)
    except LLMError:
        raise
    except Exception as e:  # network, auth, rate limit, ... all surface the same way
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
        model,
    )
