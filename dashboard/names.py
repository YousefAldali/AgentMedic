# Plain names for everything the dashboard shows. The doctor and the logs use short
# technical ids (vendor_api, repeated_call_loop, prompt:hr); people see these words instead.
import re

AGENTS = {"hr": "HR", "procurement": "Purchasing", "finance": "Finance", "support": "Support"}
SYSTEMS = {"hr_db": "HR database", "vendor_api": "Supplier system", "knowledge_base": "Knowledge base",
           "llm": "AI model"}
SYMPTOMS = {
    "tool_error_rate": "a system it uses keeps failing",
    "tool_error_streak": "failing again and again",
    "latency_high": "answers are too slow",
    "llm_error_rate": "the AI model returns errors",
    "run_failure_rate": "tasks are failing",
    "repeated_call_loop": "stuck repeating the same step",
    "token_spike": "using far too much AI",
    "empty_results_rate": "searches find nothing useful",
    "health_score_drop": "giving wrong answers",
    "upstream_bad_output": "getting bad data from Purchasing",
}
# Short versions that fit on the office drawing.
SYMPTOMS_SHORT = {
    "tool_error_rate": "system failing", "tool_error_streak": "failing repeatedly", "latency_high": "too slow",
    "llm_error_rate": "AI model errors", "run_failure_rate": "tasks failing", "repeated_call_loop": "stuck in a loop",
    "token_spike": "using too much AI", "empty_results_rate": "search finds nothing",
    "health_score_drop": "wrong answers", "upstream_bad_output": "bad data from Purchasing",
}
CAUSES = {
    "prompt:hr": "a bad update to the HR agent's instructions",
    "prompt:procurement": "a bad update to the Purchasing agent's instructions",
    "loop:procurement": "the Purchasing agent stuck in a loop",
    "vendor_api": "the supplier system", "knowledge_base": "the knowledge base", "llm": "the AI model",
    "hr_db": "the HR database",
}
ACTIONS = {
    "open_circuit_breaker": "Pause calls to the failing system",
    "close_circuit_breaker": "Resume calls to the system",
    "enable_fallback": "Use saved backup data", "disable_fallback": "Stop using backup data",
    "switch_model": "Switch to the backup AI model", "kill_run": "Stop the stuck tasks",
    "set_step_cap": "Limit how long each task can run", "replay_run": "Redo the tasks that failed",
    "rollback_prompt": "Undo the latest instructions update", "rebuild_index": "Rebuild the search index",
}

ACTIONS_SHORT = {
    "open_circuit_breaker": "pause calls to", "close_circuit_breaker": "resume calls to",
    "enable_fallback": "use saved data for", "disable_fallback": "stop saved data for",
    "switch_model": "switch to the backup model", "kill_run": "stop stuck tasks of",
    "set_step_cap": "limit task length of", "replay_run": "redo failed tasks of",
    "rollback_prompt": "undo instructions update of", "rebuild_index": "rebuild the search index of",
}

TOOLS = {"get_quotes": "price lookup", "get_leave_balance": "leave lookup",
         "search_knowledge_base": "knowledge search", "get_purchase_order": "order lookup"}

# Longest ids first, so "vendor_api" is replaced before "api" could ever match.
_WORDS = {**SYMPTOMS, **ACTIONS_SHORT, **TOOLS, "LLM endpoint": "AI model", "LLM calls": "AI calls",
          "LLM errors": "AI errors", "LLM latency": "AI response time", "same LLM": "same AI model", "LLM": "AI model", **{f"prompt:{a}": f"{n} instructions" for a, n in AGENTS.items()},
          **{f"loop:{a}": f"{n} loop" for a, n in AGENTS.items()}, **SYSTEMS, **AGENTS,
          "bad prompt": "bad instructions update", "prompt": "instructions", "circuit breaker": "pause switch",
          "golden checks": "test questions", "golden check": "test question"}
_PATTERN = re.compile("|".join(rf"\b{re.escape(k)}\b" for k in sorted(_WORDS, key=len, reverse=True)))


def plain(text: str) -> str:
    # Swap technical ids inside a sentence for plain words.
    out = _PATTERN.sub(lambda m: _WORDS[m.group(0)], text or "")
    return re.sub(r"\b(to|for|of|model)\(", r"\1 (", out)


def cause(cause_id: str) -> str:
    return CAUSES.get(cause_id) or plain(cause_id)


def symptom_type(described: str) -> str:
    # "procurement: repeated_call_loop (5, threshold 5)" -> "repeated_call_loop"
    return described.split(": ", 1)[-1].split(" (")[0].split(" on ")[0]


def fix(text: str) -> str:
    # "open_circuit_breaker(vendor_api)" -> "Pause calls to the failing system (Supplier system)"
    name, _, target = text.partition("(")
    target = target.rstrip(")")
    who = AGENTS.get(target) or SYSTEMS.get(target) or target
    return f"{ACTIONS.get(name, name)} ({who})" if target else ACTIONS.get(name, name)


def causes_sentence(causes: list[str]) -> str:
    # Root-cause labels from the doctor as one readable phrase.
    parts = []
    for c in causes:
        text = cause(c) if c in CAUSES else plain(c)
        text = re.sub(r"\s*\([^)]*\)", "", text).strip()
        text = text.replace(" deployed for ", " for ")
        text = text[:1].lower() + text[1:] if text[:2] != text[:2].upper() else text
        parts.append(text if text.startswith(("a ", "an ", "the ")) else "the " + text)
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def report(markdown: str) -> str:
    # The English report with plain names; code marks are dropped where they now hold words.
    lines = []
    for line in plain(markdown).replace("golden checks", "test questions").split("\n"):
        line = re.sub(r"`([^`]*\s[^`]*)`", r"\1", line)
        lines.append(line.replace("`", "") if line.count("`") % 2 else line)
    return "\n".join(lines)
