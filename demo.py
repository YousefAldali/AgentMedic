# Demo helper: check everything before going on stage, and reset to healthy between runs.
#
#   python demo.py check          # pre-stage checklist (add --api to test the real model with one tiny call)
#   python demo.py reset          # all faults off, known-good prompts, pending approvals cleared, doctor fresh
#
# For a completely clean screen (no old incidents), restart instead: python run.py --reset
import importlib
import socket
import sys

from common.config import ROOT, env, get_config, mock_llm

OK, WARN, BAD = "OK  ", "WARN", "FAIL"


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def check(test_api: bool) -> int:
    problems = 0

    def line(status: str, text: str) -> None:
        nonlocal problems
        problems += status == BAD
        print(f"[{status}] {text}")

    for pkg in ("fastapi", "uvicorn", "httpx", "openai", "pydantic", "yaml", "streamlit", "matplotlib"):
        try:
            importlib.import_module(pkg)
        except ImportError:
            line(BAD, f"package '{pkg}' missing: run  uv pip install -p .venv -r requirements.txt")
    line(OK, "packages installed") if problems == 0 else None

    if (ROOT / ".env").exists():
        line(OK, ".env found")
    else:
        line(WARN, ".env missing: copy .env.example to .env (MOCK mode works without it)")
    if mock_llm():
        line(OK, "MOCK mode is ON: scripted model, free, works offline")
    else:
        key = env("LLM_API_KEY", "")
        line(OK if key else BAD, f"real-model mode: key {'set (' + key[:7] + '…)' if key else 'MISSING'}, "
             f"fleet {env('FLEET_MODEL', '-')}, doctor {env('DOCTOR_MODEL', '-')}")

    cfg = get_config()
    ports = {name: s["port"] for name, s in cfg["services"].items()}
    ports.update({"dashboard": cfg["dashboard"]["port"], "office feed": cfg["dashboard"]["status_port"]})
    running = {name: port_in_use(p) for name, p in ports.items()}
    if all(running.values()):
        line(OK, "everything is running: open http://localhost:%d" % cfg["dashboard"]["port"])
    elif not any(running.values()):
        line(WARN, "nothing is running yet: start it with  python run.py --reset")
    else:
        down = ", ".join(n for n, up in running.items() if not up)
        line(BAD, f"only partly running (not up: {down}): stop everything and start again with  python run.py --reset")

    if test_api and not mock_llm():
        from common import llm
        try:
            r = llm.chat([{"role": "user", "content": "Reply with the single word: ready"}], agent="doctor", role="doctor")
            line(OK, f"real model answered: {(r.content or '').strip()[:30]!r} in {r.latency_ms:.0f} ms")
        except llm.LLMError as e:
            line(BAD, f"real model call failed ({e}). Fallback: restart with MOCK_LLM=1")
    elif test_api:
        line(OK, "skipped the model test (MOCK mode)")

    print("\nReady for the demo." if problems == 0 else f"\n{problems} problem(s) to fix before the demo.")
    return 1 if problems else 0


def reset() -> int:
    from chaos.faults import reset_all
    try:
        reset_all()
    except Exception as e:  # services not running
        print(f"Could not reach the services ({type(e).__name__}). Start them with: python run.py --reset")
        return 1
    print("Reset done: all faults off, known-good prompts active, pending approvals cleared, doctor starts fresh.")
    print("Tip: refresh the dashboard. For no old incidents at all, restart with: python run.py --reset")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in ("check", "reset"):
        sys.exit(__doc__ if __doc__ else "usage: python demo.py check [--api] | reset")
    sys.exit(check("--api" in args) if args[0] == "check" else reset())
