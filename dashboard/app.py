# AgentMedic dashboard (Streamlit). Started by run.py, or on its own:
#   .venv/bin/python -m streamlit run dashboard/app.py
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from chaos.faults import reset_all  # noqa: E402
from chaos.scenarios import SCENARIOS, inject, truth  # noqa: E402
from dashboard import data  # noqa: E402
from doctor.actions import decide, pending_approvals  # noqa: E402

st.set_page_config(page_title="AgentMedic", page_icon="🩺", layout="wide")
st.markdown("""
<style>
.st-key-ar_report, .st-key-ar_report table { direction: rtl; text-align: right; }
.feed-line { font-family: ui-monospace, monospace; font-size: 0.82rem; margin: 0 0 4px 0; }
</style>
""", unsafe_allow_html=True)

STATUS = {"green": "🟢 Healthy", "yellow": "🟡 Degraded / being treated", "red": "🔴 Failing"}
STAGE_ICON = {"DETECT": "🔎", "MONITOR": "📋", "CORRELATE": "🔗", "DIAGNOSE": "🧠", "TREAT": "💊",
              "VERIFY": "✅", "REPORT": "📝", "ESCALATE": "🙋", "RESTORE": "♻️"}
SCENARIO_NAMES = {
    "F1": "Vendor API down", "F2": "Bad HR prompt", "F3": "Knowledge base corrupted", "F4": "Slow AI model",
    "F5": "Runaway loop", "F6": "Cascade: bad POs break Finance", "F10": "Unclear evidence (should escalate)",
    "S7": "Vendor API down + bad HR prompt", "S8": "Slow AI model + corrupted KB",
    "S9": "Cascade + runaway loop", "S10": "Vendor down + bad HR prompt + corrupted KB",
}


def t(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "-"


# ---------------------------------------------------------- sidebar: chaos panel (demo operator)
with st.sidebar:
    st.header("🧪 Chaos panel")
    st.caption("Break things on purpose and watch the doctor recover.")
    if st.button("▶ Demo: S7 (Vendor API down + bad HR prompt)", type="primary", width="stretch"):
        inject("S7")
        st.toast("Injected S7: Vendor API down + bad HR prompt")
    sid = st.selectbox("Scenario", list(SCENARIOS), format_func=lambda s: f"{s} · {SCENARIO_NAMES.get(s, '')}")
    expected, escalate = truth(sid)
    st.caption("Expected: " + ("the doctor escalates to a human" if escalate else ", ".join(sorted(expected))))
    if st.button("Inject scenario", width="stretch"):
        inject(sid)
        st.toast(f"Injected {sid}")
    if st.button("♻ Reset all", width="stretch"):
        reset_all()
        st.toast("All faults reset, known-good prompts restored")
    st.divider()
    st.caption("Currently injected")
    for f in data.active_faults() or ["nothing"]:
        st.caption(f"• {f}")


# ---------------------------------------------------------- header
st.title("🩺 AgentMedic")
st.caption(f"The reliability layer for an enterprise AI agent fleet · {data.mode_label()}")


@st.fragment(run_every="2s")
def live() -> None:
    cards = data.agent_cards()
    incs = data.incidents()
    latest = incs[0] if incs else None

    m = st.columns(5)
    m[0].metric("Agents healthy", f"{sum(c['status'] == 'green' for c in cards)}/{len(cards)}")
    m[1].metric("Incidents", len(incs))
    m[2].metric("Resolved", sum(i["status"] == "resolved" for i in incs))
    m[3].metric("Time to detect (latest)", f"{latest['detect_s']} s" if latest and latest["detect_s"] is not None else "-")
    m[4].metric("Time to recover (latest)", f"{latest['recover_s']} s" if latest and latest["recover_s"] else "-")

    st.subheader("Fleet")
    cols = st.columns(len(cards))
    for col, c in zip(cols, cards):
        with col.container(border=True):
            st.markdown(f"### {c['agent'].capitalize()}")
            st.markdown(f"**{STATUS[c['status']]}**" + ("  \n🩺 under treatment" if c["under_treatment"] else ""))
            health = f"{c['health']:.2f}" if c["health"] is not None else "-"
            st.markdown(
                f"Health score: **{health}**  \n"
                f"Error rate: **{c['error_rate']:.0%}**  \n"
                f"p95 latency: **{c['p95_ms']} ms**  \n"
                f"Tokens/min: **{c['tokens_per_min']}**  \n"
                f"Prompt: **v{c['prompt_version']}** {'✅' if c['prompt_known_good'] else '⚠️ not known-good'}  \n"
                f"Model: `{c['model']}`")
            for s in c["symptoms"][:3]:
                st.caption(f"⚠ {s}")

    svc = st.columns(len(data.services()))
    for col, s in zip(svc, data.services()):
        icon = "🟢" if s["status"] in ("up", "primary") and s["integrity"] == "ok" else "🟡" if s["status"] == "backup" else "🔴"
        extra = []
        if s["breaker"] == "open":
            extra.append("breaker open")
        if s["fallback"]:
            extra.append("serving backup")
        if s["integrity"] != "ok":
            extra.append(f"integrity {s['integrity']}")
        col.caption(f"{icon} **{s['name']}** · {s['status']}" + (f" · {', '.join(extra)}" if extra else ""))

    pending = pending_approvals()
    if pending:
        st.subheader("✋ Waiting for your approval")
        for a in pending:
            with st.container(border=True):
                left, b1, b2 = st.columns([6, 1, 1])
                left.markdown(f"**{a['action']}({a['target']})** · incident #{a['incident_id']}  \n"
                              f"Reason: {a['reason']}")
                if b1.button("Approve", key=f"approve_{a['id']}", type="primary"):
                    decide(a["id"], approve=True, by="dashboard")
                    st.toast(f"Approved {a['action']}({a['target']})")
                if b2.button("Reject", key=f"reject_{a['id']}"):
                    decide(a["id"], approve=False, by="dashboard")
                    st.toast(f"Rejected {a['action']}({a['target']})")

    left, right = st.columns([5, 7])
    with left:
        st.subheader("Doctor live feed")
        with st.container(height=460):
            for e in data.doctor_feed():
                icon = STAGE_ICON.get(e["stage"], "•")
                inc = f"#{e['incident_id']} " if e["incident_id"] else ""
                st.markdown(f"<p class='feed-line'>{t(e['ts'])} {icon} <b>{e['stage']}</b> {inc}"
                            f"{e['message']}</p>", unsafe_allow_html=True)
    with right:
        st.subheader("Hypothesis board")
        if latest:
            inc = data.incident(latest["id"])
            st.caption(f"Incident #{inc['id']} · {inc['status']} · opened {t(inc['opened_at'])} · "
                       f"source: {(inc['board'] or {}).get('source', '-')}")
            rows = data.hypothesis_rows(inc)
            if rows:
                for r in rows:
                    r["Confidence"] = round(r["Confidence"] * 100)
                st.dataframe(rows, hide_index=True, width="stretch", column_config={
                    "Confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=100,
                                                                  format="%d%%"),
                    "Cause": st.column_config.TextColumn(width="large"),
                })
            else:
                st.info("Diagnosing…")
            done = [a for a in inc["actions_list"]]
            if done:
                st.caption("Actions: " + " · ".join(
                    f"{a['action']}({a['target']}) [{a.get('tier')}] → {a['status']}" for a in done))
        else:
            st.info("No incidents yet. Use the chaos panel to break something.")


live()


# ---------------------------------------------------------- incident reports
@st.fragment(run_every="5s")
def reports() -> None:
    st.subheader("Incident reports")
    incs = data.incidents()
    if not incs:
        st.caption("Reports appear here after the doctor closes an incident.")
        return
    labels = {i["id"]: f"#{i['id']} · {i['status']} · {t(i['opened_at'])} · {', '.join(i['root_causes']) or '-'}"
              for i in incs}
    chosen = st.selectbox("Incident", list(labels), format_func=labels.get)
    inc = data.incident(chosen)
    if not inc or not inc["report_md"]:
        st.caption("The doctor is still working on this incident.")
        return
    en, ar = st.tabs(["English", "العربية"])
    with en:
        st.markdown(inc["report_md"])
    with ar:
        with st.container(key="ar_report"):
            st.markdown(inc["report_md_ar"])


reports()
