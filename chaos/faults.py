# Fault injectors. Each fault knows how to inject itself, how to reset, and its
# ground-truth root causes (ids the doctor's findings are scored against).
#
#   python -m chaos.faults list
#   python -m chaos.faults inject F1
#   python -m chaos.faults reset F1      # undo one fault (the doctor then restores the breaker)
#   python -m chaos.faults reset-all     # all services healthy, known-good prompts, runtime switches cleared
#
# Root-cause ids: a service name (vendor_api, knowledge_base, ...), "llm",
# "prompt:<agent>" (bad prompt deployed) or "loop:<agent>" (runaway tool loop).
import json
import sys
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from common import db, registry
from common.config import DATA_DIR, get_config, service_url


def set_service_mode(service: str, mode: str) -> None:
    httpx.post(f"{service_url(service)}/admin/chaos", json={"mode": mode}, timeout=3).raise_for_status()


def deploy_bad_prompt(agent: str) -> None:
    # A new prompt version goes live, the way a bad deploy would.
    bad = json.loads((DATA_DIR / "bad_prompts.json").read_text(encoding="utf-8"))[agent]
    registry.activate(agent, registry.add_version(agent, bad, known_good=False))


def restore_good_prompt(agent: str) -> None:
    good = registry.last_known_good(agent)
    if good is not None:
        registry.activate(agent, good)


def _inject_f10() -> None:
    # Two unrelated problems at once, and the health endpoint sees neither:
    # the vendor API fails some requests (503) while the procurement tool garbles
    # some item names (404). The right move is to escalate, not to guess.
    set_service_mode("vendor_api", "flaky")
    db.set_control("chaos.procurement.bad_item_every", get_config()["chaos"]["bad_item_every"])


def _reset_f10() -> None:
    set_service_mode("vendor_api", "none")
    db.execute("DELETE FROM control WHERE key = 'chaos.procurement.bad_item_every'")


@dataclass
class Fault:
    id: str
    name: str
    inject: Callable[[], None]
    reset: Callable[[], None]
    root_causes: list[str]
    expect_escalation: bool = False


FAULTS = {f.id: f for f in [
    Fault("F1", "Vendor API down",
          lambda: set_service_mode("vendor_api", "down"),
          lambda: set_service_mode("vendor_api", "none"),
          ["vendor_api"]),
    Fault("F2", "Bad HR prompt deployed (no errors, wrong answers)",
          lambda: deploy_bad_prompt("hr"),
          lambda: restore_good_prompt("hr"),
          ["prompt:hr"]),
    Fault("F3", "Knowledge base index corrupted",
          lambda: set_service_mode("knowledge_base", "corrupt"),
          lambda: set_service_mode("knowledge_base", "none"),
          ["knowledge_base"]),
    Fault("F4", "Primary LLM endpoint slow",
          lambda: db.set_control("llm.chaos", {"mode": "slow", "latency_ms": get_config()["chaos"]["llm_slow_ms"]}),
          lambda: db.execute("DELETE FROM control WHERE key = 'llm.chaos'"),
          ["llm"]),
    Fault("F5", "Runaway loop: a vendor quote job never finishes",
          lambda: db.set_control("chaos.procurement.stuck_item", get_config()["chaos"]["stuck_item"]),
          lambda: db.execute("DELETE FROM control WHERE key = 'chaos.procurement.stuck_item'"),
          ["loop:procurement"]),
    Fault("F6", "Cascade: bad procurement prompt writes wrong PO totals, finance fails",
          lambda: deploy_bad_prompt("procurement"),
          lambda: restore_good_prompt("procurement"),
          ["prompt:procurement"]),
    Fault("F10", "Ambiguous evidence: partial vendor outage + misbehaving tool",
          lambda: _inject_f10(),
          lambda: _reset_f10(),
          ["vendor_api (partial outage)", "procurement tool (garbled requests)"],
          expect_escalation=True),
]}

CONTROL_PREFIXES = ("circuit.", "fallback.", "llm.", "agent.", "chaos.")


def fresh_start_for_doctor() -> None:
    # Tells the doctor (in whatever process it runs) to forget past incidents and pauses,
    # so a new test or demo run starts clean.
    db.set_control("doctor.reset_at", time.time())


def reset_all() -> None:
    for service in get_config()["services"]:
        set_service_mode(service, "none")
    for prefix in CONTROL_PREFIXES:
        db.execute("DELETE FROM control WHERE key LIKE ?", (prefix + "%",))
    for agent in registry.dependencies():
        restore_good_prompt(agent)
    db.execute("UPDATE approvals SET status = 'expired' WHERE status = 'pending'")
    fresh_start_for_doctor()


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        for f in FAULTS.values():
            print(f"{f.id:<4} {f.name}")
    elif cmd == "inject":
        FAULTS[argv[1]].inject()
        fresh_start_for_doctor()
        print(f"injected {argv[1]}: {FAULTS[argv[1]].name}")
    elif cmd == "reset":
        FAULTS[argv[1]].reset()
        print(f"reset {argv[1]}")
    elif cmd == "reset-all":
        reset_all()
        print("all faults reset")
    else:
        sys.exit(f"unknown command '{cmd}'")


if __name__ == "__main__":
    main(sys.argv[1:])
