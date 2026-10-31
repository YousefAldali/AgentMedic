# Start everything with one command.
#
# python run.py                  # services + fleet workload + doctor + health checks (Ctrl+C to stop)
# python run.py --no-doctor      # without the doctor
# python run.py --no-dashboard   # without the dashboard
# python run.py --reset          # wipe traces.db first
# python run.py --services-only  # only the mock services
# python run.py --seconds 30     # stop automatically after 30 s
import argparse
import signal
import subprocess
import sys
import threading
import time

import httpx

from common import db, registry
from common.config import ROOT, get_config, mock_llm, service_url

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


def main() -> None:
    ap = argparse.ArgumentParser(description="AgentMedic launcher")
    ap.add_argument("--reset", action="store_true", help="delete traces.db before starting")
    ap.add_argument("--services-only", action="store_true")
    ap.add_argument("--no-doctor", action="store_true")
    ap.add_argument("--no-dashboard", action="store_true")
    ap.add_argument("--seconds", type=float, default=0, help="auto-stop after N seconds")
    args = ap.parse_args()

    db.init_db(reset=args.reset)
    registry.seed_prompts()
    from fleet.finance_agent import seed_golden_po
    seed_golden_po()
    print(f"AgentMedic | MOCK_LLM={'on' if mock_llm() else 'off'} | db={get_config()['db_path']}")

    procs = start_services()
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
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", "dashboard/app.py", "--server.port", port,
             "--server.headless", "true", "--browser.gatherUsageStats", "false"],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "dashboard.status_api:app", "--host", "127.0.0.1",
             "--port", str(get_config()["dashboard"]["status_port"]), "--log-level", "warning"], cwd=ROOT))
        print(f"  ✓ dashboard at http://localhost:{port}")
    print("Running. Ctrl+C to stop.\n")
    try:
        deadline = time.time() + args.seconds if args.seconds else None
        while not stop.is_set() and (deadline is None or time.time() < deadline):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=15)
        stop_services(procs)
        print("\nStopped.")


if __name__ == "__main__":
    main()
