# Fault injectors. Each fault knows how to inject itself, how to reset, and the
# ground-truth root cause (used later by the benchmark).
#
#   python -m chaos.faults list
#   python -m chaos.faults inject F1
#   python -m chaos.faults reset F1      # undo one fault (the doctor then restores the breaker)
#   python -m chaos.faults reset-all     # all services healthy, all runtime switches cleared
import sys
from dataclasses import dataclass
from typing import Callable

import httpx

from common import db
from common.config import get_config, service_url


def set_service_mode(service: str, mode: str) -> None:
    httpx.post(f"{service_url(service)}/admin/chaos", json={"mode": mode}, timeout=3).raise_for_status()


@dataclass
class Fault:
    id: str
    name: str
    inject: Callable[[], None]
    reset: Callable[[], None]
    root_causes: list[str]


FAULTS = {f.id: f for f in [
    Fault("F1", "Vendor API down",
          lambda: set_service_mode("vendor_api", "down"),
          lambda: set_service_mode("vendor_api", "none"),
          ["vendor_api"]),
]}

CONTROL_PREFIXES = ("circuit.", "fallback.", "llm.", "agent.")


def reset_all() -> None:
    for service in get_config()["services"]:
        set_service_mode(service, "none")
    for prefix in CONTROL_PREFIXES:
        db.execute("DELETE FROM control WHERE key LIKE ?", (prefix + "%",))


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        for f in FAULTS.values():
            print(f"{f.id}  {f.name}")
    elif cmd == "inject":
        FAULTS[argv[1]].inject()
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
