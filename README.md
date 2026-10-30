# AgentMedic

An AI agent that monitors a fleet of enterprise AI agents, detects when they fail,
diagnoses the root cause (including several faults at once), fixes them safely,
verifies recovery, and writes an incident report in English and Arabic.

> Status: **Phase 2 (first vertical slice)** — the doctor detects, diagnoses, fixes and verifies a Vendor API outage (F1) on its own and writes an EN/AR report.

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
.venv/bin/python run.py --no-doctor      # fleet without the doctor
```

## Try the doctor (fault F1: Vendor API down)

In a second terminal while `run.py` is running:

```bash
.venv/bin/python -m chaos.faults inject F1   # break the Vendor API
# watch the [doctor] lines: DETECT -> CORRELATE -> DIAGNOSE -> TREAT -> VERIFY -> REPORT
.venv/bin/python -m doctor.reporter          # read the incident report (add --ar for Arabic)
.venv/bin/python -m chaos.faults reset F1    # fix the Vendor API; the doctor closes the breaker (RESTORE)
```

## Try escalation (fault F10: ambiguous evidence)

```bash
.venv/bin/python -m chaos.faults inject F10  # partial vendor outage + a tool sending garbled requests
# the health endpoint still says "up", errors are a mix of 503 and 404:
# no hypothesis reaches 60%, so the doctor takes no action and ESCALATEs to a human
.venv/bin/python -m chaos.faults reset F10
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
- **Rules detect, the LLM diagnoses.** Detection is plain rules on the traces. Diagnosis uses the doctor LLM
  (strict JSON, validated, one retry); in MOCK mode or if the LLM fails it uses a rule-based board.
- **Circuit breaker + cache.** Every tool goes through `call_dependency`, which caches the last good
  response. The workload warms the quote cache at startup so a fallback always has data.
- **Breaker closes by itself.** When a dependency with an open breaker reports healthy again,
  the doctor closes the breaker and turns the fallback off (logged as RESTORE).
- **Verification probes** (one fixed task per agent, checked against its trace and expected answer)
  stand in for the golden health checks until Phase 3.
- **`run.py` clears leftover faults and breakers** at startup so every run begins healthy.
- **Escalation instead of guessing.** Without confirmation from a health endpoint, tool errors alone can
  never push an outage hypothesis to the 60% action threshold. Mixed 4xx/5xx errors add a competing
  "bad requests" hypothesis. After escalating, the same problem does not reopen an incident for 120 s.
- **Safety rules in code after every diagnosis** (LLM or rules): a hypothesis whose fix touches a service is
  capped at 55% when that service's health endpoint says it is healthy or its errors mix 4xx and 5xx.
  The cap is keyed on the fix target, not on the wording. If the root cause has no allowed action,
  the doctor escalates at once.
- **Half-open check before closing a breaker:** 5 real requests to the service must all succeed;
  the health endpoint alone is not trusted.
- **F10 is deterministic:** the flaky vendor fails every 2nd request and the tool garbles every 3rd call,
  and the vendor API rejects unknown items before any backend fault, so both error types always appear.
