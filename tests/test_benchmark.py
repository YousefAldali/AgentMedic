# Benchmark scoring, summary and outputs (no services needed).
import json

from benchmark import chart, run_benchmark as rb
from benchmark.naive_agent import NaiveRemediator


def row(scenario, remediator, recovered, t=None, expected="vendor_api", found="vendor_api", correct=1, total=1,
        actions=2, wrong=0, escalated=False):
    return rb.Row(scenario, "F1", remediator, recovered, t, escalated, expected, found, correct, total, actions,
                  wrong, 0, True)


def test_action_to_cause_mapping():
    assert rb.action_cause("open_circuit_breaker", "vendor_api") == "vendor_api"
    assert rb.action_cause("rollback_prompt", "hr") == "prompt:hr"
    assert rb.action_cause("set_step_cap", "procurement") == "loop:procurement"
    assert rb.action_cause("switch_model", "llm") == "llm"
    assert rb.action_cause("replay_run", "finance") is None


def test_doctor_actions_on_the_right_cause_are_not_wrong():
    acts = [{"action": "open_circuit_breaker", "target": "vendor_api", "result": '{"status": "done"}'},
            {"action": "replay_run", "target": "procurement", "result": '{"status": "done"}'},
            {"action": "close_circuit_breaker", "target": "vendor_api", "result": '{"status": "done"}'}]
    assert rb.score_actions(acts, {"vendor_api"}, naive=False) == (2, 0)


def test_doctor_action_on_another_cause_is_wrong():
    acts = [{"action": "rebuild_index", "target": "knowledge_base", "result": '{"status": "done"}'}]
    assert rb.score_actions(acts, {"vendor_api"}, naive=False) == (1, 1)


def test_every_naive_action_counts_as_unnecessary():
    acts = [{"action": "kill_run", "target": "procurement", "result": '{"status": "done"}'},
            {"action": "replay_run", "target": "procurement", "result": '{"status": "done"}'}]
    assert rb.score_actions(acts, {"vendor_api"}, naive=True) == (2, 2)


def test_summary_numbers():
    rows = [row("F1", "AgentMedic", True, 20.0), row("F2", "AgentMedic", True, 40.0),
            row("F10", "AgentMedic", False, expected="escalate", found="escalated", correct=0, total=0, actions=0,
                escalated=True),
            row("F1", "Naive retry + restart", False, found="-", correct=0, actions=4, wrong=4),
            row("F2", "Naive retry + restart", False, found="-", correct=0, actions=0, wrong=0)]
    s = rb.summary(rows)
    assert s["AgentMedic"]["recovery_rate"] == 1.0 and s["AgentMedic"]["mean_recover_s"] == 30.0
    assert s["AgentMedic"]["root_cause_accuracy"] == 1.0 and s["AgentMedic"]["correct_escalations"] == "1/1"
    assert s["Naive retry + restart"]["recovery_rate"] == 0.0
    assert s["Naive retry + restart"]["mean_recover_s"] is None
    assert s["Naive retry + restart"]["wrong_actions"] == 4


def test_outputs_and_chart_are_written(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, "RESULTS", tmp_path)
    monkeypatch.setattr(chart, "OUT", tmp_path)
    rows = [row("F1", "AgentMedic", True, 20.0), row("F1", "Naive retry + restart", False, found="-", correct=0)]
    rb.write_outputs(rows)
    assert (tmp_path / "benchmark.csv").read_text().count("\n") == 3
    assert json.loads((tmp_path / "benchmark.json").read_text())["summary"]["AgentMedic"]["recovery_rate"] == 1.0
    assert "| AgentMedic | 100% |" in (tmp_path / "benchmark.md").read_text()
    png = chart.draw(rows)
    assert (tmp_path / "benchmark.png").stat().st_size > 10000 and png.endswith("benchmark.png")


def test_naive_only_sees_classic_symptoms_and_backs_off(monkeypatch):
    calls = []
    monkeypatch.setattr("benchmark.naive_agent.actions.execute", lambda a, t, i, approved_by=None: calls.append((a, t)))
    naive = NaiveRemediator(cooldown_s=60)
    monkeypatch.setattr(naive, "failing_agents", lambda: ["procurement"])
    naive.tick()
    naive.tick()
    assert calls == [("kill_run", "procurement"), ("replay_run", "procurement")]
