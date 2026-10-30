# Finance agent: validates supplier invoices against Procurement's purchase orders.
# It depends on Procurement's output, which makes it the downstream end of the cascade path.
import random
import time

from common import db
from fleet.base_agent import BaseAgent, Tool, ToolError

GOLDEN_PO = {"po_id": "PO-GOLD01", "item": "laptops", "quantity": 10, "vendor": "eXtra Enterprise",
             "unit_price": 3950, "total": 39500, "currency": "SAR"}


def get_purchase_order(po_id: str) -> dict:
    rows = db.query("SELECT po_id, item, quantity, vendor, unit_price, total, currency, valid "
                    "FROM purchase_orders WHERE po_id = ?", (po_id.strip().upper(),))
    if not rows:
        raise ToolError(f"purchase order {po_id} not found")
    return rows[0]


def seed_golden_po() -> None:
    # A fixed PO the golden health checks can always validate against.
    db.execute(
        "INSERT OR REPLACE INTO purchase_orders(po_id, run_id, item, quantity, vendor, unit_price, total, "
        "currency, raw, valid, ts) VALUES(?, 'seed', ?, ?, ?, ?, ?, ?, '', 1, ?)",
        (GOLDEN_PO["po_id"], GOLDEN_PO["item"], GOLDEN_PO["quantity"], GOLDEN_PO["vendor"],
         GOLDEN_PO["unit_price"], GOLDEN_PO["total"], GOLDEN_PO["currency"], time.time()))


def invoice_task() -> str:
    # A supplier invoice for a recent PO. The supplier bills the real amount (unit price x quantity),
    # so a PO with a wrong total cannot be matched. About 1 in 3 invoices is overbilled and
    # should be rejected.
    rows = db.query("SELECT po_id, vendor, unit_price, quantity FROM purchase_orders "
                    "WHERE run_id != 'seed' AND vendor IS NOT NULL ORDER BY ts DESC LIMIT 10")
    po = random.choice(rows) if rows else GOLDEN_PO
    try:
        amount = round(float(po["unit_price"]) * int(po["quantity"]), 2)
    except (TypeError, ValueError):
        amount = GOLDEN_PO["total"]
    total = amount if random.random() < 0.67 else round(amount * 1.1, 2)
    inv = f"INV-{random.randint(1000, 9999)}"
    total_text = f"{total:g}"
    if random.random() < 0.5:
        return f"Validate invoice {inv} from {po['vendor']} for {total_text} SAR against {po['po_id']}."
    return f"تحقق من الفاتورة {inv} من {po['vendor']} بمبلغ {total_text} ريال مقابل أمر الشراء {po['po_id']}."


class FinanceAgent(BaseAgent):
    name = "finance"
    tools = {
        "get_purchase_order": Tool(get_purchase_order, {
            "type": "function", "function": {
                "name": "get_purchase_order",
                "description": "Get a purchase order drafted by the procurement agent, by PO id (e.g. PO-1A2B3C).",
                "parameters": {"type": "object", "properties": {
                    "po_id": {"type": "string"}}, "required": ["po_id"]}}}),
    }
