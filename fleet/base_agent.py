# Minimal tool-calling agent loop. Every LLM call and tool call is traced.
#
# Step cap and token budget can be changed at runtime through the control table
# (keys "agent.<name>.max_steps" / "agent.<name>.token_budget"), which is how
# the doctor will stop runaway loops. "agent.<name>.kill_before" = a timestamp kills every
# run of that agent that started before it, at its next step.
import json
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from common import db, llm, registry, tracer
from common.config import get_config


class ToolError(Exception):
    # A tool failed; the error is shown to the model as the tool result.
    pass


@dataclass
class Tool:
    fn: Callable[..., dict]
    schema: dict  # OpenAI function-tool schema


def http_get(url: str, params: dict | None = None) -> dict:
    # GET a JSON endpoint, turning any failure into a ToolError.
    try:
        r = httpx.get(url, params=params, timeout=get_config()["agents"]["tool_timeout_s"])
    except httpx.HTTPError as e:
        raise ToolError(f"{type(e).__name__}: {e}") from e
    if r.status_code >= 400:
        raise ToolError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def worth_caching(result: dict) -> bool:
    # Only good data may become backup data: no empty or zero-score search results,
    # no "still pending" answers, no records with missing values.
    if not isinstance(result, dict) or result.get("status") == "pending":
        return False
    if "results" in result:
        hits = result["results"] or []
        return bool(hits) and any(h.get("snippet") and h.get("score") for h in hits)
    if "quotes" in result:
        return bool(result["quotes"]) and all(isinstance(q.get("unit_price"), (int, float)) for q in result["quotes"])
    if "leave_balance_days" in result:
        return result["leave_balance_days"] is not None
    return True


def call_dependency(dependency: str, key: str, fetch: Callable[[], dict]) -> dict:
    # Calls a shared dependency through its circuit breaker.
    # Breaker open + fallback on -> serve the last good response from the cache.
    # Breaker open + no fallback -> fail fast without calling the dependency.
    # Every successful call with good data refreshes the cache.
    if db.get_control(f"circuit.{dependency}") == "open":
        if db.get_control(f"fallback.{dependency}", False):
            rows = db.query("SELECT value, ts FROM cache WHERE dependency = ? AND key = ?",
                            (dependency, key))
            if rows:
                return {**json.loads(rows[0]["value"]), "source": "cache", "cached_at": rows[0]["ts"]}
            raise ToolError(f"{dependency} circuit open and no cached data for '{key}'")
        raise ToolError(f"{dependency} circuit open")
    result = fetch()
    if not worth_caching(result):
        return result
    db.execute(
        "INSERT INTO cache(dependency, key, value, ts) VALUES(?, ?, ?, ?) "
        "ON CONFLICT(dependency, key) DO UPDATE SET value = excluded.value, ts = excluded.ts",
        (dependency, key, json.dumps(result, ensure_ascii=False), time.time()))
    return result


class BaseAgent:
    name: str = "base"
    tools: dict[str, Tool] = {}

    def _limits(self) -> tuple[int, int | None]:
        cfg = get_config()["agents"]
        max_steps = db.get_control(f"agent.{self.name}.max_steps", cfg["default_max_steps"])
        budget = db.get_control(f"agent.{self.name}.token_budget", cfg["default_token_budget"])
        return max_steps, budget

    def run(self, task: str) -> dict:
        version, system_prompt = registry.get_active_prompt(self.name)
        max_steps, budget = self._limits()
        run_id = tracer.start_run(self.name, task)
        messages = [{"role": "system", "content": system_prompt},
                    {"role": "user", "content": task}]
        schemas = [t.schema for t in self.tools.values()]
        total_tokens, step = 0, 0
        started = time.time()

        def finish(status: str, answer: str | None = None) -> dict:
            tracer.end_run(run_id, status, total_tokens, step)
            return {"run_id": run_id, "agent": self.name, "status": status, "answer": answer}

        for step in range(1, max_steps + 1):
            if db.get_control(f"agent.{self.name}.kill_before", 0) > started:
                tracer.log_span(run_id, self.name, step, "error", error="run killed by the doctor",
                                prompt_version=version)
                return finish("killed")
            try:
                resp = llm.chat(messages, schemas, agent=self.name)
            except llm.LLMError as e:
                tracer.log_span(run_id, self.name, step, "error", error=str(e), prompt_version=version)
                return finish("failed")

            total_tokens += resp.tokens_in + resp.tokens_out
            tracer.log_span(run_id, self.name, step, "llm", input=messages[-1], output={
                "content": resp.content, "tool_calls": [c.__dict__ for c in resp.tool_calls]},
                latency_ms=resp.latency_ms, tokens_in=resp.tokens_in, tokens_out=resp.tokens_out,
                model=resp.model, prompt_version=version)

            if budget and total_tokens > budget:
                tracer.log_span(run_id, self.name, step, "error",
                                error=f"token budget {budget} exceeded", prompt_version=version)
                return finish("killed")

            if not resp.tool_calls:
                tracer.log_span(run_id, self.name, step, "final", output=resp.content,
                                model=resp.model, prompt_version=version)
                return finish("ok", resp.content)

            messages.append({"role": "assistant", "content": resp.content, "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                for c in resp.tool_calls]})
            for call in resp.tool_calls:
                result = self._run_tool(run_id, step, call, version)
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(result, ensure_ascii=False)})

        tracer.log_span(run_id, self.name, step, "error",
                        error=f"step cap {max_steps} reached", prompt_version=version)
        return finish("timeout")

    def _run_tool(self, run_id: str, step: int, call: llm.ToolCall, version: int) -> dict:
        start = time.perf_counter()
        tool = self.tools.get(call.name)
        error, result = None, None
        try:
            if tool is None:
                raise ToolError(f"unknown tool '{call.name}'")
            result = tool.fn(**call.arguments)
        except ToolError as e:
            error = str(e)
        except TypeError as e:  # bad arguments from the model
            error = f"bad arguments: {e}"
        latency = (time.perf_counter() - start) * 1000
        tracer.log_span(run_id, self.name, step, "tool", tool_name=call.name, input=call.arguments,
                        output=result, error=error, latency_ms=latency, prompt_version=version)
        return {"error": error} if error else result
