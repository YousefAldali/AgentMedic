# Sends a steady stream of realistic English and Arabic tasks to the fleet.
import itertools
import random
import threading
from concurrent.futures import ThreadPoolExecutor

from common.config import get_config
from fleet.base_agent import BaseAgent
from fleet.hr_agent import HRAgent
from fleet.procurement_agent import ProcurementAgent

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
}


def build_fleet() -> dict[str, BaseAgent]:
    return {"hr": HRAgent(), "procurement": ProcurementAgent()}


def _run_one(agent: BaseAgent, task: str) -> None:
    try:
        result = agent.run(task)
        answer = (result["answer"] or "").replace("\n", " ")[:90]
        print(f"[{agent.name:<11}] {result['status']:<7} {task[:45]:<45} -> {answer}", flush=True)
    except Exception as e:
        print(f"[{agent.name:<11}] CRASH  {task[:45]} -> {type(e).__name__}: {e}", flush=True)


def run_forever(stop: threading.Event) -> None:
    cfg = get_config()["workload"]
    fleet = build_fleet()
    order = itertools.cycle(fleet)
    with ThreadPoolExecutor(max_workers=cfg["max_concurrent"]) as pool:
        while not stop.is_set():
            name = next(order)
            pool.submit(_run_one, fleet[name], random.choice(TASKS[name]))
            stop.wait(cfg["interval_s"])
