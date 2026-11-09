# The dashboard shows plain words instead of the doctor's technical ids.
from dashboard import names


def test_plain_swaps_ids_inside_sentences():
    assert names.plain("procurement: repeated_call_loop (5, threshold 5)") == \
        "Purchasing: stuck repeating the same step (5, threshold 5)"
    assert names.plain("vendor_api outage (status=down)") == "Supplier system outage (status=down)"
    assert names.plain("4/4 get_quotes calls failed") == "4/4 price lookup calls failed"


def test_plain_leaves_other_words_alone():
    assert names.plain("health checks passed") == "health checks passed"
    assert names.plain("hrs and supporting") == "hrs and supporting"


def test_fix_reads_like_an_action():
    assert names.fix("open_circuit_breaker(vendor_api)") == "Pause calls to the failing system (Supplier system)"
    assert names.fix("rollback_prompt(hr)") == "Undo the latest instructions update (HR)"


def test_causes_sentence_joins_with_and():
    assert names.causes_sentence(["vendor_api outage (status=down)", "bad prompt deployed for hr"]) == \
        "the supplier system outage and the bad instructions update for HR"
    assert names.causes_sentence(["prompt:hr"]) == "a bad update to the HR agent's instructions"
    assert names.causes_sentence([]) == ""


def test_report_keeps_code_marks_balanced():
    out = names.report("- action: `rollback_prompt` → `hr` (done)\n- `tool_error_rate` on `vendor_api`")
    assert all(line.count("`") % 2 == 0 for line in out.splitlines())
    assert "undo instructions update" in out and "a system it uses keeps failing" in out
