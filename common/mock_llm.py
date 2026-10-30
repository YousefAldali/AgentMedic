# Scripted, deterministic LLM replies for MOCK_LLM=1.
#
# Each agent has a small "planner" that looks at the task and the tool results
# so far, and returns either tool calls or a final answer, the same way a real
# model would. Replies are in Arabic when the task is in Arabic.
import json
import re
import uuid

from common.llm import ToolCall

ARABIC = re.compile(r"[؀-ۿ]")
MAX_TOOL_RETRIES = 3  # how often a mock agent retries a failing tool before giving up

# Arabic and English words -> catalog item keys used by the vendor API
ITEM_WORDS = {
    "laptops": ["laptop", "حاسوب محمول", "لابتوب", "حواسيب"],
    "monitors": ["monitor", "شاشة", "شاشات"],
    "office chairs": ["chair", "كرسي", "كراسي"],
    "printers": ["printer", "طابعة", "طابعات"],
}


def _call(name: str, **args) -> ToolCall:
    return ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=name, arguments=args)


def _task(messages: list[dict]) -> str:
    return next((m["content"] for m in messages if m["role"] == "user"), "")


def _tool_results(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        if m["role"] == "tool":
            try:
                out.append(json.loads(m["content"]))
            except (json.JSONDecodeError, TypeError):
                out.append({"error": "unreadable tool result"})
    return out


def _trailing_errors(results: list[dict]) -> int:
    n = 0
    for r in reversed(results):
        if "error" not in r:
            break
        n += 1
    return n


# ---------------------------------------------------------------- HR agent
def _hr(task: str, results: list[dict], ar: bool):
    emp = re.search(r"\bE\d{3}\b", task)
    asks_leave = bool(re.search(r"leave|vacation|إجاز|رصيد", task, re.I))
    if not results or (results[-1].get("error") and _trailing_errors(results) < MAX_TOOL_RETRIES):
        if asks_leave and emp:
            return None, [_call("get_leave_balance", employee_id=emp.group())]
        return None, [_call("search_knowledge_base", query=task)]

    last = results[-1]
    if "error" in last:
        return ("عذرًا، لم أتمكن من الوصول إلى البيانات المطلوبة حاليًا." if ar
                else "Sorry, I couldn't reach the data I need right now."), []
    if "leave_balance_days" in last:
        if ar:
            return f"رصيد إجازات الموظف {last['employee_id']} ({last.get('name_ar', last['name'])}) هو {last['leave_balance_days']} يومًا.", []
        return f"{last['employee_id']} ({last['name']}) has {last['leave_balance_days']} leave days left.", []
    hits = last.get("results") or []
    if not hits:
        return ("لم أجد معلومات حول هذا الموضوع." if ar
                else "I couldn't find any information about that."), []
    top = hits[0]
    return (f"وفقًا لـ «{top['title']}»: {top['snippet']}" if ar
            else f"According to \"{top['title']}\": {top['snippet']}"), []


# ------------------------------------------------------- Procurement agent
def _parse_order(task: str) -> tuple[str, int]:
    qty = re.search(r"\d+", task)
    low = task.lower()
    for key, words in ITEM_WORDS.items():
        if any(w in low for w in words):
            return key, int(qty.group()) if qty else 1
    return "laptops", int(qty.group()) if qty else 1


def _procurement(task: str, results: list[dict], ar: bool):
    item, qty = _parse_order(task)
    if not results or (results[-1].get("error") and _trailing_errors(results) < MAX_TOOL_RETRIES):
        return None, [_call("get_quotes", item=item, quantity=qty)]

    last = results[-1]
    if "error" in last:
        return json.dumps({"status": "failed",
                           "summary": "تعذر الحصول على عروض الأسعار." if ar
                           else "Could not get vendor quotes."}, ensure_ascii=False), []
    quotes = [q for q in last.get("quotes", []) if isinstance(q.get("unit_price"), (int, float))]
    if not quotes:
        return json.dumps({"status": "failed", "summary": "No valid quotes returned."}), []
    best = min(quotes, key=lambda q: q["unit_price"])
    po = {
        "po_id": f"PO-{uuid.uuid4().hex[:6].upper()}",
        "item": item, "quantity": qty, "vendor": best["vendor"],
        "unit_price": best["unit_price"], "total": round(best["unit_price"] * qty, 2),
        "currency": "SAR",
    }
    summary = (f"أفضل عرض من {best['vendor']} بسعر {best['unit_price']} ريال للوحدة. تم تجهيز أمر الشراء." if ar
               else f"Best quote is {best['vendor']} at {best['unit_price']} SAR/unit. PO drafted.")
    return json.dumps({"status": "ok", "summary": summary, "po": po}, ensure_ascii=False), []


PLANNERS = {"hr": _hr, "procurement": _procurement}


def mock_reply(agent: str, messages: list[dict], tools: list[dict]):
    # Return (content, tool_calls) for one scripted model turn.
    task = _task(messages)
    planner = PLANNERS.get(agent)
    if planner is None:
        return f"[mock] No script for agent '{agent}'.", []
    return planner(task, _tool_results(messages), bool(ARABIC.search(task)))
