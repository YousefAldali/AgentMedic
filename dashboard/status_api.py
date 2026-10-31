# Small JSON feed for the live office view (the browser polls it twice a second).
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dashboard import data

app = FastAPI(title="AgentMedic status")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


@app.get("/office")
def office():
    return data.office_state()
