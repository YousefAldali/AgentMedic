# Procurement agent: gets vendor quotes, picks the best one, drafts a PO.
from common.config import service_url
from fleet.base_agent import BaseAgent, Tool, call_dependency, http_get


def get_quotes(item: str, quantity: int = 1) -> dict:
    data = call_dependency("vendor_api", item.lower(), lambda: http_get(
        f"{service_url('vendor_api')}/quotes", {"item": item, "quantity": quantity}))
    return {**data, "quantity": quantity}


class ProcurementAgent(BaseAgent):
    name = "procurement"
    tools = {
        "get_quotes": Tool(get_quotes, {
            "type": "function", "function": {
                "name": "get_quotes",
                "description": "Get vendor price quotes for a catalog item "
                               "(laptops, monitors, office chairs, printers).",
                "parameters": {"type": "object", "properties": {
                    "item": {"type": "string"},
                    "quantity": {"type": "integer", "minimum": 1}},
                    "required": ["item", "quantity"]}}}),
    }
