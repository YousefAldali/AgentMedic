# Benchmark: AgentMedic vs naive retry + restart

Mode: MOCK (scripted model) · scenarios: 11  
Naive retry + restart results are from the full run on 2026-10-31 17:12-18:01 (its code did not change); AgentMedic was rerun after a fix that keeps root causes from every diagnosis round on the record.

| Remediator | Recovery rate | Mean time to recover | Root causes found | Wrong / unnecessary actions | Correct escalations (F10) |
|---|---|---|---|---|---|
| AgentMedic | 100% | 34.5 s | 100% | 0 | 1/1 |
| Naive retry + restart | 0% | - | 0% | 102 | 0/1 |

| Scenario | Faults | Remediator | Recovered | Time (s) | Expected | Found | Actions | Wrong |
|---|---|---|---|---|---|---|---|---|
| F1 | F1 | AgentMedic | yes | 29.3 | vendor_api | vendor_api | 2 | 0 |
| F2 | F2 | AgentMedic | yes | 28.9 | prompt:hr | prompt:hr | 1 | 0 |
| F3 | F3 | AgentMedic | yes | 21.8 | knowledge_base | knowledge_base | 1 | 0 |
| F4 | F4 | AgentMedic | yes | 54.7 | llm | llm | 1 | 0 |
| F5 | F5 | AgentMedic | yes | 46.4 | loop:procurement | loop:procurement | 2 | 0 |
| F6 | F6 | AgentMedic | yes | 13.9 | prompt:procurement | prompt:procurement | 3 | 0 |
| F10 | F10 | AgentMedic | escalated | - | escalate | escalated | 0 | 0 |
| S7 | F1 + F2 | AgentMedic | yes | 24.9 | prompt:hr, vendor_api | prompt:hr, vendor_api | 3 | 0 |
| S8 | F4 + F3 | AgentMedic | yes | 51.1 | knowledge_base, llm | knowledge_base, llm | 2 | 0 |
| S9 | F6 + F5 | AgentMedic | yes | 51.8 | loop:procurement, prompt:procurement | loop:procurement, prompt:procurement | 5 | 0 |
| S10 | F1 + F2 + F3 | AgentMedic | yes | 22.5 | knowledge_base, prompt:hr, vendor_api | knowledge_base, prompt:hr, vendor_api | 4 | 0 |
| F1 | F1 | Naive retry + restart | no | - | vendor_api | - | 16 | 16 |
| F2 | F2 | Naive retry + restart | no | - | prompt:hr | - | 0 | 0 |
| F3 | F3 | Naive retry + restart | no | - | knowledge_base | - | 0 | 0 |
| F4 | F4 | Naive retry + restart | no | - | llm | - | 29 | 29 |
| F5 | F5 | Naive retry + restart | no | - | loop:procurement | - | 0 | 0 |
| F6 | F6 | Naive retry + restart | no | - | prompt:procurement | - | 0 | 0 |
| F10 | F10 | Naive retry + restart | no | - | escalate | - | 8 | 8 |
| S7 | F1 + F2 | Naive retry + restart | no | - | prompt:hr, vendor_api | - | 16 | 16 |
| S8 | F4 + F3 | Naive retry + restart | no | - | knowledge_base, llm | - | 19 | 19 |
| S9 | F6 + F5 | Naive retry + restart | no | - | loop:procurement, prompt:procurement | - | 0 | 0 |
| S10 | F1 + F2 + F3 | Naive retry + restart | no | - | knowledge_base, prompt:hr, vendor_api | - | 14 | 14 |
