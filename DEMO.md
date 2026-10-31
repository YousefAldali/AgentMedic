# AgentMedic: demo guide and fallback plan

## Before going on stage (5 minutes)

1. Start everything fresh:
   ```bash
   cd fleet-doctor
   .venv/bin/python run.py --reset                 # MOCK mode (free, offline, same result every time)
   MOCK_LLM=0 .venv/bin/python run.py --reset      # or: real models
   ```
2. Run the checklist: `.venv/bin/python demo.py check` (add `--api` in real-model mode to test the key).
3. Open **http://localhost:8501**, zoom the browser to about 90%, and wait until all 4 agents are green.
4. Keep `http://localhost:8501/?lang=ar` ready in a second tab (opens the Arabic report).
5. Have the slides open on the benchmark chart (`benchmark/results/benchmark.png`) and the architecture
   diagram (`docs/architecture.png`).

## The 3-minute demo

| Time | Do | Say |
|---|---|---|
| 0:00 | Slide: the problem | "AI agents fail quietly: a loop that cost $47K, a support bot that invented a policy, an agent that deleted a production database. Monitoring asks if the process is alive. AgentMedic asks if the agent is doing the right thing, and fixes it." |
| 0:30 | Dashboard, all green | "Four enterprise agents - HR, procurement, finance, support - working in Arabic and English. The doctor sits in its office and makes rounds." |
| 0:45 | Sidebar: **Run demo: S7** | "Two unrelated failures at once: the supplier system goes down, and a bad prompt update goes live on HR." |
| 1:00 | Watch the office | "Procurement fails loudly. HR fails silently - no errors, just wrong answers - and only the golden health checks catch it. The doctor walks over." |
| 1:20 | Doctor panel + hypothesis board | "It doesn't jump at the first idea. Two independent root causes, each with evidence. It rejected 'the AI model is broken' because the other agents on that model are healthy." |
| 1:45 | **Approve** | "Simple fixes it does alone: circuit breaker and backup prices. Rolling back a prompt is risky, so it waits for a human. I approve." |
| 2:05 | All green, timeline | "Now it proves the fix with the golden checks - three rounds in a row - instead of trusting the agents. Time to detect, time to recover." |
| 2:25 | Reports, العربية tab | "Every incident ends with a report in English and Arabic." |
| 2:40 | Slide: benchmark | "Against the usual retry-and-restart approach on 11 scenarios: …" (numbers from `benchmark/results/benchmark.md`) |
| 2:55 | Close | "AgentMedic is the reliability layer for enterprise agent platforms like HUMAIN ONE." |

Between runs: **Reset all** in the sidebar, or `.venv/bin/python demo.py reset`.

## Fallback plan

| If this happens | Do this |
|---|---|
| The model API is slow, rate-limited or down | Stop (`Ctrl+C`) and restart in MOCK mode: `.venv/bin/python run.py --reset`. Same screens, same flow, scripted model. Say "this is the offline mode we use for testing." |
| No internet at all | MOCK mode needs no internet. Everything runs on the laptop. |
| The doctor stands still while agents are red | It is either waiting for **Approve** ("Waiting for your approval" tag; the button is in the doctor panel), or it handed a problem to a human ("Handed to a human" tag). Click **Reset all**, then run the scenario again. |
| The screen looks mixed up (several scenarios at once) | **Reset all**, wait 10 seconds until everything is green, then **Run demo: S7** once. Don't click scenarios while the doctor is working. |
| The dashboard is frozen or blank | Refresh the browser tab. If that fails, `Ctrl+C` and `.venv/bin/python run.py --reset`. |
| A port is already in use when starting | An old run is still alive: `pkill -f run.py; pkill -f uvicorn; pkill -f "streamlit run"`, then start again. |
| Everything fails | Show the recorded demo video and the benchmark slide. |

## Timing on stage

| Mode | S7 from click to all green |
|---|---|
| MOCK | about 25-45 s (plus the time you take to click Approve) |
| Real models | about 1-2 minutes; the doctor's reasoning is written by the model |
