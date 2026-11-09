# Scripted, deterministic LLM replies used by the tests instead of a paid model.
#
# Each agent has a small "planner" that looks at the task and the tool results
# so far, and returns either tool calls or a final answer, the same way a real
# model would. Replies are in Arabic when the task is in Arabic.
import json
import re
import uuid

from common.llm import ToolCall

ARABIC = re.compile(r"[؀-ۿ]")
MAX_TOOL_RETRIES = 3  # how often a scripted agent retries a failing tool before giving up

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
def _hr(task: str, results: list[dict], ar: bool, system: str = ""):
    if "do not call any tools" in system:
        # A bad prompt: answers from "general knowledge" - confident, fluent and wrong.
        return ("لكل موظف 30 يومًا من الإجازة سنويًا، والسياسات قياسية." if ar
                else "Every employee has 30 days of leave per year, and the policies are standard."), []
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
    return _kb_answer(last, ar)


# ------------------------------------------------------- Procurement agent
def _parse_order(task: str) -> tuple[str, int]:
    qty = re.search(r"\d+", task)
    low = task.lower()
    for key, words in ITEM_WORDS.items():
        if any(w in low for w in words):
            return key, int(qty.group()) if qty else 1
    return "laptops", int(qty.group()) if qty else 1


def _procurement(task: str, results: list[dict], ar: bool, system: str = ""):
    item, qty = _parse_order(task)
    if results and results[-1].get("status") == "pending":
        # The tool says the quote is still being prepared: ask again (forever, if it never finishes).
        return None, [_call("get_quotes", item=item, quantity=qty)]
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
        "unit_price": best["unit_price"],
        "total": best["unit_price"] if "do not multiply" in system else round(best["unit_price"] * qty, 2),
        "currency": "SAR",
    }
    summary = (f"أفضل عرض من {best['vendor']} بسعر {best['unit_price']} ريال للوحدة. تم تجهيز أمر الشراء." if ar
               else f"Best quote is {best['vendor']} at {best['unit_price']} SAR/unit. PO drafted.")
    return json.dumps({"status": "ok", "summary": summary, "po": po}, ensure_ascii=False), []


# ---------------------------------------------------------- Support agent
def _kb_answer(result: dict, ar: bool):
    hits = result.get("results") or []
    if not hits or not hits[0].get("snippet"):
        return ("لم أجد معلومات حول هذا الموضوع." if ar
                else "I couldn't find any information about that."), []
    top = hits[0]
    return (f"وفقًا لـ «{top['title']}»: {top['snippet']}" if ar
            else f"According to \"{top['title']}\": {top['snippet']}"), []


def _support(task: str, results: list[dict], ar: bool, system: str = ""):
    if not results or (results[-1].get("error") and _trailing_errors(results) < MAX_TOOL_RETRIES):
        return None, [_call("search_knowledge_base", query=task)]
    if "error" in results[-1]:
        return ("عذرًا، لا يمكنني الوصول إلى قاعدة المعرفة حاليًا. سأحولك إلى موظف." if ar
                else "Sorry, I can't reach the knowledge base right now. Let me connect you to a human."), []
    return _kb_answer(results[-1], ar)


# ---------------------------------------------------------- Finance agent
def _finance(task: str, results: list[dict], ar: bool, system: str = ""):
    po_id = re.search(r"PO-[A-Z0-9]+", task)
    inv = re.search(r"INV-\d+", task)
    amount = re.search(r"([\d,.]+)\s*(?:SAR|ريال)", task)
    if not results or (results[-1].get("error") and _trailing_errors(results) < MAX_TOOL_RETRIES):
        return None, [_call("get_purchase_order", po_id=po_id.group() if po_id else "")]

    out = {"invoice_id": inv.group() if inv else None, "po_id": po_id.group() if po_id else None}
    po = results[-1]
    if "error" in po or not po.get("valid"):
        out.update(decision="error", reason="تعذر الحصول على أمر شراء صالح." if ar
                   else "Could not retrieve a valid purchase order.")
        return json.dumps(out, ensure_ascii=False), []
    try:
        invoice_total = float(amount.group(1).replace(",", "")) if amount else None
        po_total = float(po["total"])
    except (TypeError, ValueError):
        invoice_total, po_total = None, None
    vendor_ok = bool(po.get("vendor")) and po["vendor"] in task
    total_ok = invoice_total is not None and po_total is not None and abs(invoice_total - po_total) < 0.01
    if vendor_ok and total_ok:
        out.update(decision="approved", reason="المورد والمبلغ مطابقان لأمر الشراء." if ar
                   else "Vendor and total match the purchase order.")
    elif not vendor_ok:
        out.update(decision="rejected", reason="المورد لا يطابق أمر الشراء." if ar
                   else "Vendor does not match the purchase order.")
    else:
        out.update(decision="rejected", reason=f"المبلغ {invoice_total} لا يطابق أمر الشراء ({po_total})." if ar
                   else f"Invoice total {invoice_total} does not match the PO total {po_total}.")
    return json.dumps(out, ensure_ascii=False), []


PLANNERS = {"hr": _hr, "procurement": _procurement, "finance": _finance, "support": _support}


def fake_reply(agent: str, messages: list[dict], tools: list[dict]):
    # Return (content, tool_calls) for one scripted model turn.
    task = _task(messages)
    planner = PLANNERS.get(agent)
    if planner is None:
        return f"No script for agent '{agent}'.", []
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    return planner(task, _tool_results(messages), bool(ARABIC.search(task)), system)
