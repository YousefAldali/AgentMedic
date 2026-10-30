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

ACTION_AR = {
    "open_circuit_breaker": "فتح قاطع الدائرة",
    "close_circuit_breaker": "إغلاق قاطع الدائرة",
    "enable_fallback": "تفعيل البيانات الاحتياطية المخزنة",
    "disable_fallback": "إيقاف البيانات الاحتياطية",
}
PREVENTION = {
    "open_circuit_breaker": (
        "Keep a circuit breaker on every external dependency so outages fail fast instead of causing retry storms.",
        "إبقاء قاطع دائرة على كل خدمة خارجية حتى يتوقف الطلب بسرعة عند العطل بدل تكرار المحاولات."),
    "enable_fallback": (
        "Keep a warm cache of the last good data and alert when it is older than an agreed limit.",
        "الاحتفاظ بنسخة مخزنة من آخر بيانات سليمة والتنبيه عند تجاوزها العمر المسموح."),
}


def _t(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "-"


def _secs(a: float | None, b: float | None) -> str:
    return f"{b - a:.1f} s" if a and b else "-"


def facts(incident: dict, board: dict, actions: list[dict], verification: list[dict],
          follow_up: list[str]) -> dict:
    causes = [h for h in board["hypotheses"] if h["confidence"] >= board["threshold"]]
    return {
        "id": incident["id"], "status": incident["status"],
        "first_symptom_at": incident["first_symptom_at"], "opened_at": incident["opened_at"],
        "recovered_at": incident["recovered_at"], "symptoms": json.loads(incident["symptoms"]),
        "hypotheses": board["hypotheses"], "board_source": board["source"], "root_causes": causes,
        "actions": actions, "verification": verification, "follow_up": follow_up,
        "agents": sorted({a for h in causes for a in h["affected_agents"]}),
    }


def _template_text(f: dict) -> dict:
    causes = ", ".join(h["cause"] for h in f["root_causes"]) or "no confirmed cause"
    acts = ", ".join(f"{a['action']}({a['target']})" for a in f["actions"] if a["status"] == "done") or "none"
    acts_ar = "، ".join(f"{ACTION_AR.get(a['action'], a['action'])} ({a['target']})"
                       for a in f["actions"] if a["status"] == "done") or "لا شيء"
    agents = ", ".join(f["agents"]) or "-"
    passes = sum(1 for v in f["verification"] if v["ok"])
    ok = f["status"] == "resolved"
    summary_en = (f"Agents affected: {agents}. Root cause: {causes}. AgentMedic applied {acts}"
                  + (f" and verified recovery with {passes} passing check rounds." if ok
                     else ". Recovery could not be verified, so the incident was escalated to a human."))
    summary_ar = (f"الوكلاء المتأثرون: {agents}. السبب الجذري: {causes}. نفّذ AgentMedic: {acts_ar}"
                  + (f"، وتحقق من التعافي عبر {passes} جولات فحص ناجحة." if ok
                     else "، ولم يتمكن من التحقق من التعافي فتم تصعيد الحادثة إلى فريق بشري."))
    prev = [PREVENTION[a["action"]] for a in f["actions"] if a["action"] in PREVENTION and a["status"] == "done"]
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
        verdict = L("root cause", "سبب جذري") if h["cause"] in causes else L("rejected", "مرفوضة")
        cell = lambda xs: "<br>".join(x.replace("|", "/") for x in xs) or "-"
        lines.append(f"| {h['cause']} | {h['confidence']:.0%} | {verdict} | {cell(h['evidence_for'])} | "
                     f"{cell(h['evidence_against'])} |")
    lines += ["", f"## {L('Actions taken', 'الإجراءات المنفذة')}", "",
              f"| {L('Action', 'الإجراء')} | {L('Target', 'الهدف')} | {L('Tier', 'المستوى')} | {L('Result', 'النتيجة')} |",
              "|---|---|---|---|"]
    lines += [f"| `{a['action']}` | {a['target']} | {a['tier']} | {a['status']} |" for a in f["actions"]]
    lines += ["", f"## {L('Verification', 'التحقق')}"]
    for v in f["verification"]:
        mark = "✅" if v["ok"] else "❌"
        detail = "; ".join(v["probes"].values())
        if v["live_symptoms"]:
            detail += f" | {L('live symptoms', 'أعراض حية')}: {'; '.join(v['live_symptoms'])}"
        lines.append(f"- {L('Round', 'جولة')} {v['round']} {mark} {detail}")
    if f["follow_up"]:
        lines += ["", f"## {L('Current state and follow-up', 'الوضع الحالي والمتابعة')}"]
        lines += [f"- {x}" for x in f["follow_up"]]
    lines += ["", f"## {L('Prevention', 'الوقاية')}"]
    lines += [f"- {x}" for x in text["prevention_ar" if ar else "prevention_en"]]
    return "\n".join(lines) + "\n"


def write_reports(f: dict) -> tuple[str, str]:
    text = None if mock_llm() else _llm_text(f)
    text = text or _template_text(f)
    return _render(f, text, ar=False), _render(f, text, ar=True)


if __name__ == "__main__":
    col = "report_md_ar" if "--ar" in sys.argv else "report_md"
    rows = db.query(f"SELECT {col} AS r FROM incidents WHERE {col} IS NOT NULL ORDER BY id DESC LIMIT 1")
    print(rows[0]["r"] if rows else "No incident reports yet.")
