# The Arabic incident report, written fully in Arabic (not the English report with
# Arabic headings). Names, symptoms, causes, evidence, actions and results are translated
# from the recorded facts. In real-model mode, text the doctor wrote in its own words is
# translated by the model; in MOCK mode the rule-based wording is translated here.
import re
from datetime import datetime

AGENT = {"hr": "وكيل الموارد البشرية", "procurement": "وكيل المشتريات", "finance": "وكيل المالية",
         "support": "وكيل خدمة العملاء"}
SYSTEM = {"vendor_api": "نظام الموردين", "hr_db": "قاعدة بيانات الموارد البشرية", "knowledge_base": "قاعدة المعرفة",
          "llm": "نموذج الذكاء الاصطناعي", "procurement": "وكيل المشتريات"}
SYMPTOM = {
    "tool_error_rate": "نسبة مرتفعة من الأخطاء في الأداة",
    "tool_error_streak": "أخطاء متتالية في الأداة",
    "latency_high": "بطء في استجابة نموذج الذكاء الاصطناعي",
    "llm_error_rate": "أخطاء في استدعاءات نموذج الذكاء الاصطناعي",
    "run_failure_rate": "فشل متكرر في تنفيذ المهام",
    "repeated_call_loop": "تكرار الاستدعاء نفسه في حلقة",
    "token_spike": "استهلاك مفرط للرموز",
    "empty_results_rate": "نتائج بحث فارغة أو غير مفيدة",
    "health_score_drop": "انخفاض درجة فحوص الصحة",
    "upstream_bad_output": "مخرجات غير صالحة من الوكيل السابق",
}
STATUS = {"down": "متوقف", "erroring": "يعيد أخطاء", "degraded": "بطيء", "unreachable": "لا يمكن الوصول إليه",
          "up": "يعمل", "primary": "النموذج الأساسي", "backup": "النموذج الاحتياطي"}
ACTION = {
    "open_circuit_breaker": "فتح قاطع الدائرة", "close_circuit_breaker": "إغلاق قاطع الدائرة",
    "enable_fallback": "تفعيل البيانات الاحتياطية", "disable_fallback": "إيقاف البيانات الاحتياطية",
    "switch_model": "التحويل إلى النموذج الاحتياطي", "kill_run": "إيقاف التشغيل العالق",
    "set_step_cap": "تحديد سقف للخطوات والرموز", "replay_run": "إعادة تنفيذ المهام الفاشلة",
    "rollback_prompt": "استرجاع آخر نسخة سليمة من التعليمات", "rebuild_index": "إعادة بناء فهرس البحث",
}
RESULT = {"done": "تم", "pending": "بانتظار الموافقة", "rejected": "رُفض", "not approved in time": "لم تتم الموافقة في الوقت المحدد",
          "refused": "مرفوض لأنه ممنوع", "unavailable": "غير متاح", "failed": "فشل"}
TIER = {"auto": "تلقائي", "approval": "يتطلب موافقة بشرية", "forbidden": "ممنوع"}
# Status as the predicate of "ما زال" (accusative), e.g. "ما زال متوقفًا".
STATUS_STILL = {"down": "متوقفًا", "erroring": "يعيد أخطاء", "degraded": "بطيئًا", "unreachable": "خارج الخدمة",
                "up": "يعمل"}


def name(x: str) -> str:
    return AGENT.get(x) or SYSTEM.get(x) or x


def _t(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "-"


def _secs(a: float | None, b: float | None) -> str:
    return f"{b - a:.1f} ثانية" if a and b else "-"


def action_ar(a: dict) -> str:
    return f"{ACTION.get(a['action'], a['action'])} ({name(a['target'])})"


def symptom_ar(s: dict) -> str:
    where = f" في {name(s['dependency'])}" if s.get("dependency") and s["dependency"] != "llm" else ""
    return f"{name(s['agent'])}: {SYMPTOM.get(s['type'], s['type'])}{where} (القيمة {s['value']}، الحد {s['threshold']})"


CAUSE_PATTERNS = [
    (r"^(\w+) outage \(status=(\w+)\)", lambda m: f"تعطل {name(m[1])} (الحالة: {STATUS.get(m[2], m[2])})"),
    (r"^(\w+) is serving corrupted data", lambda m: f"{name(m[1])} تقدم بيانات تالفة (فشل فحص سلامة البيانات)"),
    (r"^bad prompt deployed for (\w+)", lambda m: f"نشر تعليمات معيبة ل{name(m[1])}"),
    (r"^LLM endpoint degraded", lambda m: "تدهور خدمة نموذج الذكاء الاصطناعي"),
    (r"^runaway tool loop in (\w+)", lambda m: f"حلقة تكرار لا تنتهي لدى {name(m[1])}"),
    (r"^cascade: (\w+) is producing invalid output that (.+) consumes",
     lambda m: f"فشل متسلسل: {name(m[1])} ينتج مخرجات غير صالحة يعتمد عليها {', '.join(name(x.strip()) for x in m[2].split(','))}"),
    (r"^(\w+) gives wrong answers or fails without a dependency error",
     lambda m: f"{name(m[1])} يعطي إجابات خاطئة دون خطأ في الأنظمة التي يعتمد عليها"),
    (r"^([\w, ]+) tool sending bad requests to (\w+)",
     lambda m: f"أداة {name(m[1].split(',')[0].strip())} ترسل طلبات غير صحيحة إلى {name(m[2])}"),
]

EVIDENCE_PATTERNS = [
    (r"^safety rule: dropped (\w+)\((\w+)\): (.+)$",
     lambda m: f"قاعدة أمان: تم استبعاد إجراء «{ACTION.get(m[1], m[1])}» على {name(m[2])} لعدم وجود دليل كافٍ"),
    (r"^safety rule: .*capped at (\d+)%", lambda m: f"قاعدة أمان: خُفّضت الثقة إلى {m[1]}% لأن الأدلة غير كافية"),
    (r"^(\w+) health endpoint reports status=(\w+)(?:, integrity=(\w+))?",
     lambda m: f"فحص الصحة في {name(m[1])} يفيد بأن الحالة: {STATUS.get(m[2], m[2])}"
               + (f"، وسلامة البيانات: {'سليمة' if m[3] == 'ok' else 'فاشلة'}" if m[3] else "")),
    (r"^(\w+) health endpoint reports the service healthy", lambda m: f"فحص الصحة في {name(m[1])} يفيد بأن الخدمة سليمة"),
    (r"^(\w+) health endpoint confirms a real failure", lambda m: f"فحص الصحة في {name(m[1])} يؤكد وجود عطل فعلي"),
    (r"^(\d+) of (\d+) errors are 5xx", lambda m: f"{m[1]} من أصل {m[2]} أخطاء صادرة من الخادم (5xx)"),
    (r"^(\d+) errors are 5xx", lambda m: f"{m[1]} أخطاء صادرة من الخادم (5xx)، ولا تفسرها الطلبات الخاطئة"),
    (r"^(\d+) of (\d+) errors are 4xx", lambda m: f"{m[1]} من أصل {m[2]} أخطاء بسبب طلبات رفضتها الخدمة (4xx)"),
    (r"^(\w+): (\d+) LLM calls, (\d+) LLM errors, avg (\d+) ms",
     lambda m: f"{name(m[1])}: {m[2]} استدعاء للنموذج و{m[3]} أخطاء بمتوسط {m[4]} مللي ثانية، أي أن النموذج يعمل طبيعيًا"),
    (r"^(?:other )?agents on the same LLM are healthy: (.+)$",
     lambda m: "الوكلاء الآخرون الذين يستخدمون النموذج نفسه سليمون: " + "، ".join(name(x.strip()) for x in m[1].split(","))),
    (r"^other agents using (\w+) are healthy: (.+)$",
     lambda m: f"الوكلاء الآخرون الذين يستخدمون {name(m[1])} سليمون: " + "، ".join(name(x.strip()) for x in m[2].split(","))),
    (r"^active prompt v(\d+) is the last known-good version", lambda m: f"التعليمات النشطة (v{m[1]}) هي آخر نسخة سليمة"),
    (r"^active prompt v(\d+) is not the last known-good v(\d+)",
     lambda m: f"التعليمات النشطة (v{m[1]}) ليست آخر نسخة سليمة (v{m[2]})"),
    (r"^v(\d+) went live (\d+) s before the first symptom", lambda m: f"نُشرت النسخة v{m[1]} قبل {m[2]} ثانية من أول عرض"),
    (r"^(\w+)'s output is what the downstream agents fail on", lambda m: f"مخرجات {name(m[1])} هي ما تفشل عليه الوكلاء التالية"),
    (r"^(\d+)/(\d+) (\w+) calls failed", lambda m: f"فشل {m[1]} من أصل {m[2]} استدعاءات للأداة"),
    (r"^last (\d+) (\w+) calls failed in a row", lambda m: f"فشلت آخر {m[1]} استدعاءات متتالية للأداة"),
    (r"^(\d+)/(\d+) recent purchase orders from procurement are invalid",
     lambda m: f"{m[1]} من أصل {m[2]} من أوامر الشراء الأخيرة غير صالحة"),
    (r"^golden checks score ([\d.]+)", lambda m: f"درجة فحوص الصحة: {m[1]}"),
    (r"^run (\w+) called (\w+)(.*) (\d+) times", lambda m: f"التشغيل {m[1]} استدعى الأداة نفسها {m[4]} مرات"),
    (r"^run (\w+) used (\d+) tokens", lambda m: f"التشغيل {m[1]} استهلك {m[2]} رمزًا"),
    (r"^p95 LLM latency (\d+) ms over (\d+) calls", lambda m: f"زمن الاستجابة (p95) {m[1]} مللي ثانية عبر {m[2]} استدعاء"),
    (r"^(\d+)/(\d+) (\w+) calls returned nothing useful", lambda m: f"{m[1]} من أصل {m[2]} عمليات بحث لم تُرجع نتائج مفيدة"),
    (r"^HTTP (\d+)", lambda m: f"رمز الخطأ HTTP {m[1]}"),
]


def _match(text: str, patterns) -> str | None:
    for pat, fn in patterns:
        m = re.match(pat, text.strip())
        if m:
            return fn(m)
    return None


def evidence_ar(text: str, translated: dict) -> str | None:
    if text in translated:
        return translated[text]
    sym = re.match(r"^(\w+): (\w+)(?: on (\w+))? \(([\d.]+), threshold ([\d.]+)\)", text)
    if sym:
        return symptom_ar({"agent": sym[1], "type": sym[2], "dependency": sym[3], "value": sym[4], "threshold": sym[5]})
    return _match(text, EVIDENCE_PATTERNS)


def cause_ar(h: dict, translated: dict) -> str:
    cause = h["cause"].replace(" (direct proof from the rules)", "")
    proof = " (دليل مباشر من القواعد)" if "(direct proof" in h["cause"] else ""
    return (translated.get(h["cause"]) or _match(cause, CAUSE_PATTERNS) or by_fix(h) or "فرضية أخرى") + proof


def by_fix(h: dict) -> str | None:
    for f in h.get("proposed_fix", []):
        a, t = f["action"], f["target"]
        if a in ("open_circuit_breaker", "enable_fallback"):
            return f"تعطل {name(t)}"
        if a == "rebuild_index":
            return "تلف فهرس قاعدة المعرفة"
        if a == "switch_model":
            return "تدهور خدمة نموذج الذكاء الاصطناعي"
        if a == "rollback_prompt":
            return f"نشر تعليمات معيبة ل{name(t)}"
        if a in ("kill_run", "set_step_cap"):
            return f"حلقة تكرار لا تنتهي لدى {name(t)}"
    return None


def causes_sentence(names: list[str]) -> str:
    if not names:
        return "لم يُحدَّد سبب جذري مؤكد."
    if len(names) == 1:
        return f"تبيّن أن السبب الجذري هو: {names[0]}."
    count = {2: "سببين جذريين مستقلين", 3: "ثلاثة أسباب جذرية مستقلة"}.get(len(names), f"{len(names)} أسباب جذرية مستقلة")
    return f"تبيّن وجود {count}: " + "، و".join(names) + "."


def escalation_ar(reason: str) -> str:
    m = re.search(r"only (\d+)% confident and needs (\d+)%", reason)
    if m:
        return (f"الأدلة غير حاسمة: أقوى فرضية بلغت ثقتها {m[1]}% فقط، بينما يتطلب التنفيذ {m[2]}% على الأقل، "
                "ولا يوجد إجراء آمن مناسب، لذلك لم يُخمّن AgentMedic.")
    if "no safe automatic fix" in reason:
        return "السبب الجذري المرجّح لا يملك إصلاحًا تلقائيًا آمنًا، لذلك لم يُخمّن AgentMedic."
    if "could not be verified" in reason:
        return "تعذّر التحقق من تعافي النظام بعد عدة محاولات تشخيص."
    return "لم يتمكن AgentMedic من حل الحادثة بثقة كافية."


def verification_ar(detail: str) -> str:
    parts = []
    for agent, p, t in re.findall(r"(\w+) golden checks (\d+)/(\d+)", detail):
        parts.append(f"فحوص الصحة ل{name(agent)}: {p} من {t}")
    return "، ".join(parts) or "—"


def render(f: dict, text: dict, translated: dict | None = None) -> str:
    tr = translated or {}
    status = {"resolved": "تم الحل", "escalated": "أُحيلت إلى فريق بشري"}.get(f["status"], f["status"])
    causes = {h["cause"] for h in f["root_causes"]}
    lines = [
        f"# تقرير الحادثة رقم {f['id']}",
        "",
        f"**الحالة:** {status}  ",
        f"**زمن الاكتشاف:** {_secs(f['first_symptom_at'], f['opened_at'])} · "
        f"**زمن التعافي:** {_secs(f['first_symptom_at'], f['recovered_at'])}",
        "",
        "## الملخص",
        text["summary_ar"],
        "",
        "## التسلسل الزمني",
        f"- {_t(f['first_symptom_at'])} رُصد أول عرض",
        f"- {_t(f['opened_at'])} فُتحت الحادثة",
    ]
    for a in f["actions"]:
        lines.append(f"- {_t(a.get('ts'))} إجراء: {action_ar(a)}: {RESULT.get(a['status'], a['status'])}")
    if f["recovered_at"]:
        lines.append(f"- {_t(f['recovered_at'])} تأكّد تعافي النظام")
    lines += ["", "## الأعراض المرصودة"]
    lines += [f"- {symptom_ar(s)}" for s in f["symptoms"]]
    lines += ["", "## الفرضيات التي دُرست", "",
              "| الفرضية | الثقة | القرار | أبرز الأدلة المؤيدة | أبرز الأدلة المعارضة |", "|---|---|---|---|---|"]
    for h in f["hypotheses"]:
        if h["cause"] in causes:
            verdict = "سبب جذري"
        elif h["confidence"] >= 0.3:
            verdict = "غير حاسمة"
        else:
            verdict = "مرفوضة"

        def cell(items):
            out = [x for x in (evidence_ar(i, tr) for i in items) if x]
            return " · ".join(x.replace("|", "/") for x in out[:3]) or "-"
        lines.append(f"| {cause_ar(h, tr)} | {h['confidence']:.0%} | {verdict} | {cell(h['evidence_for'])} | "
                     f"{cell(h['evidence_against'])} |")
    lines += ["", "## الإجراءات المنفذة", ""]
    if f["actions"]:
        lines += ["| الإجراء | الهدف | المستوى | النتيجة |", "|---|---|---|---|"]
        lines += [f"| {ACTION.get(a['action'], a['action'])} | {name(a['target'])} | {TIER.get(a['tier'], a['tier'])} | "
                  f"{RESULT.get(a['status'], a['status'])} |" for a in f["actions"]]
    else:
        lines.append("لم يُنفَّذ أي إجراء، لأن أي فرضية لم تبلغ درجة الثقة اللازمة لإجراء آمن.")
    lines += ["", "## التحقق من التعافي"]
    for v in f["verification"]:
        mark = "ناجحة" if v["ok"] else "فاشلة"
        lines.append(f"- الجولة {v['round']}: **{mark}**، {verification_ar('; '.join(v['probes'].values()))}")
    if f["follow_up"]:
        lines += ["", "## الوضع الحالي والمتابعة"]
        lines += [f"- {x[1] if isinstance(x, (list, tuple)) else x}" for x in f["follow_up"]]
    lines += ["", "## التوصيات الوقائية"]
    lines += [f"- {x}" for x in text["prevention_ar"]]
    return "\n".join(lines) + "\n"
