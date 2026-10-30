# Procurement agent: gets vendor quotes, picks the best one, drafts a PO.
import itertools
import json
import threading
import time

from common import db
from common.config import service_url
from fleet.base_agent import BaseAgent, Tool, call_dependency, http_get


_calls = itertools.count(1)
_lock = threading.Lock()


def get_quotes(item: str, quantity: int = 1) -> dict:
    if item.lower() == db.get_control("chaos.procurement.stuck_item"):
        # Chaos hook (F5): the vendor's quote job for this item never finishes.
        return {"item": item, "status": "pending",
                "message": "Quote is still being prepared. Call get_quotes again with the same arguments."}
    sent = item
    every = db.get_control("chaos.procurement.bad_item_every", 0)
    with _lock:
        n = next(_calls)
    if every and n % every == 0:
        # Chaos hook (F10): a misbehaving tool sends a garbled item name -> 404 from the vendor API.
        sent = item.rstrip("s") + "-x"
    data = call_dependency("vendor_api", item.lower(), lambda: http_get(
        f"{service_url('vendor_api')}/quotes", {"item": sent, "quantity": quantity}))
    return {**data, "quantity": quantity}


def save_po(run_id: str, answer: str | None) -> None:
    # Stores the PO from procurement's answer so finance can validate invoices against it.
    # Malformed POs are stored too (valid = 0): that is how a bad upstream output reaches finance.
    text = answer or ""
    try:
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        po = data.get("po") or {}
        po_id = str(po.get("po_id", "")).strip().upper()
    except (ValueError, AttributeError):
        return
    if not po_id:
        return
    try:
        valid = (data.get("status") == "ok" and isinstance(po.get("vendor"), str) and po["vendor"]
                 and int(po["quantity"]) > 0
                 and abs(float(po["unit_price"]) * int(po["quantity"]) - float(po["total"])) < 0.01)
    except (KeyError, TypeError, ValueError):
        valid = False
    db.execute(
        "INSERT OR REPLACE INTO purchase_orders(po_id, run_id, item, quantity, vendor, unit_price, total, "
        "currency, raw, valid, ts) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (po_id, run_id, str(po.get("item")), po.get("quantity"), po.get("vendor"), po.get("unit_price"),
         po.get("total"), po.get("currency"), text[:2000], int(bool(valid)), time.time()))


def regenerate_po(po_id: str) -> bool:
    # Replay: run the procurement task that produced a PO again and store the new PO under
    # the same PO id, so invoices that refer to it can be validated. True if the new PO is valid.
    rows = db.query("SELECT r.task FROM purchase_orders p JOIN runs r ON r.run_id = p.run_id WHERE p.po_id = ?",
                    (po_id,))
    if not rows:
        return False
    task = rows[0]["task"].removeprefix("[replay] ")
    result = ProcurementAgent().run(f"[replay] {task}")
    text = result["answer"] or ""
    try:
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        data["po"]["po_id"] = po_id
    except (ValueError, KeyError, TypeError):
        return False
    save_po(result["run_id"], json.dumps(data, ensure_ascii=False))
    return bool(db.query("SELECT valid FROM purchase_orders WHERE po_id = ?", (po_id,))[0]["valid"])


class ProcurementAgent(BaseAgent):
    name = "procurement"
    tools = {
        "get_quotes": Tool(get_quotes, {
            "type": "function", "function": {
                "name": "get_quotes",
                "description": "Get vendor price quotes for a catalog item "
                               "(laptops, monitors, office chairs, printers).",
                "parameters": {"type": "object", "properties": {
                    "item": {"type": "string", "enum": ["laptops", "monitors", "office chairs", "printers"]},
                    "quantity": {"type": "integer", "minimum": 1}},
                    "required": ["item", "quantity"]}}}),
    }

    def run(self, task: str) -> dict:
        result = super().run(task)
        if result["status"] == "ok" and not task.startswith("[replay]"):
            save_po(result["run_id"], result["answer"])
        return result
