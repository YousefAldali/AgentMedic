# AgentMedic: design decisions

The simplest option that keeps the demo reliable, chosen at each step.

- **Extra small modules** not in the original file list: `common/config.py` (loads `config.yaml` + `.env`),
  `services/chaos.py` (shared fault switch). The tests replace the AI model with scripted replies (`tests/fake_llm.py`).
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
  (strict JSON, validated, one retry); if the LLM fails it uses a rule-based board.
- **Circuit breaker + cache.** Every tool goes through `call_dependency`, which caches the last good
  response. The workload warms the quote cache at startup so a fallback always has data.
- **Breaker closes by itself.** When a dependency with an open breaker reports healthy again,
  the doctor closes the breaker and turns the fallback off (logged as RESTORE).
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
  `purchase_orders`; Finance reads them. This is the cascade path for fault F6.
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
  calling (11 times in our test); with a plain "still pending" it sensibly gave up after 2-3 tries.
- **The live office animates in the browser** from a small JSON feed (`dashboard/status_api.py`, port 8104),
  so the animation never restarts when the rest of the dashboard refreshes. Where the doctor walks comes from
  its latest real event: the first agent or system named in it.
- **The dashboard's plain names are hand-written tables** (`dashboard/names.py`: agents, systems, symptoms, fixes).
  A new agent or system works everywhere else from `config.yaml` alone, but shows up on the dashboard under its
  technical id until a plain name is added there.
