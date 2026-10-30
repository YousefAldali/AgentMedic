# AgentMedic

An AI agent that monitors a fleet of enterprise AI agents, detects when they fail,
diagnoses the root cause (including several faults at once), fixes them safely,
verifies recovery, and writes an incident report in English and Arabic.

> Status: **Phase 1 (foundation)** — mock services, trace store, LLM gateway, HR + Procurement agents, workload.

## Setup

```bash
uv venv .venv
uv pip install -p .venv -r requirements.txt
cp .env.example .env        # MOCK_LLM=1 by default: no API key needed
```

## Run

```bash
.venv/bin/python run.py --reset          # services + agents, fresh traces.db (Ctrl+C to stop)
.venv/bin/python run.py --seconds 30     # stop automatically after 30 s
.venv/bin/python run.py --services-only  # only the mock services
```

Mock services (ports in `config.yaml`):

| Service | Port | Endpoints |
|---|---|---|
| HR DB | 8101 | `GET /employees/{id}/leave`, `GET /policies/{topic}` |
| Vendor API | 8102 | `GET /quotes?item=&quantity=` |
| Knowledge Base | 8103 | `GET /search?q=`, `POST /admin/rebuild` |

All services: `POST /admin/chaos {"mode": "none|down|slow|error_500|corrupt"}`, `GET /admin/chaos`, `GET /admin/state`.

## Environment variables

| Var | Meaning |
|---|---|
| `MOCK_LLM` | `1` = scripted offline replies (default), `0` = real model |
| `LLM_BASE_URL`, `LLM_API_KEY` | any OpenAI-compatible endpoint |
| `FLEET_MODEL`, `DOCTOR_MODEL`, `BACKUP_MODEL` | model names |
| `AUTO_APPROVE` | `1` = approval-tier actions run without a click |

## Architecture

```
┌──────────────── DASHBOARD (Streamlit) ────────────────┐
│ agent cards 🟢/🔴 · doctor live reasoning · reports    │
└───────────────▲───────────────────────▲───────────────┘
                │                       │
        ┌───────┴───────┐       ┌───────┴────────┐
        │ FLEET (4 AI   │──────▶│ TRACE STORE    │◀── DOCTOR reads
        │ agents)       │ spans │ (SQLite)       │
        └───────┬───────┘       └────────────────┘
                │ calls
        ┌───────┴──────────────────────────┐
        │ SHARED DEPENDENCIES:             │◀── CHAOS injects faults
        │ mock tool APIs (FastAPI),        │◀── DOCTOR applies fixes
        │ LLM gateway, prompt registry,    │
        │ knowledge-base index             │
        └──────────────────────────────────┘
```

## Design decisions (simplest option that keeps the demo working)

- **Extra small modules** not in the original file list: `common/config.py` (loads `config.yaml` + `.env`),
  `common/mock_llm.py` (scripted replies for `MOCK_LLM=1`), `services/chaos.py` (shared fault switch).
- **`control` table** in SQLite holds runtime switches (LLM chaos, route-to-backup, per-agent step cap and
  token budget). Chaos and the doctor run in other processes, so agents read these on every run.
- **"down" fault** returns HTTP 503 immediately instead of killing the process, so it can be switched back instantly.
- **`/admin/state` is never affected by chaos** and reports health the way a real health endpoint would.
  `/admin/chaos` exists only for the demo chaos panel; the doctor must not read it.
- **Knowledge base search** is plain TF-IDF in pure Python with light Arabic normalization
  (diacritics, alef/yaa/taa marbuta forms, the "ال" prefix).
- **Run status is the agent's own view.** A run where every tool call failed but the agent replied
  "could not get quotes" is still `ok`. The doctor has to look at spans and health checks, not trust this.
