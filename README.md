# AgentMedic

**An AI agent that keeps other AI agents running.**

Companies now use AI agents for everyday work like HR requests, purchasing and finance. These agents often fail
without any error: a request gets lost, a record is saved with the wrong numbers, a task gets stuck, or an agent
retries the same thing for hours. Normal monitoring only checks that the system is on, not that the agent is doing
the right thing.

AgentMedic watches a fleet of AI agents and the systems they depend on. When something goes wrong, it finds the
cause, fixes it safely, proves the fix worked, and writes a short incident report in English and Arabic.

Built for the HUMAIN Future AI Builders Hackathon (Challenge 1: agentic recovery of a failing system).

## How it works

1. **Watch and detect.** AgentMedic records every step each agent takes and regularly asks each agent questions it
   already knows the answers to. Simple rules flag errors, slowness, loops, wrong answers and bad data passed
   between agents.
2. **Diagnose and fix safely.** It lists the possible causes, weighs the evidence for and against each one, and can
   find several causes at once. Safe fixes run automatically, risky fixes wait for a person to approve, and deleting
   or changing data is never allowed. If it isn't sure, it hands the problem to a person instead of guessing.
3. **Verify and report.** It asks the known questions again until everything passes several times in a row, then
   writes an incident report in English and Arabic.

## What makes it different

- Fixes problems on a live system instead of only analysing them afterwards.
- Handles several failures at the same time and explains why it rejected the other causes.
- Catches silent failures, where nothing errors but the answers are wrong.
- Fixes the root cause (for example, undoing a bad update) instead of just restarting.
- Never deletes data, asks before risky fixes, and escalates when the evidence is unclear.

## Results

Tested on 11 failure scenarios, including 2 and 3 failures at once, against classic "retry and restart" monitoring:

| | AgentMedic | Retry and restart |
|---|---|---|
| Recovered | 100% | 0% |
| Root causes found | 100% | 0% |
| Wrong actions | 0 | 102 |
| Average time to recover | 35 s | never |

Details: [benchmark/results/benchmark.md](benchmark/results/benchmark.md)

## Quick start

```bash
cd fleet-doctor
uv venv .venv
uv pip install -p .venv -r requirements.txt
cp .env.example .env              # works offline by default (MOCK_LLM=1)
.venv/bin/python run.py --reset   # starts the agents, the doctor and the dashboard
```

Open http://localhost:8501 and click **Run demo: S7** in the sidebar. To use a real model, set `MOCK_LLM=0` and
fill in the model settings in `.env` (any OpenAI-compatible endpoint works).

Run the tests with `.venv/bin/python -m pytest -q`.

## Project structure

```
fleet/       the AI agents (HR, procurement, finance, support) and their health checks
doctor/      AgentMedic: detect, diagnose, fix, verify, report
services/    the systems the agents depend on, with switches to break them on purpose
chaos/       failure scenarios for testing
dashboard/   the live dashboard
benchmark/   comparison with retry-and-restart monitoring
tests/       automated tests
```

More: [demo guide](DEMO.md) · [design decisions](docs/DESIGN.md)
