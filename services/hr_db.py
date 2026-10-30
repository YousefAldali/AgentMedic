"""Mock HR database: employees, leave balances, policies."""
import json

from fastapi import FastAPI, HTTPException

from common.config import DATA_DIR
from services.chaos import Chaos

app = FastAPI(title="HR DB")
chaos = Chaos("hr_db")
app.include_router(chaos.router)

EMPLOYEES = {e["id"]: e for e in json.loads((DATA_DIR / "employees.json").read_text(encoding="utf-8"))}
POLICIES = json.loads((DATA_DIR / "policies.json").read_text(encoding="utf-8"))


@app.get("/employees/{employee_id}/leave")
def leave_balance(employee_id: str):
    corrupt = chaos.check()
    emp = EMPLOYEES.get(employee_id.upper())
    if not emp:
        raise HTTPException(404, f"Employee {employee_id} not found")
    return {
        "employee_id": emp["id"], "name": emp["name"], "name_ar": emp["name_ar"],
        "department": emp["department"],
        "leave_balance_days": None if corrupt else emp["leave_balance_days"],
    }


@app.get("/policies/{topic}")
def policy(topic: str):
    corrupt = chaos.check()
    p = POLICIES.get(topic)
    if not p:
        raise HTTPException(404, f"Policy '{topic}' not found")
    return {"topic": topic, "en": "" if corrupt else p["en"], "ar": "" if corrupt else p["ar"]}


@app.get("/admin/state")
def state():
    return {"service": "hr_db", "status": chaos.status(),
            "employees": len(EMPLOYEES), "policies": len(POLICIES),
            "data_integrity": "failed" if chaos.mode == "corrupt" else "ok"}
