# AgentMedic

An AI agent that monitors a fleet of enterprise AI agents, detects when they fail,
diagnoses the root cause (including several faults at once), fixes them safely,
verifies recovery, and writes an incident report in English and Arabic.

> Status: **Phase 5 (dashboard)** — live office view, fleet cards, doctor feed, hypothesis board, approval buttons, chaos panel and EN/AR reports, all in the browser.

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

## Dashboard

`run.py` opens it at **http://localhost:8501**.

- **Live office:** every agent at a desk (🟢 / 🟡 / 🔴, health bar), request envelopes flying in, the shared
  systems at the bottom with lines to the agents that use them (red and moving when a system fails), and the
  doctor walking to whatever it is examining, with its current reasoning in a speech bubble.
- **Fleet cards:** status, health score, error rate, p95 latency, tokens/min, prompt version, model.
- **Doctor live feed** and the **hypothesis board** with confidence bars.
- **Approval panel:** Approve / Reject buttons for approval-tier fixes.
- **Chaos panel** (sidebar): one-click demo (S7), any scenario, Reset all.
- **Incident reports** with English and Arabic (right-to-left) tabs, time to detect and time to recover.

Demo in 3 minutes: start `run.py`, open the dashboard, click **▶ Demo: S7**, watch Procurement and HR turn red
and the doctor walk over, click **Approve** for the prompt rollback, watch everything turn green, open the report.

## Try the doctor (fault F1: Vendor API down)

In a second terminal while `run.py` is running:

```bash
.venv/bin/python -m chaos.faults inject F1   # break the Vendor API
# watch the [doctor] lines: DETECT -> CORRELATE -> DIAGNOSE -> TREAT -> VERIFY -> REPORT
.venv/bin/python -m doctor.reporter          # read the incident report (add --ar for Arabic)
.venv/bin/python -m chaos.faults reset F1    # fix the Vendor API; the doctor closes the breaker (RESTORE)
```

## Faults and scenarios

| Fault | What breaks | What the doctor should see | Correct fix |
|---|---|---|---|
| F1 | Vendor API down | tool errors, retry storm in Procurement | circuit breaker + cached quotes (auto) |
| F2 | bad HR prompt deployed | no errors, HR golden checks fail | roll back to the known-good prompt (approval) |
| F3 | knowledge base index corrupted | empty/irrelevant search results for Support and HR | rebuild the index (approval) |
| F4 | primary LLM slow | latency up on every agent | switch to the backup model (auto) |
| F5 | a vendor quote job never finishes | same tool call repeated, token spike | kill the run + step/token cap (auto) |
| F6 | bad procurement prompt writes wrong PO totals | Finance fails on Procurement's output | roll back Procurement's prompt, then replay both (approval + auto) |
| F10 | partial outage + misbehaving tool, health check says "up" | mixed 404/503 errors | escalate to a human, no action |

Scenarios: every fault alone, plus **S7** = F1+F2, **S8** = F4+F3, **S9** = F6+F5, **S10** = F1+F2+F3.

```bash
.venv/bin/python -m chaos.scenarios list
.venv/bin/python -m chaos.scenarios inject S7     # while run.py is running
.venv/bin/python -m doctor.approvals              # list actions waiting for approval
.venv/bin/python -m doctor.approvals approve 1    # approve one (the dashboard will have a button)
.venv/bin/python -m chaos.faults reset-all        # back to healthy
.venv/bin/python -m chaos.scenarios check         # run every scenario unattended and score the doctor
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

## Tests

```bash
.venv/bin/python -m pytest -q     # 144 tests, offline, about 5 seconds
```

| File | What it checks |
|---|---|
| `tests/test_detectors.py` | when a symptom must fire and when it must stay quiet |
| `tests/test_correlator.py` | grouping symptoms by service and counting 4xx vs 5xx errors |
| `tests/test_diagnoser.py` | the hypothesis board, the 55% safety rule (any wording, loose target names), LLM retry and fallback |
| `tests/test_actions.py` | safety tiers: destructive actions refused, approvals wait, every action can be undone |
| `tests/test_doctor.py` | fix vs escalate, no fix -> escalate at once, cooldown, the 5-request check before closing a breaker |
| `tests/test_faults.py` | the F10 fault itself, and the agent's circuit breaker and cache |
| `tests/test_phase4.py` | loop, token, empty-result, health-drop and cascade detectors; multi-fault diagnosis; new actions; approvals |
| `tests/test_dashboard.py` | the dashboard renders, shows the board and reports, Approve/Reject buttons work |
| `tests/test_golden.py` | golden answer checks, health scores, the finance agent and the procurement → finance hand-off |

## The fleet

| Agent | Job | Uses |
|---|---|---|
| HR | leave balances and HR policies | HR DB, Knowledge Base, LLM |
| Procurement | compares vendor quotes, drafts a purchase order | Vendor API, LLM |
| Finance | validates invoices against Procurement's purchase orders | Procurement's output, LLM |
| Support | answers customer questions from the knowledge base | Knowledge Base, LLM |

## Golden health checks

Each agent has 3-4 questions with known correct answers in `fleet/golden/<agent>.json` (English and Arabic).
They catch silent failures: nothing errors, but the answers are wrong. Score = fraction passed.
They run every 60 s (`[health]` lines in the log) and after every fix: an agent counts as recovered only
with a score of 1.0 for several rounds in a row.

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
- **Purchase order store:** Procurement saves every PO it drafts (malformed ones marked invalid) in
  `purchase_orders`; Finance reads them. This is the cascade path for fault F6 later.
- **Golden checks use simple matchers** (regex, keywords, JSON fields), no LLM judge: free, fast, and the same
  every run. A shut-down mid-check saves nothing, so it never shows up as a fake low score.
- **Knowledge base search returns the best-matching paragraph first, then the rest of the short document**,
  so answers are not cut off at the wrong paragraph.
- **Workload:** every agent gets one new task every 6 s (staggered), so each agent's traffic stays the same
  as more agents are added.
- **On a new incident the doctor health-checks every agent at once** (waiting at most 20 s), so problems that
  only show up in answers (a bad prompt) land on the same hypothesis board as the ones that show up as errors.
- **Fix order:** automatic fixes first (contain the damage), then approval-tier fixes (the doctor waits for a
  human, up to 5 minutes), then replays of failed work once the cause is fixed.
- **F5 (runaway loop)** is a quote job that never finishes for one item (printers): the agent keeps asking.
  The doctor kills the running loops and caps steps and tokens; a human still has to fix the vendor job.
- **F6 (cascade)** is a bad procurement prompt that writes wrong PO totals. Invoices bill the real amount, so
  Finance can't match them. Replay regenerates the bad POs under the same PO ids, then re-runs Finance.
- **Bad prompts are deployed as new prompt versions** (`data/bad_prompts.json`), so the doctor sees "v2 went
  live N s before the first symptom" and can roll back to the last known-good version.
- **Scenario checker** (`chaos/scenarios.py check`) approves approval-tier actions automatically and counts a
  scenario as passed when every incident is resolved and the root causes the doctor acted on match the ground
  truth exactly (F10: escalated with no action).
- **Every fix needs matching evidence** (checked in code after every diagnosis): step caps and kills only with
  a detected loop or token spike, prompt rollback only if the prompt really changed, model switch only with LLM
  symptoms, circuit breaker only if the health endpoint reports a failure, index rebuild only if the index
  check failed, replays only next to a real fix or after a cascade. Dropped fixes are listed on the board.
- **Proof floor:** when the rules have direct proof of a cause (health endpoint down, the same call repeated
  5+ times, a new prompt plus failing golden checks, every agent slow), the LLM cannot talk it below the action
  threshold; the rule's hypothesis is added to the board. The 55% safety caps still apply afterwards.
- **Backup data is only good data:** empty search results, pending answers or records with missing values
  are never cached. After a repair, the doctor tests the service with real requests and returns to live data
  before verifying.
- **Real models are well behaved:** for F5 the stuck quote answers "retry required", so a real model keeps
  calling (Haiku: 11 times); with a plain "still pending" it sensibly gave up after 2-3 tries.
- **The live office animates in the browser** from a small JSON feed (`dashboard/status_api.py`, port 8104),
  so the animation never restarts when the rest of the dashboard refreshes. Where the doctor walks comes from
  its latest real event: the first agent or system named in it.
