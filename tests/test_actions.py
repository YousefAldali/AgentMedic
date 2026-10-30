# Actions and safety tiers: what the doctor may do, may never do, and how it undoes things.
import pytest

from common import db
from doctor import actions


@pytest.mark.parametrize("name", ["delete_records", "drop_table", "truncate_logs", "rotate_credentials",
                                  "wipe_cache", "edit_record"])
def test_destructive_actions_are_refused_and_logged(name):
    out = actions.execute(name, "vendor_api", incident_id=1)
    assert out["status"] == "refused"
    log = db.query("SELECT action, tier FROM actions_log")
    assert log == [{"action": name, "tier": "forbidden"}]


def test_unknown_action_is_not_run():
    assert actions.execute("restart_everything", "vendor_api", 1)["status"] == "unavailable"


def test_unknown_target_is_not_run():
    out = actions.execute("open_circuit_breaker", "vendor_api.get_quotes", 1)
    assert out["status"] == "unavailable"
    assert db.get_control("circuit.vendor_api.get_quotes") is None


def test_auto_action_runs_and_can_be_undone():
    out = actions.execute("open_circuit_breaker", "vendor_api", 1)
    assert out["status"] == "done"
    assert db.get_control("circuit.vendor_api") == "open"
    actions.undo("open_circuit_breaker", "vendor_api", out["saved"], 1)
    assert db.get_control("circuit.vendor_api") == "closed"


def test_fallback_undo_restores_previous_value():
    out = actions.execute("enable_fallback", "vendor_api", 1)
    assert db.get_control("fallback.vendor_api") is True
    actions.undo("enable_fallback", "vendor_api", out["saved"], 1)
    assert db.get_control("fallback.vendor_api") is False


@pytest.fixture
def approval_action(monkeypatch):
    test_action = actions.Action("rebuild_test_index", actions.APPROVAL, "test only",
                                 lambda t: actions._swap(f"rebuilt.{t}", True), lambda t, s: None)
    monkeypatch.setitem(actions.REGISTRY, test_action.name, test_action)
    return test_action.name


def test_approval_action_waits_for_a_human(approval_action):
    out = actions.execute(approval_action, "knowledge_base", 1)
    assert out["status"] == "pending"
    assert db.get_control("rebuilt.knowledge_base") is None


def test_approval_action_runs_when_approved(approval_action):
    out = actions.execute(approval_action, "knowledge_base", 1, approved_by="demo user")
    assert out["status"] == "done"
    assert db.get_control("rebuilt.knowledge_base") is True


def test_auto_approve_setting_runs_approval_actions(approval_action, monkeypatch):
    monkeypatch.setenv("AUTO_APPROVE", "1")
    out = actions.execute(approval_action, "knowledge_base", 1)
    assert out["status"] == "done"
    assert out["approved_by"] == "auto-approve"
