# AgentMedic dashboard (Streamlit). Started by run.py, or on its own:
#   .venv/bin/python -m streamlit run dashboard/app.py
#
# Layout (follows the demo story, left to right, top to bottom):
#   live office | doctor panel (steps, what it is doing, Approve / Reject)
#   KPI strip, incident timeline, hypothesis cards, fleet table, doctor feed, reports.
import base64
import html
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from chaos.faults import reset_all  # noqa: E402
from chaos.scenarios import SCENARIOS, inject, truth  # noqa: E402
from common.config import ROOT, get_config  # noqa: E402
from dashboard import data  # noqa: E402
from doctor.actions import decide, pending_approvals  # noqa: E402

LOGO_NAMES = ("logo.svg", "logo.png", "AgentMedic.svg", "AgentMedic.png", "agentmedic.svg", "agentmedic.png")


def find_logo() -> Path | None:
    for folder in (Path(__file__).parent / "assets", ROOT):
        for name in LOGO_NAMES:
            if (folder / name).exists():
                return folder / name
    return None


def data_url(path: Path | None) -> str:
    if not path:
        return ""
    mime = "image/svg+xml" if path.suffix == ".svg" else "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


LOGO = find_logo()
st.set_page_config(page_title="AgentMedic", page_icon=str(LOGO) if LOGO else "🩺", layout="wide")

CSS = (
    "<style>"
    ".block-container { padding-top: 1.2rem; max-width: 1500px; }"
    ".am-head { display:flex; align-items:center; gap:14px; margin-bottom:6px; }"
    ".am-head img { height:44px; }"
    ".am-title { font-size:1.9rem; font-weight:800; letter-spacing:-0.02em; color:#f1f5f9; }"
    ".am-sub { color:#94a3b8; font-size:0.92rem; }"
    ".pill { display:inline-block; padding:2px 10px; border-radius:999px; font-size:0.78rem; font-weight:600; }"
    ".pill.green { background:rgba(34,197,94,.15); color:#4ade80; border:1px solid rgba(34,197,94,.35); }"
    ".pill.yellow { background:rgba(245,158,11,.15); color:#fbbf24; border:1px solid rgba(245,158,11,.35); }"
    ".pill.red { background:rgba(239,68,68,.15); color:#f87171; border:1px solid rgba(239,68,68,.4); }"
    ".pill.blue { background:rgba(56,189,248,.12); color:#7dd3fc; border:1px solid rgba(56,189,248,.35); }"
    ".card { background:#111a2e; border:1px solid #1e2a44; border-radius:14px; padding:14px 16px; }"
    ".kpis { display:grid; grid-template-columns:repeat(5,1fr); gap:12px; margin:6px 0 14px 0; }"
    ".kpi .v { font-size:1.9rem; font-weight:800; color:#f8fafc; line-height:1.1; }"
    ".kpi .l { color:#94a3b8; font-size:0.8rem; text-transform:uppercase; letter-spacing:.06em; }"
    ".steps { display:flex; flex-direction:column; gap:8px; margin:10px 0 12px 0; }"
    ".step { display:flex; align-items:center; gap:10px; color:#64748b; font-weight:600; }"
    ".step .dot { width:14px; height:14px; border-radius:50%; border:2px solid #334155; flex:none; }"
    ".step.done { color:#e2e8f0; } .step.done .dot { background:#22c55e; border-color:#22c55e; }"
    ".step.cur { color:#7dd3fc; } .step.cur .dot { border-color:#38bdf8; animation:pulse 1.2s infinite; }"
    ".step.wait { color:#fbbf24; } .step.wait .dot { border-color:#f59e0b; background:rgba(245,158,11,.4); }"
    ".step .t { margin-left:auto; color:#64748b; font-variant-numeric:tabular-nums; font-weight:500; }"
    "@keyframes pulse { 0%{box-shadow:0 0 0 0 rgba(56,189,248,.6);} 100%{box-shadow:0 0 0 10px rgba(56,189,248,0);} }"
    ".say { background:#0b1220; border:1px solid #1e2a44; border-radius:10px; padding:10px 12px; color:#cbd5e1;"
    " font-size:0.88rem; min-height:64px; }"
    ".tl { position:relative; height:78px; margin:4px 8px 2px 8px; }"
    ".tl .bar { position:absolute; top:34px; left:0; right:0; height:4px; background:#1e2a44; border-radius:2px; }"
    ".tl .fill { position:absolute; top:34px; left:0; height:4px; border-radius:2px;"
    " background:linear-gradient(90deg,#ef4444,#f59e0b,#22c55e); }"
    ".tl .m { position:absolute; top:4px; transform:translateX(-50%); text-align:center; white-space:nowrap; }"
    ".tl .m .d { width:18px; height:18px; border-radius:50%; margin:3px auto; border:3px solid #111a2e; }"
    ".tl .m .lb { color:#cbd5e1; font-size:0.74rem; }"
    ".tl .m .tt { color:#64748b; font-size:0.72rem; }"
    ".hyp { display:grid; grid-template-columns:1fr; gap:10px; }"
    ".h { background:#111a2e; border:1px solid #1e2a44; border-radius:12px; padding:12px 14px; }"
    ".h.root { border-color:rgba(34,197,94,.55); }"
    ".h .top { display:flex; align-items:center; gap:10px; }"
    ".h .cause { font-weight:700; color:#f1f5f9; flex:1; }"
    ".h .pct { font-weight:800; color:#f8fafc; font-variant-numeric:tabular-nums; }"
    ".h .cb { height:6px; background:#1e2a44; border-radius:3px; margin:8px 0; overflow:hidden; }"
    ".h .cb div { height:100%; border-radius:3px; }"
    ".h .ev { display:grid; grid-template-columns:1fr 1fr; gap:10px; font-size:0.82rem; color:#94a3b8; }"
    ".h .ev b { color:#cbd5e1; }"
    ".chip { display:inline-block; background:#0b1220; border:1px solid #334155; color:#7dd3fc; border-radius:6px;"
    " padding:1px 7px; font-size:0.76rem; margin:2px 4px 0 0; font-family:ui-monospace,monospace; }"
    "table.fleet { width:100%; border-collapse:collapse; font-size:0.88rem; }"
    "table.fleet th { text-align:left; color:#64748b; font-weight:600; font-size:0.74rem; text-transform:uppercase;"
    " letter-spacing:.05em; padding:6px 8px; border-bottom:1px solid #1e2a44; }"
    "table.fleet td { padding:8px; border-bottom:1px solid #16213a; color:#e2e8f0; }"
    ".feed { font-family:ui-monospace,monospace; font-size:0.78rem; color:#cbd5e1; margin:0 0 5px 0; }"
    ".feed .s { color:#7dd3fc; font-weight:700; }"
    ".st-key-ar_report, .st-key-ar_report table { direction:rtl; text-align:right; }"
    "</style>"
)
st.markdown(CSS, unsafe_allow_html=True)

STATUS_PILL = {"green": ("green", "Healthy"), "yellow": ("yellow", "Degraded"), "red": ("red", "Failing")}
STAGE_ICON = {"DETECT": "🔎", "MONITOR": "📋", "CORRELATE": "🔗", "DIAGNOSE": "🧠", "TREAT": "💊",
              "VERIFY": "✅", "REPORT": "📝", "ESCALATE": "🙋", "RESTORE": "♻️"}
SCENARIO_NAMES = {
    "F1": "Vendor API down", "F2": "Bad HR prompt", "F3": "Knowledge base corrupted", "F4": "Slow AI model",
    "F5": "Runaway loop", "F6": "Cascade: bad POs break Finance", "F10": "Unclear evidence (should escalate)",
    "S7": "Vendor API down + bad HR prompt", "S8": "Slow AI model + corrupted KB",
    "S9": "Cascade + runaway loop", "S10": "Vendor down + bad HR prompt + corrupted KB",
}
TL_COLOR = {"bad": "#ef4444", "info": "#38bdf8", "fix": "#a78bfa", "human": "#f59e0b", "good": "#22c55e"}


def t(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "-"


def esc(s) -> str:
    return html.escape(str(s))


def pill(status: str) -> str:
    cls, label = STATUS_PILL.get(status, ("blue", status))
    return f"<span class='pill {cls}'>● {label}</span>"


# ---------------------------------------------------------- sidebar: chaos panel (demo operator)
with st.sidebar:
    if LOGO:
        st.image(str(LOGO), width=140)
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
logo_html = f"<img src='{data_url(LOGO)}'/>" if LOGO else "<span style='font-size:2.2rem'>🩺</span>"
st.markdown(f"<div class='am-head'>{logo_html}<div><div class='am-title'>AgentMedic</div>"
            f"<div class='am-sub'>The reliability layer for an enterprise AI agent fleet · "
            f"<span class='pill blue'>{esc(data.mode_label())}</span></div></div></div>", unsafe_allow_html=True)


# ---------------------------------------------------------- row 1: live office + doctor panel
office_col, doctor_col = st.columns([7.4, 3.6], gap="medium")
with office_col:
    # Our own page (not user input), drawn once and animated in the browser from the status feed,
    # so it never restarts.
    office = (Path(__file__).parent / "office.html").read_text(encoding="utf-8")
    feed = f"http://localhost:{get_config()['dashboard']['status_port']}"
    st.iframe(office.replace("__API__", feed).replace("__LOGO__", data_url(LOGO)), height=560)


@st.fragment(run_every="1s")
def doctor_panel() -> None:
    incs = data.incidents(1)
    st.markdown("<div class='am-sub' style='margin-bottom:4px'>🧑‍⚕️ THE DOCTOR</div>", unsafe_allow_html=True)
    if not incs:
        st.markdown("<div class='card'><b>All quiet.</b><br><span class='am-sub'>Watching the fleet. "
                    "Use the chaos panel to break something.</span></div>", unsafe_allow_html=True)
        return
    inc = incs[0]
    info = data.doctor_steps(inc["id"])
    status_pill = {"open": "<span class='pill blue'>working</span>",
                   "resolved": "<span class='pill green'>resolved</span>",
                   "escalated": "<span class='pill yellow'>escalated to a human</span>"}.get(inc["status"], "")
    rows = []
    for s in info["steps"]:
        cls = "done" if s["done"] else "cur" if s["current"] else ""
        label = s["label"]
        if s["key"] == "TREAT" and info["waiting_approval"]:
            cls, label = "wait", "Treat · waiting for your approval"
        if s["key"] == "VERIFY" and info["escalated"]:
            label = "Verify · handed to a human"
        at = f"{s['at']:.0f}s" if s["at"] is not None else ""
        rows.append(f"<div class='step {cls}'><div class='dot'></div>{esc(label)}<span class='t'>{at}</span></div>")
    icon = STAGE_ICON.get(info["last_stage"], "•")
    st.markdown(f"<div class='card'><div style='display:flex;justify-content:space-between'>"
                f"<b>Incident #{inc['id']}</b>{status_pill}</div><div class='steps'>{''.join(rows)}</div>"
                f"<div class='say'>{icon} {esc(info['last_message'][:260])}</div></div>", unsafe_allow_html=True)

    for a in pending_approvals():
        with st.container(border=True):
            st.markdown(f"✋ **Approve `{a['action']}({a['target']})`?**  \n"
                        f"<span class='am-sub'>{esc(a['reason'])}</span>", unsafe_allow_html=True)
            b1, b2 = st.columns(2)
            if b1.button("Approve", key=f"approve_{a['id']}", type="primary", width="stretch"):
                decide(a["id"], approve=True, by="dashboard")
                st.toast(f"Approved {a['action']}({a['target']})")
            if b2.button("Reject", key=f"reject_{a['id']}", width="stretch"):
                decide(a["id"], approve=False, by="dashboard")
                st.toast(f"Rejected {a['action']}({a['target']})")


with doctor_col:
    doctor_panel()


# ---------------------------------------------------------- row 2: KPIs, timeline, board, fleet table
def kpi_strip(cards: list[dict], incs: list[dict]) -> None:
    latest = incs[0] if incs else None
    healthy = sum(c["status"] == "green" for c in cards)
    kpis = [("Agents healthy", f"{healthy}/{len(cards)}"), ("Incidents", len(incs)),
            ("Resolved", sum(i["status"] == "resolved" for i in incs)),
            ("Time to detect", f"{latest['detect_s']}s" if latest and latest["detect_s"] is not None else "–"),
            ("Time to recover", f"{latest['recover_s']}s" if latest and latest["recover_s"] else "–")]
    st.markdown("<div class='kpis'>" + "".join(
        f"<div class='card kpi'><div class='l'>{esc(label)}</div><div class='v'>{esc(value)}</div></div>"
        for label, value in kpis) + "</div>", unsafe_allow_html=True)


def timeline_bar(incident_id: int) -> None:
    points = data.timeline(incident_id)
    end = max(p["t"] for p in points) or 1
    span = end * 1.1
    marks = "".join(
        f"<div class='m' style='left:{4 + 92 * p['t'] / span:.1f}%'><div class='lb'>{esc(p['label'])}</div>"
        f"<div class='d' style='background:{TL_COLOR[p['kind']]}'></div><div class='tt'>{p['t']:.0f}s</div></div>"
        for p in points)
    st.markdown(f"<div class='card' style='margin-bottom:14px'><div class='am-sub'>INCIDENT #{incident_id} TIMELINE"
                f"</div><div class='tl'><div class='bar'></div><div class='fill' style='width:{4 + 92 * end / span:.1f}%'>"
                f"</div>{marks}</div></div>", unsafe_allow_html=True)


def hypothesis_cards(incident_id: int | None) -> None:
    st.markdown("#### Hypothesis board")
    rows = data.hypothesis_rows(data.incident(incident_id)) if incident_id else []
    if not rows:
        st.info("No incidents yet. Use the chaos panel to break something.")
        return
    out = []
    for r in rows:
        root = r["Verdict"].startswith("✅")
        color = "#22c55e" if root else "#f59e0b" if r["Verdict"].startswith("❔") else "#475569"
        fixes = "".join(f"<span class='chip'>{esc(f.strip())}</span>" for f in r["Fix"].split(", ") if f != "-")
        ev_for = esc(r["Evidence for"]).replace("\n", "<br>")
        ev_against = esc(r["Evidence against"]).replace("\n", "<br>")
        out.append(
            f"<div class='h {'root' if root else ''}'><div class='top'><span>{r['Verdict'].split(' ')[0]}</span>"
            f"<span class='cause'>{esc(r['Cause'])}</span><span class='pct'>{r['Confidence']:.0%}</span></div>"
            f"<div class='cb'><div style='width:{r['Confidence'] * 100:.0f}%;background:{color}'></div></div>"
            f"<div class='ev'><div><b>Evidence for</b><br>{ev_for}</div><div><b>Evidence against</b><br>{ev_against}"
            f"</div></div><div style='margin-top:6px'>{fixes}</div></div>")
    st.markdown("<div class='hyp'>" + "".join(out) + "</div>", unsafe_allow_html=True)


def fleet_table(cards: list[dict]) -> None:
    st.markdown("#### Fleet")
    rows = []
    for c in cards:
        health = "-" if c["health"] is None else f"{c['health']:.2f}"
        prompt = f"v{c['prompt_version']} {'✅' if c['prompt_known_good'] else '⚠️'}"
        rows.append(f"<tr><td><b>{esc(c['agent'].capitalize())}</b>{' 🩺' if c['under_treatment'] else ''}</td>"
                    f"<td>{pill(c['status'])}</td><td>{health}</td><td>{c['error_rate']:.0%}</td>"
                    f"<td>{c['p95_ms']} ms</td><td>{c['tokens_per_min']}</td><td>{prompt}</td>"
                    f"<td><span class='chip'>{esc(c['model'])}</span></td></tr>")
    st.markdown("<div class='card'><table class='fleet'><tr><th>Agent</th><th>Status</th><th>Health</th>"
                "<th>Errors</th><th>p95</th><th>Tok/min</th><th>Prompt</th><th>Model</th></tr>"
                + "".join(rows) + "</table></div>", unsafe_allow_html=True)
    pills = []
    for s in data.services():
        ok = s["status"] in ("up", "primary") and s["integrity"] == "ok"
        state = "green" if ok else "yellow" if s["status"] == "backup" else "red"
        extra = " · breaker open" if s["breaker"] == "open" else ""
        extra += " · backup data" if s["fallback"] and s["name"] != "llm" else ""
        pills.append(f"<span class='pill {state}' style='margin:6px 6px 0 0'>{esc(s['name'])} · "
                     f"{esc(s['status'])}{esc(extra)}</span>")
    st.markdown("<div>" + "".join(pills) + "</div>", unsafe_allow_html=True)


def doctor_feed() -> None:
    st.markdown("#### Doctor live feed")
    with st.container(height=300):
        for e in data.doctor_feed(40):
            inc_id = f"#{e['incident_id']} " if e["incident_id"] else ""
            st.markdown(f"<p class='feed'>{t(e['ts'])} {STAGE_ICON.get(e['stage'], '•')} "
                        f"<span class='s'>{e['stage']}</span> {inc_id}{esc(e['message'])}</p>",
                        unsafe_allow_html=True)


@st.fragment(run_every="2s")
def overview() -> None:
    cards = data.agent_cards()
    incs = data.incidents()
    kpi_strip(cards, incs)
    if incs:
        timeline_bar(incs[0]["id"])
    board_col, fleet_col = st.columns([6, 5], gap="medium")
    with board_col:
        hypothesis_cards(incs[0]["id"] if incs else None)
    with fleet_col:
        fleet_table(cards)
        doctor_feed()


overview()


# ---------------------------------------------------------- incident reports
@st.fragment(run_every="5s")
def reports() -> None:
    st.markdown("#### Incident reports")
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
