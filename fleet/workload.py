# Sends a steady stream of realistic English and Arabic tasks to the fleet.
import random
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx

from common.config import get_config, service_url
from fleet.base_agent import BaseAgent, ToolError
from fleet.finance_agent import FinanceAgent, invoice_task
from fleet.hr_agent import HRAgent
from fleet.procurement_agent import ProcurementAgent, get_quotes
from fleet.support_agent import SupportAgent

TASKS = {
    "hr": [
        "How many leave days does E002 have left?",
        "كم رصيد إجازات الموظف E003؟",
        "How many leave days does E005 have left?",
        "كم يومًا تبقى من إجازة الموظف E001؟",
        "What is the remote work policy?",
        "ما هي سياسة العمل عن بعد؟",
        "How many unused annual leave days can be carried over?",
        "ما هي سياسة استرداد المصروفات؟",
    ],
    "procurement": [
        "Compare quotes for 50 laptops and draft a PO.",
        "قارن عروض الأسعار لـ 20 شاشة وجهّز أمر شراء.",
        "Get quotes for 10 office chairs and draft a PO.",
        "Compare quotes for 5 printers.",
        "أحتاج 15 حاسوب محمول، جهّز أمر شراء بأفضل سعر.",
    ],
    "support": [
        "How do I reset my password?",
        "I bought the service 3 days ago and haven't used it. Can I get a refund?",
        "How long does a refund take to reach my card?",
        "نسيت كلمة المرور، ماذا أفعل؟",
        "هل يمكنني استرداد المبلغ بعد 20 يومًا من الشراء؟",
    ],
}


def next_task(agent: str) -> str:
    if agent == "finance":
        return invoice_task()
    return random.choice(TASKS[agent])


def build_fleet() -> dict[str, BaseAgent]:
    return {"hr": HRAgent(), "procurement": ProcurementAgent(), "finance": FinanceAgent(),
            "support": SupportAgent()}


def _run_one(agent: BaseAgent, task: str) -> None:
    try:
        result = agent.run(task)
        answer = (result["answer"] or "").replace("\n", " ")[:90]
        print(f"[{agent.name:<11}] {result['status']:<7} {task[:45]:<45} -> {answer}", flush=True)
    except Exception as e:  # keep the workload alive no matter what
        print(f"[{agent.name:<11}] CRASH  {task[:45]} -> {type(e).__name__}: {e}", flush=True)


def warm_cache() -> None:
    # Fetch every catalog item once at startup so the quote cache is complete
    # before any fault can happen (a normal cache warm-up).
    try:
        items = httpx.get(f"{service_url('vendor_api')}/admin/state", timeout=3).json()["items"]
        for item in items:
            get_quotes(item, 1)
    except (httpx.HTTPError, ToolError, KeyError) as e:
        print(f"cache warm-up skipped: {e}", flush=True)


def run_forever(stop: threading.Event) -> None:
    cfg = get_config()["workload"]
    warm_cache()
    fleet = build_fleet()
    # Every agent gets one new task per interval, spread out so they don't all start at once.
    gap = cfg["interval_s"] / len(fleet)
    with ThreadPoolExecutor(max_workers=cfg["max_concurrent"]) as pool:
        while not stop.is_set():
            for name, agent in fleet.items():
                pool.submit(_run_one, agent, next_task(name))
                if stop.wait(gap):
                    break
