# Start everything with one command.
#
# python run.py                  # services + fleet workload + doctor + health checks (Ctrl+C to stop)
# python run.py --no-doctor      # without the doctor
# python run.py --no-dashboard   # without the dashboard
# python run.py --reset          # wipe traces.db first
# python run.py --services-only  # only the simulated HR, supplier and knowledge base services
# python run.py --seconds 30     # stop automatically after 30 s
# python run.py --check          # check the setup (add --api to test the AI model with one tiny call)
# python run.py --clear-faults   # while running: turn every failure off and restore the known-good prompts
import argparse
import importlib
import signal
import socket
import subprocess
import sys
import threading
import time

import httpx

from common import db, registry
from common.config import ROOT, env, get_config, service_url

SERVICES = {
    "hr_db": "services.hr_db:app",
    "vendor_api": "services.vendor_api:app",
    "knowledge_base": "services.knowledge_base:app",
}


def start_services() -> list[subprocess.Popen]:
    procs = []
    for name, target in SERVICES.items():
        svc = get_config()["services"][name]
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", target, "--host", svc["host"],
             "--port", str(svc["port"]), "--log-level", "warning"], cwd=ROOT))
    for name in SERVICES:
        url = f"{service_url(name)}/admin/state"
        for _ in range(50):
            try:
                if httpx.get(url, timeout=1).status_code == 200:
                    print(f"  ✓ {name} up at {service_url(name)}")
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            stop_services(procs)
            sys.exit(f"  ✗ {name} did not start")
    return procs


def stop_services(procs: list[subprocess.Popen]) -> None:
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def check(test_api: bool) -> int:
    problems = 0

    def line(status: str, text: str) -> None:
        nonlocal problems
        problems += status == "FAIL"
        print(f"[{status:<4}] {text}")

    if sys.version_info < (3, 11):
        line("FAIL", f"Python {sys.version.split()[0]} is too old: AgentMedic needs Python 3.11 or newer")
    missing = []
    for pkg in ("fastapi", "uvicorn", "httpx", "openai", "pydantic", "yaml", "streamlit", "matplotlib"):
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        line("FAIL", f"missing packages ({', '.join(missing)}): run  uv pip install -p .venv -r requirements.txt")
    else:
        import streamlit
        if tuple(int(x) for x in streamlit.__version__.split(".")[:2]) < (1, 66):
            line("FAIL", f"streamlit {streamlit.__version__} is too old for the dashboard: run  "
                 "uv pip install -p .venv -r requirements.txt")
        else:
            line("OK", "packages installed")

    if (ROOT / ".env").exists():
        line("OK", ".env found")
    else:
        line("FAIL", ".env missing: copy .env.example to .env and add your AI model settings")
    from common import llm
    try:
        ep = llm.endpoint()
        line("OK", f"AI provider: {ep['provider']}" + (f" at {ep['base_url']}" if ep["base_url"] else ""))
    except llm.LLMError as e:
        ep = {"api_key": None, "local": False}
        line("FAIL", str(e))
    key, fleet = ep["api_key"], env("FLEET_MODEL", "")
    if ep["local"]:
        line("OK", "local AI server: no API key needed")
    else:
        line("OK" if key else "FAIL", "LLM_API_KEY " + ("set" if key else "MISSING: add it to .env"))
    line("OK" if fleet else "FAIL", f"FLEET_MODEL {fleet or 'MISSING: add the model name to .env'}, "
         f"doctor {env('DOCTOR_MODEL') or fleet or '-'}")
    line("WARN", f"AgentMedic calls your AI model all the time: 4 agents get a new task every "
         f"{get_config()['workload']['interval_s']} s and health checks run every "
         f"{get_config()['health']['interval_s']} s. Stop it (Ctrl+C) when you are done")

    cfg = get_config()
    ports = {name: svc["port"] for name, svc in cfg["services"].items()}
    ports.update({"dashboard": cfg["dashboard"]["port"], "office feed": cfg["dashboard"]["status_port"]})
    running = {name: port_in_use(p) for name, p in ports.items()}
    if all(running.values()):
        line("OK", "everything is running: open http://localhost:%d" % cfg["dashboard"]["port"])
    elif not any(running.values()):
        line("WARN", "nothing is running yet: start it with  python run.py --reset")
    else:
        down = ", ".join(n for n, up in running.items() if not up)
        line("FAIL", f"only partly running (not up: {down}): stop everything and start again with  python run.py --reset")

    if test_api and key and fleet:
        try:
            r = llm.chat([{"role": "user", "content": "Reply with the single word: ready"}], agent="doctor", role="doctor")
            line("OK", f"AI model answered: {(r.content or '').strip()[:30]!r} in {r.latency_ms:.0f} ms")
        except llm.LLMError as e:
            line("FAIL", f"AI model call failed: {e}")

    print("\nAll good." if problems == 0 else f"\n{problems} problem(s) to fix.")
    return 1 if problems else 0


def clear_faults() -> int:
    from chaos.faults import reset_all
    try:
        reset_all()
    except Exception as e:
        print(f"Could not reach the services ({type(e).__name__}). Start them with: python run.py --reset")
        return 1
    print("Done: all failures off, known-good prompts active, pending approvals cleared, doctor starts fresh.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="AgentMedic launcher")
    ap.add_argument("--reset", action="store_true", help="delete traces.db before starting")
    ap.add_argument("--services-only", action="store_true")
    ap.add_argument("--no-doctor", action="store_true")
    ap.add_argument("--no-dashboard", action="store_true")
    ap.add_argument("--seconds", type=float, default=0, help="auto-stop after N seconds")
    ap.add_argument("--check", action="store_true", help="check the setup and exit")
    ap.add_argument("--api", action="store_true", help="with --check: also test the real model")
    ap.add_argument("--clear-faults", action="store_true", help="turn every failure off in a running system and exit")
    args = ap.parse_args()
    if args.check:
        sys.exit(check(args.api))
    if args.clear_faults:
        sys.exit(clear_faults())

    if not args.services_only:
        from common import llm
        try:
            ready = bool(llm.endpoint()["api_key"] and env("FLEET_MODEL"))
        except llm.LLMError as e:
            sys.exit(str(e))
        if not ready:
            sys.exit("AgentMedic needs an AI model. Copy .env.example to .env, pick LLM_PROVIDER (or LLM_BASE_URL), "
                     "and set LLM_API_KEY and FLEET_MODEL. Then check it with:  python run.py --check --api")
    db.init_db(reset=args.reset)
    registry.seed_prompts()
    from fleet.finance_agent import seed_golden_po
    seed_golden_po()
    print(f"AgentMedic | model {env('FLEET_MODEL') or '-'} | db={get_config()['db_path']}")

    procs = start_services()
    log = None
    from chaos.faults import reset_all
    reset_all()  # start clean: no faults, no breakers left over from a previous run
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    threads = []
    if not args.services_only:
        from fleet.workload import run_forever as run_workload
        threads.append(threading.Thread(target=run_workload, args=(stop,), daemon=True))
        if not args.no_doctor:
            from doctor.doctor import run_forever as run_doctor
            from doctor.monitor import run_health_forever
            threads.append(threading.Thread(target=run_doctor, args=(stop,), daemon=True))
            threads.append(threading.Thread(target=run_health_forever, args=(stop,), daemon=True))
        for t in threads:
            t.start()
    if not args.no_dashboard:
        port = str(get_config()["dashboard"]["port"])
        log = open(ROOT / "dashboard.log", "w", encoding="utf-8")
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "dashboard/app.py", "--server.port", port,
             "--server.headless", "true", "--browser.gatherUsageStats", "false"],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT))
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "dashboard.status_api:app", "--host", "127.0.0.1",
             "--port", str(get_config()["dashboard"]["status_port"]), "--log-level", "warning"], cwd=ROOT))
        print(f"  ✓ dashboard at http://localhost:{port}  (its messages go to dashboard.log)")
    print("Running. Ctrl+C to stop.\n")
    try:
        deadline = time.time() + args.seconds if args.seconds else None
        next_prune = time.time() + 60
        while not stop.is_set() and (deadline is None or time.time() < deadline):
            time.sleep(0.5)
            if time.time() >= next_prune:
                next_prune = time.time() + 60
                try:
                    db.prune(get_config()["keep_history_s"])
                except Exception as e:
                    print(f"cleanup skipped: {type(e).__name__}: {e}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=15)
        stop_services(procs)
        if log:
            log.close()
        print("\nStopped.")


if __name__ == "__main__":
    main()
