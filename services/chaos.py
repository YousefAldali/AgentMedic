# Shared fault switch for the mock services.
#
# Each service gets:
#   POST /admin/chaos {"mode": "none|down|slow|error_500|corrupt"}  (used by chaos/ and the demo panel)
#   GET  /admin/chaos                                                (current mode, for the chaos panel)
# Business endpoints call `chaos.check()` first. `/admin/*` is never affected by chaos,
# so the doctor can always read true state from `/admin/state`.
import time
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from common.config import get_config

Mode = Literal["none", "down", "slow", "error_500", "corrupt"]


class ChaosRequest(BaseModel):
    mode: Mode


class Chaos:
    def __init__(self, service: str):
        self.service = service
        self.mode: str = "none"
        self.router = APIRouter(prefix="/admin")

        @self.router.post("/chaos")
        def set_mode(req: ChaosRequest):
            self.mode = req.mode
            return {"service": self.service, "mode": self.mode}

        @self.router.get("/chaos")
        def get_mode():
            return {"service": self.service, "mode": self.mode}

    def check(self) -> bool:
        # Apply the current fault. Returns True if the response should be corrupted.
        if self.mode == "down":
            # Simulated outage: fail fast like an unreachable upstream.
            raise HTTPException(503, f"{self.service} unavailable")
        if self.mode == "error_500":
            raise HTTPException(500, f"{self.service} internal error")
        if self.mode == "slow":
            time.sleep(get_config()["chaos"]["slow_delay_s"])
        return self.mode == "corrupt"

    def status(self) -> str:
        # Health status as a real service's health endpoint would report it.
        return {"none": "up", "down": "down", "slow": "degraded",
                "error_500": "erroring", "corrupt": "up"}[self.mode]
