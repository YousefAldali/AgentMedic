# REPORT: incident report in English and Arabic (markdown).
# The facts (timeline, symptoms, hypotheses, actions, verification) always come from
# recorded data. In real mode the doctor LLM writes the summary and prevention text;
# in MOCK mode, or if that fails, fixed templates are used.
#
#   python -m doctor.reporter          # print the latest report (English)
#   python -m doctor.reporter --ar     # print it in Arabic
import json
import sys
from datetime import datetime

from common import db, llm
from common.config import mock_llm
from doctor import report_ar

ACTION_AR = {
    "open_circuit_breaker": "فتح قاطع الدائرة",
    "close_circuit_breaker": "إغلاق قاطع الدائرة",
    "enable_fallback": "تفعيل البيانات الاحتياطية المخزنة",
    "disable_fallback": "إيقاف البيانات الاحتياطية",
    "switch_model": "التحويل إلى النموذج الاحتياطي",
    "kill_run": "إيقاف التشغيل العالق",
    "set_step_cap": "تحديد عدد الخطوات والرموز",
    "replay_run": "إعادة تشغيل المهام الفاشلة",
    "rollback_prompt": "استرجاع آخر تعليمات سليمة",
    "rebuild_index": "إعادة بناء فهرس البحث",
}
PREVENTION = {
    "open_circuit_breaker": (
        "Keep a circuit breaker on every external dependency so outages fail fast instead of causing retry storms.",
        "إبقاء قاطع دائرة على كل خدمة خارجية حتى يتوقف الطلب بسرعة عند العطل بدل تكرار المحاولات."),
    "rollback_prompt": (
        "Run the golden health checks before any prompt change goes live, and roll back automatically if they fail.",
        "تشغيل فحوص الصحة المرجعية قبل نشر أي تغيير في التعليمات، والتراجع تلقائيًا عند فشلها."),
    "rebuild_index": (
        "Verify the search index after every rebuild with known queries, and alert on empty results.",
        "التحقق من فهرس البحث بعد كل إعادة بناء باستعلامات معروفة، والتنبيه عند ظهور نتائج فارغة."),
    "switch_model": (
        "Keep a warm backup model and alert on LLM latency per model, not only per agent.",
        "إبقاء نموذج احتياطي جاهز ومراقبة زمن استجابة كل نموذج على حدة."),
    "set_step_cap": (
        "Give every agent a default step cap and token budget, and alert on repeated identical tool calls.",
        "تحديد سقف افتراضي للخطوات والرموز لكل وكيل، والتنبيه عند تكرار نفس الاستدعاء."),
    "replay_run": (
        "Validate upstream output (e.g. PO totals) before downstream agents consume it.",
        "التحقق من مخرجات الوكيل السابق (مثل إجمالي أمر الشراء) قبل أن يستخدمها الوكيل التالي."),
    "enable_fallback": (
        "Keep a warm cache of the last good data and alert when it is older than an agreed limit.",
        "الاحتفاظ بنسخة مخزنة من آخر بيانات سليمة والتنبيه عند تجاوزها العمر المسموح."),
}


def _t(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "-"


def _secs(a: float | None, b: float | None) -> str:
    return f"{b - a:.1f} s" if a and b else "-"


def facts(incident: dict, board: dict, actions: list[dict], verification: list[dict],
          follow_up: list[str], escalation_reason: str = "") -> dict:
    causes = [h for h in board["hypotheses"] if h["confidence"] >= board["threshold"]]
    return {
        "id": incident["id"], "status": incident["status"],
        "first_symptom_at": incident["first_symptom_at"], "opened_at": incident["opened_at"],
        "recovered_at": incident["recovered_at"], "symptoms": json.loads(incident["symptoms"]),
        "hypotheses": board["hypotheses"], "board_source": board["source"], "root_causes": causes,
        "actions": actions, "verification": verification, "follow_up": follow_up,
        "agents": sorted({s["agent"] for s in json.loads(incident["symptoms"])}),
        "escalation_reason": escalation_reason,
    }


def _template_text(f: dict) -> dict:
    causes = ", ".join(h["cause"] for h in f["root_causes"]) or "no confirmed cause"
    acts = ", ".join(f"{a['action']}({a['target']})" for a in f["actions"] if a["status"] == "done") or "none"
    acts_ar = "، ".join(report_ar.action_ar(a) for a in f["actions"] if a["status"] == "done") or "لا شيء"
    agents = ", ".join(f["agents"]) or "-"
    agents_ar = " و".join(report_ar.name(a) for a in f["agents"]) or "-"
    causes_ar = report_ar.causes_sentence([report_ar.cause_ar(h, {}) for h in f["root_causes"]])
    passes = sum(1 for v in f["verification"] if v["ok"])
    ok = f["status"] == "resolved"
    if ok:
        summary_en = (f"Agents affected: {agents}. Root cause: {causes}. AgentMedic applied {acts} "
                      f"and verified recovery with {passes} passing check rounds.")
        summary_ar = (f"تأثر {agents_ar}. {causes_ar} نفّذ AgentMedic الإجراءات التالية: "
                      f"{acts_ar}، ثم تحقّق من تعافي النظام عبر {passes} جولات فحص ناجحة.")
    else:
        summary_en = (f"Agents affected: {agents}. {f['escalation_reason']} Actions taken: {acts}. "
                      "The incident was escalated to a human.")
        summary_ar = (f"تأثر {agents_ar}. {report_ar.escalation_ar(f['escalation_reason'])} "
                      f"الإجراءات المنفذة: {acts_ar}. أُحيلت الحادثة إلى فريق بشري لمراجعتها.")
    prev = [PREVENTION[a["action"]] for a in f["actions"] if a["action"] in PREVENTION and a["status"] == "done"]
    if not ok:
        prev.append(("A human should check the affected systems' real error rates and the agents' tool "
                     "requests, then add a detector for this pattern.",
                     "ينبغي أن يراجع أحد أعضاء الفريق نسب الأخطاء الفعلية في الأنظمة المتأثرة وطلبات أدوات الوكلاء، "
                     "ثم تُضاف قاعدة اكتشاف لهذا النمط."))
    prev = list(dict.fromkeys(prev)) or [("Review the fault and add a detector rule for it.",
                                          "مراجعة العطل وإضافة قاعدة اكتشاف خاصة به.")]
    return {"summary_en": summary_en, "summary_ar": summary_ar,
            "prevention_en": [p[0] for p in prev], "prevention_ar": [p[1] for p in prev]}


def _llm_text(f: dict) -> dict | None:
    prompt = ("Write the summary and prevention recommendations for this incident, based only on the facts "
              "given. Reply with ONLY a JSON object: {\"summary_en\": str, \"summary_ar\": str, "
              "\"prevention_en\": [str], \"prevention_ar\": [str]}. The _ar fields must be in Arabic. "
              "Keep the summary to 2-3 sentences.")
    try:
        resp = llm.chat([{"role": "system", "content": prompt},
                         {"role": "user", "content": json.dumps(f, ensure_ascii=False, default=str)}],
                        agent="doctor", role="doctor")
        text = resp.content or ""
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        if all(data.get(k) for k in ("summary_en", "summary_ar", "prevention_en", "prevention_ar")):
            return data
    except (llm.LLMError, ValueError):
        pass
    return None


def _render(f: dict, text: dict, ar: bool) -> str:
    L = (lambda en, a: a) if ar else (lambda en, a: en)
    status = {"resolved": L("Resolved", "تم الحل"), "escalated": L("Escalated", "تم التصعيد")}.get(f["status"], f["status"])
    lines = [
        f"# {L('Incident', 'حادثة')} #{f['id']}",
        "",
        f"**{L('Status', 'الحالة')}:** {status}  ",
        f"**{L('Time to detect', 'زمن الاكتشاف')}:** {_secs(f['first_symptom_at'], f['opened_at'])} · "
        f"**{L('Time to recover', 'زمن التعافي')}:** {_secs(f['first_symptom_at'], f['recovered_at'])}",
        "",
        f"## {L('Summary', 'الملخص')}",
        text["summary_ar" if ar else "summary_en"],
        "",
        f"## {L('Timeline', 'التسلسل الزمني')}",
        f"- {_t(f['first_symptom_at'])} {L('first symptom recorded', 'أول عرض مسجل')}",
        f"- {_t(f['opened_at'])} {L('incident opened', 'فتح الحادثة')}",
    ]
    for a in f["actions"]:
        lines.append(f"- {_t(a.get('ts'))} {L('action', 'إجراء')}: `{a['action']}` → `{a['target']}` ({a['status']})")
    if f["recovered_at"]:
        lines.append(f"- {_t(f['recovered_at'])} {L('recovery verified', 'تم التحقق من التعافي')}")
    lines += ["", f"## {L('Symptoms', 'الأعراض')}"]
    lines += [f"- {s['agent']}: `{s['type']}`" + (f" ({s['dependency']})" if s["dependency"] else "")
              + f" = {s['value']} — {'; '.join(s['evidence'][:2])}" for s in f["symptoms"]]
    lines += ["", f"## {L('Hypotheses considered', 'الفرضيات التي تم فحصها')} ({L('source', 'المصدر')}: {f['board_source']})", "",
              f"| {L('Cause', 'السبب')} | {L('Confidence', 'الثقة')} | {L('Verdict', 'القرار')} | "
              f"{L('Evidence for', 'أدلة مؤيدة')} | {L('Evidence against', 'أدلة معارضة')} |",
              "|---|---|---|---|---|"]
    causes = {h["cause"] for h in f["root_causes"]}
    for h in f["hypotheses"]:
        if h["cause"] in causes:
            verdict = L("root cause", "سبب جذري")
        elif h["confidence"] >= 0.3:
            verdict = L("inconclusive", "غير حاسمة")
        else:
            verdict = L("rejected", "مرفوضة")
        cell = lambda xs: "<br>".join(x.replace("|", "/") for x in xs) or "-"
        lines.append(f"| {h['cause']} | {h['confidence']:.0%} | {verdict} | {cell(h['evidence_for'])} | "
                     f"{cell(h['evidence_against'])} |")
    lines += ["", f"## {L('Actions taken', 'الإجراءات المنفذة')}", ""]
    if f["actions"]:
        lines += [f"| {L('Action', 'الإجراء')} | {L('Target', 'الهدف')} | {L('Tier', 'المستوى')} | {L('Result', 'النتيجة')} |",
                  "|---|---|---|---|"]
        lines += [f"| `{a['action']}` | {a['target']} | {a['tier']} | {a['status']} |" for a in f["actions"]]
    else:
        lines.append(L("None. No hypothesis was confident enough for a safe action.",
                       "لا شيء. لم تصل أي فرضية إلى درجة ثقة كافية لتنفيذ إجراء آمن."))
    lines += ["", f"## {L('Verification', 'التحقق')}"]
    for v in f["verification"]:
        mark = L("pass", "ناجح") if v["ok"] else L("fail", "فاشل")
        detail = "; ".join(v["probes"].values())
        if v["live_symptoms"]:
            detail += f" | {L('live symptoms', 'أعراض حية')}: {'; '.join(v['live_symptoms'])}"
        lines.append(f"- {L('Round', 'جولة')} {v['round']}: **{mark}** · {detail}")
    if f["follow_up"]:
        lines += ["", f"## {L('Current state and follow-up', 'الوضع الحالي والمتابعة')}"]
        lines += [f"- {x[0] if isinstance(x, (list, tuple)) else x}" for x in f["follow_up"]]
    lines += ["", f"## {L('Prevention', 'الوقاية')}"]
    lines += [f"- {x}" for x in text["prevention_ar" if ar else "prevention_en"]]
    return "\n".join(lines) + "\n"


def _translate_free_text(f: dict) -> dict:
    # Real-model mode: the doctor may write hypotheses and evidence in its own words, which the
    # rule-based Arabic patterns can't cover. Translate just those strings with the model.
    pending = []
    for h in f["hypotheses"]:
        if not report_ar._match(h["cause"].replace(" (direct proof from the rules)", ""), report_ar.CAUSE_PATTERNS):
            pending.append(h["cause"])
        pending += [e for e in h["evidence_for"] + h["evidence_against"] if report_ar.evidence_ar(e, {}) is None]
    pending = list(dict.fromkeys(pending))[:60]
    if not pending:
        return {}
    prompt = ("Translate each English string to clear Modern Standard Arabic for an incident report. Keep "
              "numbers, percentages and technical identifiers (like vendor_api, HTTP 503) as they are. Reply with "
              "ONLY a JSON object mapping each original string to its Arabic translation.")
    try:
        resp = llm.chat([{"role": "system", "content": prompt},
                         {"role": "user", "content": json.dumps(pending, ensure_ascii=False)}],
                        agent="doctor", role="doctor")
        text = resp.content or ""
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except (llm.LLMError, ValueError):
        return {}


def write_reports(f: dict) -> tuple[str, str]:
    text = None if mock_llm() else _llm_text(f)
    text = text or _template_text(f)
    translated = {} if mock_llm() else _translate_free_text(f)
    return _render(f, text, ar=False), report_ar.render(f, text, translated)


if __name__ == "__main__":
    col = "report_md_ar" if "--ar" in sys.argv else "report_md"
    rows = db.query(f"SELECT {col} AS r FROM incidents WHERE {col} IS NOT NULL ORDER BY id DESC LIMIT 1")
    print(rows[0]["r"] if rows else "No incident reports yet.")
