"""Mock vendor API: price quotes per catalog item."""
import json

from fastapi import FastAPI, HTTPException

from common.config import DATA_DIR
from services.chaos import Chaos

app = FastAPI(title="Vendor API")
chaos = Chaos("vendor_api")
app.include_router(chaos.router)

QUOTES = json.loads((DATA_DIR / "vendor_quotes.json").read_text(encoding="utf-8"))


@app.get("/quotes")
def quotes(item: str, quantity: int = 1):
    corrupt = chaos.check()
    rows = QUOTES.get(item.lower())
    if rows is None:
        raise HTTPException(404, f"No vendors for item '{item}'")
    if corrupt:
        rows = [{"vendor": q["vendor"], "unit_price": "N/A", "delivery_days": -1} for q in rows]
    return {"item": item.lower(), "quantity": quantity, "quotes": rows}


@app.get("/admin/state")
def state():
    return {"service": "vendor_api", "status": chaos.status(), "items": sorted(QUOTES),
            "data_integrity": "failed" if chaos.mode == "corrupt" else "ok"}
