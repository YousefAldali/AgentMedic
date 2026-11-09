# AgentMedic dashboard (Streamlit). Started by run.py, or on its own:
#   .venv/bin/python -m streamlit run dashboard/app.py
#
# Layout, top to bottom:
#   header | status banner (what is happening, Approve / Reject when needed)
#   getting-started steps (only until the first incident)
#   live office + doctor panel (its steps in plain words)
#   tabs: Overview (numbers with live charts, agents, systems), How the doctor decided,
#   Technical log, Reports.
# The look lives in dashboard/style.css; plain names for technical ids in dashboard/names.py.
import base64
import html
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from chaos.faults import reset_all  # noqa: E402
from chaos.scenarios import SCENARIOS, inject, truth  # noqa: E402
from common.config import get_config  # noqa: E402
from dashboard import data, names  # noqa: E402
from dashboard.icons import bars, sparkline, stage as stage_icon, svg  # noqa: E402
from doctor.actions import decide, pending_approvals  # noqa: E402

HERE = Path(__file__).parent
def data_url(path: Path | None) -> str:
    if not path:
        return ""
    mime = "image/svg+xml" if path.suffix == ".svg" else "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


LOGO = HERE / "assets" / "logo.png"
ICON = HERE / "assets" / "icon.png"
st.set_page_config(page_title="AgentMedic", page_icon=str(ICON) if ICON else ":material/health_and_safety:",
                   layout="wide")
st.markdown(f"<style>{(HERE / 'style.css').read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)

STATUS_PILL = {"green": ("green", "Healthy"), "yellow": ("yellow", "Recovering"), "red": ("red", "Failing")}
STATUS_COLOR = {"green": "#4ade80", "yellow": "#fbbf24", "red": "#f87171"}
SCENARIO_NAMES = {
    "F1": "Supplier system down", "F2": "Bad update to the HR agent", "F3": "Knowledge base has wrong data",
    "F4": "AI model is slow", "F5": "Agent stuck in a loop", "F6": "Bad purchase orders break Finance",
    "F10": "Unclear cause (should hand to a person)",
    "S7": "Supplier system down + bad HR update", "S8": "Slow AI model + wrong knowledge base data",
    "S9": "Bad purchase orders + agent stuck in a loop",
    "S10": "Supplier down + bad HR update + wrong knowledge base data",
}
SYSTEM_STATUS = {"up": ("green", "Online"), "primary": ("green", "Main model"), "backup": ("yellow", "Backup model"),
                 "down": ("red", "Down"), "degraded": ("yellow", "Slow"), "erroring": ("red", "Errors"),
                 "unknown": ("red", "Unreachable")}
SYSTEM_ICON = {"hr_db": "database", "vendor_api": "store", "knowledge_base": "book", "llm": "cpu"}
AGENT_ICON = {"hr": "users", "procurement": "cart", "finance": "receipt", "support": "headset"}
AGENT_ROLE = {"hr": "HR questions", "procurement": "Quotes & orders", "finance": "Checks invoices",
              "support": "Customer help"}
STAGE_COLOR = {"MONITOR": "#7dd3fc", "DETECT": "#f87171", "CORRELATE": "#38bdf8", "DIAGNOSE": "#a78bfa",
               "TREAT": "#f472b6", "VERIFY": "#4ade80", "REPORT": "#2dd4bf", "RESTORE": "#4ade80",
               "ESCALATE": "#fbbf24"}
TL_COLOR = {"bad": "#ef4444", "info": "#38bdf8", "fix": "#a78bfa", "human": "#f59e0b", "good": "#22c55e"}
STEP_NAMES = {"DETECT": "Spot the problem", "DIAGNOSE": "Find the cause", "TREAT": "Fix it",
              "VERIFY": "Check the fix worked", "REPORT": "Write the report"}
DOING = {"MONITOR": "Testing every agent with questions it knows the answers to.",
         "DETECT": "Looking at what went wrong.", "CORRELATE": "Working out which system is involved.",
         "DIAGNOSE": "Weighing the possible causes.", "TREAT": "Applying the fix.",
         "VERIFY": "Asking the agents test questions to make sure the fix worked.",
         "RESTORE": "A system is healthy again, so the agents are switched back to it.",
         "ESCALATE": "It could not fix this safely, so it handed it to a person.",
         "REPORT": "Writing the incident report."}


# The banner, the doctor panel and the Overview tab refresh every second or two and need the
# same numbers, so each reading is shared for one second. The database path is part of the key,
# so a different database (like in the tests) never sees another one's numbers.
@st.cache_data(ttl=1, show_spinner=False)
def _cards(db_key: str) -> list[dict]:
    return data.agent_cards()


@st.cache_data(ttl=2, show_spinner=False)
def _trends(db_key: str) -> dict:
    return data.trends()


def cards_now() -> list[dict]:
    from common.db import db_path
    return _cards(str(db_path()))


def trends_now() -> dict:
    from common.db import db_path
    return _trends(str(db_path()))


def t(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "-"


def esc(s) -> str:
    return html.escape(str(s))


def pill(status: str) -> str:
    cls, label = STATUS_PILL.get(status, ("blue", status))
    return f"<span class='pill {cls}'>{svg('dot', 10)} {label}</span>"


def section(title: str, hint: str = "", icon: str = "", first: bool = False) -> str:
    ico = f"<span class='ic'>{svg(icon, 16)}</span>" if icon else ""
    return (f"<div class='sec{' first' if first else ''}'>{ico}<span class='t' role='heading' aria-level='2'>{esc(title)}</span>"
            + (f"<span class='d'>{esc(hint)}</span>" if hint else "") + "</div>")


# ---------------------------------------------------------- sidebar: test a failure
with st.sidebar:
    st.markdown(f"<div class='side-brand'><img src='{data_url(ICON)}' alt=''/><div class='nm'>Agent<span>Medic</span>"
                "</div></div>" if ICON else "", unsafe_allow_html=True)
    st.header(":material/science: Test a failure")
    st.caption("Cause a problem on purpose and watch the doctor find and fix it by itself. Nothing real is harmed.")
    sid = st.selectbox("Choose a failure", list(SCENARIOS), index=list(SCENARIOS).index("S7"),
                       format_func=lambda s: SCENARIO_NAMES.get(s, s))
    expected, escalate = truth(sid)
    outcome = ("The doctor sees the cause is unclear and hands it to a person instead of guessing."
               if escalate else "The doctor should find " + " and ".join(names.cause(c) for c in sorted(expected))
               + ", and fix it.")
    st.markdown(f"<div class='side-card'><b>What should happen</b><br>{esc(outcome)}</div>", unsafe_allow_html=True)
    if st.button("Start this failure", icon=":material/bolt:", type="primary", width="stretch"):
        inject(sid)
        st.toast(f"Started: {SCENARIO_NAMES.get(sid, sid)}. Watch the banner and the doctor panel.")
    if st.button("Reset everything", icon=":material/restart_alt:", width="stretch",
                 help="Turns every failure off and puts the agents back to normal."):
        reset_all()
        st.toast("Everything is back to normal.")
    st.divider()
    st.caption("ACTIVE FAILURES")
    faults = data.active_faults()
    if faults:
        for f in faults:
            st.caption(f"• {names.plain(f)}")
    else:
        st.caption("None. Everything is running normally.")


# ---------------------------------------------------------- header
def model_chip() -> str:
    from common.config import env
    fleet = env("FLEET_MODEL", "not set")
    doctor = env("DOCTOR_MODEL") or fleet
    if doctor == fleet:
        return f"<span class='model'>{svg('cpu', 14)} AI model <code>{esc(fleet)}</code></span>"
    return (f"<span class='model'>{svg('cpu', 14)} Agents <code>{esc(fleet)}</code> · Doctor "
            f"<code>{esc(doctor)}</code></span>")


logo_html = f"<img src='{data_url(ICON)}' alt=''/>" if ICON else svg("activity", 44, "#2dd4bf")
st.markdown(f"<div class='hero'>{logo_html}<div><div class='title' role='heading' aria-level='1'>AgentMedic</div>"
            f"<div class='sub'>Watches your AI agents, finds what broke, and fixes it safely.</div></div>"
            f"<div class='right'><span class='live'><span class='d'></span>LIVE</span>{model_chip()}</div></div>",
            unsafe_allow_html=True)


# ---------------------------------------------------------- status banner: what is happening, what to do
def banner(kind: str, icon: str, title: str, text: str, stat: tuple[str, str] | None = None) -> None:
    right = (f"<div class='stat'><div class='n'>{esc(stat[0])}</div><div class='l'>{esc(stat[1])}</div></div>"
             if stat else "")
    st.markdown(f"<div class='banner {kind}' role='status' aria-live='polite'><div class='orb' aria-hidden='true'>{svg(icon, 26)}</div><div><div class='bt' role='heading' aria-level='2'>{title}</div>"
                f"<div class='bd'>{text}</div></div>{right}</div>", unsafe_allow_html=True)


@st.fragment(run_every="1s")
def status_banner() -> None:
    approvals = pending_approvals()
    incs = data.incidents(1)
    latest = incs[0] if incs else None
    if approvals:
        a = approvals[0]
        plain = names.ACTIONS.get(a["action"], a["action"])
        who = names.AGENTS.get(a["target"], names.SYSTEMS.get(a["target"], a["target"]))
        with st.container(key="approval_box"):
            banner("warn", "hand", "The doctor needs your OK for one fix",
                   f"<b>{esc(plain)} ({esc(who)})</b><br>Why: {esc(names.plain(a['reason']))}. "
                   f"<span class='tech'>Approve <code>{esc(a['action'])}({esc(a['target'])})</code>?</span>")
            b1, b2, _ = st.columns([1, 1, 3])
            if b1.button("Approve", key=f"approve_{a['id']}", icon=":material/check:", type="primary",
                         width="stretch"):
                decide(a["id"], approve=True, by="dashboard")
                st.toast(f"Approved: {plain}")
            if b2.button("Reject", key=f"reject_{a['id']}", icon=":material/close:", width="stretch"):
                decide(a["id"], approve=False, by="dashboard")
                st.toast(f"Rejected: {plain}")
        return
    if latest and latest["status"] == "open":
        causes = names.causes_sentence(latest["root_causes"])
        running = int(time.time() - (latest["first_symptom_at"] or latest["opened_at"]))
        banner("info", "stethoscope", "Problem found. The doctor is working on it",
               (f"Cause: {esc(causes)}. " if causes else "") + "You don't need to do anything unless it asks "
               "for your OK. Follow along in the doctor panel.", (f"{running}s", f"incident #{latest['id']}"))
    elif latest and latest["status"] == "escalated" and time.time() - (latest["resolved_at"] or 0) < 600:
        banner("warn", "alert", "Handed to a person",
               "The evidence was unclear, so the doctor did not guess. Read why in the <b>Reports</b> tab, "
               "then click <b>Reset everything</b> in the sidebar.", ("–", f"incident #{latest['id']}"))
    elif latest and latest["status"] == "resolved" and time.time() - (latest["resolved_at"] or 0) < 600:
        causes = names.causes_sentence(latest["root_causes"]) or "the problem"
        banner("good", "check", "Fixed and checked",
               f"The doctor fixed {esc(causes)} and checked that every agent answers correctly again. "
               "The full story is in the <b>Reports</b> tab.",
               (f"{latest['recover_s']}s" if latest["recover_s"] else "–", "time to fix"))
    else:
        cards = cards_now()
        healthy = sum(c["status"] == "green" for c in cards)
        banner("good", "shield", "Everything is working",
               "All agents are answering correctly. To see the doctor at work, start a failure from the sidebar.",
               (f"{healthy}/{len(cards)}", "agents healthy"))


status_banner()


# ---------------------------------------------------------- getting started (until the first incident)
if not data.incidents(1):
    steps = [("Choose a failure", "In the sidebar, pick a problem to cause on purpose."),
             ("Click Start", "The doctor notices it, finds the cause and starts fixing it by itself."),
             ("Approve if asked", "Risky fixes wait for you. A yellow box with <b>Approve</b> appears at the top."),
             ("Read the report", "When it is fixed, open <b>Reports</b>. It is in English and Arabic.")]
    st.markdown("<div class='guide'>" + "".join(
        f"<div class='g'><div class='n'>{i}</div><div><div class='gt'>{title}</div><div class='gd'>{text}</div>"
        "</div></div>" for i, (title, text) in enumerate(steps, 1)) + "</div>", unsafe_allow_html=True)


# ---------------------------------------------------------- live office + doctor panel
office_row = st.container(key="office_row")
office_col, doctor_col = office_row.columns([7.6, 3.4], gap="medium")
with office_col:
    # Our own page (not user input), drawn once and animated in the browser from the status feed,
    # so it never restarts.
    office = (HERE / "office.html").read_text(encoding="utf-8")
    feed = f"http://localhost:{get_config()['dashboard']['status_port']}"
    with st.container(key="office_frame"):
        st.iframe(office.replace("__API__", feed).replace("__LOGO__", data_url(LOGO)), height="content")


def ago(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60:02d}s"


def rounds_box() -> str:
    # When the doctor last tested every agent, how they did, and a countdown to the next round.
    r = data.health_rounds()
    if not r["last"]:
        return ("<div class='rounds'><div class='rt'>First test round starting…</div>"
                "<div class='bar'><div style='width:8%'></div></div></div>")
    every = get_config()["health"]["interval_s"]
    done = 100 * (1 - (r["next_in"] or 0) / every)
    ok = r["passed"] == r["total"]
    return (f"<div class='rounds'><div class='rt'><span>{svg('check' if ok else 'alert', 14, '#4ade80' if ok else '#fbbf24')}"
            f" Last test round <b>{ago(time.time() - r['last'])} ago</b> · {r['passed']}/{r['total']} agents passed</span>"
            f"<span class='nx'>next in {ago(r['next_in'] or 0)}</span></div>"
            f"<div class='bar'><div style='width:{done:.0f}%'></div></div></div>")


@st.fragment(run_every="1s")
def doctor_panel() -> None:
    incs = data.incidents(1)
    head = ("<div class='doc-head'><div class='av'>" + svg("stethoscope", 20) + "</div><div><div class='nm'>"
            "The doctor</div><div class='rl'>{sub}</div></div>{pill}</div>")
    quiet = not incs or (incs[0]["status"] != "open" and time.time() - (incs[0]["resolved_at"] or 0) > 600)
    if quiet:
        st.markdown(
            "<div class='card'>" + head.format(sub="Making rounds", pill="<span class='pill green'>All quiet</span>")
            + rounds_box() + "<div class='watch'>"
            f"<div class='row'><span class='i'>{svg('check', 15, '#4ade80')}</span>Tests every agent with questions "
            "it knows the answers to</div>"
            f"<div class='row'><span class='i'>{svg('search', 15, '#7dd3fc')}</span>Watches for errors, slowness, "
            "loops and wrong data</div>"
            f"<div class='row'><span class='i'>{svg('hand', 15, '#fbbf24')}</span>Asks you before any risky fix</div>"
            f"<div class='row'><span class='i'>{svg('file', 15, '#a78bfa')}</span>Writes a report in English and "
            "Arabic</div></div></div>", unsafe_allow_html=True)
        return
    inc = incs[0]
    info = data.doctor_steps(inc["id"])
    status_pill = {"open": "<span class='pill blue'>Working on it</span>",
                   "resolved": "<span class='pill green'>Fixed</span>",
                   "escalated": "<span class='pill yellow'>Handed to a person</span>"}.get(inc["status"], "")
    rows = []
    for s in info["steps"]:
        cls = "done" if s["done"] else "cur" if s["current"] else ""
        label = STEP_NAMES.get(s["key"], s["label"])
        if s["key"] == "TREAT" and info["waiting_approval"]:
            cls, label = "wait", "Fix it · waiting for your OK"
        if s["key"] == "VERIFY" and info["escalated"]:
            label = "Handed to a person"
        at = f"{s['at']:.0f}s" if s["at"] is not None else ""
        rows.append(f"<div class='step {cls}'><div class='dot'></div>{esc(label)}<span class='t'>{at}</span></div>")
    if inc["status"] == "resolved":
        now = "Done. Every agent passed its test questions again."
    elif info["waiting_approval"]:
        now = "Waiting for you: approve or reject the fix in the yellow box at the top."
    else:
        now = DOING.get(info["last_stage"], "Working on it.")
    said = names.plain(info["last_message"].split(" (failed:")[0].split(" | live:")[0].split(" (#")[0])
    said = said if len(said) <= 160 else said[:159] + "…"
    st.markdown("<div class='card'>" + head.format(sub=f"Incident #{inc['id']}", pill=status_pill)
                + f"<div class='steps'>{''.join(rows)}</div>"
                f"<div class='say'><b>Now:</b> {esc(now)}<div class='tech' style='margin-top:6px'>"
                f"{stage_icon(info['last_stage'], 13, '#94a3b8')} {esc(said)}</div></div></div>",
                unsafe_allow_html=True)


with doctor_col:
    doctor_panel()


# ---------------------------------------------------------- Overview tab
def kpi_strip(cards: list[dict], incs: list[dict], trend: dict) -> None:
    latest = incs[0] if incs else None
    healthy = sum(c["status"] == "green" for c in cards)
    per_min = 60 / trend["bucket_s"]
    tasks = [round(x * per_min) for x in trend["tasks"]]
    lat = [x for x in trend["latency"] if x is not None]
    kpis = [
        ("Agents healthy", f"{healthy}/{len(cards)}", "answering correctly", "shield", "#4ade80", None),
        ("Tasks / min", f"{tasks[-2] if len(tasks) > 1 else 0}", "last 15 minutes", "zap", "#2dd4bf", tasks),
        ("Response time", f"{lat[-1] / 1000:.1f}s" if lat else "–", "average AI answer", "clock", "#38bdf8",
         trend["latency"]),
        ("Time to notice", f"{latest['detect_s']}s" if latest and latest["detect_s"] is not None else "–",
         "latest problem", "search", "#a78bfa", [i["detect_s"] for i in reversed(incs)]),
        ("Time to fix", f"{latest['recover_s']}s" if latest and latest["recover_s"] else "–",
         "latest problem, until checked", "wrench", "#fbbf24", [i["recover_s"] for i in reversed(incs)]),
    ]
    out = []
    for n, (label, value, hint, icon, color, series) in enumerate(kpis):
        if n >= 3:
            chart = f"<div class='spark'>{bars(series, color, label=label + ' in seconds')}</div>" if series else ""
        else:
            chart = f"<div class='spark'>{sparkline(series, color, label=label + ' over the last 15 minutes')}</div>" if series else ""
        out.append(f"<div class='card kpi'><div class='top'><div class='l'>{esc(label)}</div><div class='v'>{esc(value)}"
                   f"</div><div class='ic' style='background:{color}1f;color:{color}'>{svg(icon, 16)}</div></div>"
                   f"<div class='hint'>{esc(hint)}</div>{chart}</div>")
    st.markdown("<div class='kpis'>" + "".join(out) + "</div>", unsafe_allow_html=True)


def agent_cards(cards: list[dict], trend: dict) -> None:
    out = []
    for c in cards:
        color = STATUS_COLOR.get(c["status"], "#94a3b8")
        health = "–" if c["health"] is None else f"{c['health']:.0%}"
        notes = []
        if c["under_treatment"]:
            notes.append(f"{svg('stethoscope', 13)} Being treated by the doctor")
        if not c["prompt_known_good"]:
            notes.append(f"{svg('alert', 13)} Running new instructions")
        if c["symptoms"]:
            notes.append(f"{svg('alert', 13)} {esc(names.SYMPTOMS.get(names.symptom_type(c['symptoms'][0]), ''))}")
        series = trend["agent_latency"].get(c["agent"], [])
        out.append(
            f"<div class='card agent' style='--accent:{color}'><div class='head'>"
            f"<div class='av'>{svg(AGENT_ICON.get(c['agent'], 'user'), 20)}</div><div>"
            f"<div class='nm'>{esc(names.AGENTS.get(c['agent'], c['agent']))}</div>"
            f"<div class='rl'>{esc(AGENT_ROLE.get(c['agent'], ''))}</div></div></div>"
            f"<div class='badge'>{pill(c['status'])}</div>"
            f"<div class='stats'><div class='s'><div class='k'>Correct</div><div class='x'>{health}</div></div>"
            f"<div class='s'><div class='k'>Errors</div><div class='x'>{c['error_rate']:.0%}</div></div>"
            f"<div class='s'><div class='k'>Slowest</div><div class='x'>{c['p95_ms'] / 1000:.1f}s</div></div></div>"
            + (f"<div class='note'>{notes[0]}</div>" if notes else "")
            + f"<div class='spark'>{sparkline(series, color, height=36, label=names.AGENTS.get(c['agent'], c['agent']) + ' response time in ms')}</div></div>")
    st.markdown("<div class='agents'>" + "".join(out) + "</div>", unsafe_allow_html=True)


def systems_row() -> None:
    out = []
    for s in data.services():
        status = s["status"] if s["integrity"] == "ok" else "erroring"
        cls, label = SYSTEM_STATUS.get(status, ("red", status))
        if s["integrity"] != "ok":
            label = "Wrong data"
        notes = []
        if s["breaker"] == "open":
            notes.append("paused by the doctor")
        if s["fallback"] and s["name"] != "llm":
            notes.append("agents use saved backup data")
        sub = ", ".join(notes).capitalize() if notes else ""
        out.append(f"<div class='card sys'><div class='ic'>{svg(SYSTEM_ICON.get(s['name'], 'link'), 18)}</div>"
                   f"<div><div class='nm'>{esc(names.SYSTEMS.get(s['name'], s['name']))}</div>"
                   f"<div class='row'><span class='pill {cls}'>{svg('dot', 10)} {esc(label)}</span>"
                   + (f"<span class='ex'>{esc(sub)}</span>" if sub else "") + "</div></div></div>")
    st.markdown("<div class='systems'>" + "".join(out) + "</div>", unsafe_allow_html=True)


@st.fragment(run_every="2s")
def overview_tab() -> None:
    cards = cards_now()
    trend = trends_now()
    kpi_strip(cards, data.incidents(), trend)
    st.markdown(section("AI agents", "response time over the last 15 minutes", "users"), unsafe_allow_html=True)
    agent_cards(cards, trend)
    st.markdown(section("Systems the agents use", "", "link"), unsafe_allow_html=True)
    systems_row()


# ---------------------------------------------------------- How the doctor decided tab
def timeline_bar(incident_id: int) -> None:
    points = data.timeline(incident_id)
    for p in points:
        if p["label"].startswith("First fix: "):
            p["label"] = "First fix"
    end = max(p["t"] for p in points) or 1
    span = end * 1.1
    # Labels alternate above and below the line so close moments don't overlap.
    marks = "".join(
        (f"<div class='m' style='left:{4 + 92 * p['t'] / span:.1f}%'><div class='lb'>{esc(p['label'])} · "
         f"{p['t']:.0f}s</div><div class='d' style='background:{TL_COLOR[p['kind']]}'></div></div>")
        if i % 2 == 0 else
        (f"<div class='m low' style='left:{4 + 92 * p['t'] / span:.1f}%'><div class='d' "
         f"style='background:{TL_COLOR[p['kind']]}'></div><div class='lb'>{esc(p['label'])} · {p['t']:.0f}s</div></div>")
        for i, p in enumerate(points))
    st.markdown(f"<div class='card' style='margin-bottom:14px'><div class='cap'>INCIDENT #{incident_id} TIMELINE"
                f"</div><div class='tl'><div class='bar'></div><div class='fill' style='width:{4 + 92 * end / span:.1f}%'>"
                f"</div>{marks}</div></div>", unsafe_allow_html=True)


def hypothesis_cards(incident_id: int | None) -> None:
    rows = data.hypothesis_rows(data.incident(incident_id)) if incident_id else []
    if not rows:
        st.info("No incidents yet. Use Test a failure in the sidebar to break something.")
        return
    labels = {"root cause": "Cause found", "inconclusive": "Not sure", "rejected": "Ruled out"}
    out = []
    for r in rows:
        root = r["Verdict"] == "root cause"
        color = "#4ade80" if root else "#fbbf24" if r["Verdict"] == "inconclusive" else "#94a3b8"
        mark = svg({"root cause": "check", "inconclusive": "help"}.get(r["Verdict"], "x"), 18, color)
        fixes = "".join(f"<span class='chip'>{svg('wrench', 12)} {esc(names.fix(f.strip()))}</span>"
                        for f in r["Fix"].split(", ") if f != "-")
        ev_for = esc(names.plain(r["Evidence for"])).replace("\n", "<br>")
        ev_against = esc(names.plain(r["Evidence against"])).replace("\n", "<br>")
        out.append(
            f"<div class='h {'root' if root else ''}'><div class='top'>{mark}"
            f"<span class='cause'>{esc(names.plain(r['Cause']))}</span><span class='verdict' style='color:{color}'>"
            f"{labels[r['Verdict']]}</span><span class='pct'>{r['Confidence']:.0%}</span></div>"
            f"<div class='cb'><div style='width:{r['Confidence'] * 100:.0f}%;background:{color}'></div></div>"
            f"<div class='ev'><div><b>Evidence for</b><br>{ev_for}</div><div><b>Evidence against</b><br>{ev_against}"
            f"</div></div>" + (f"<div style='margin-top:8px'>{fixes}</div>" if fixes else "") + "</div>")
    st.markdown("<div class='hyp'>" + "".join(out) + "</div>", unsafe_allow_html=True)


@st.fragment(run_every="2s")
def reasoning_tab() -> None:
    incs = data.incidents()
    st.markdown("<p class='empty'>For the latest problem: each possible cause, the evidence for and against it, "
                "and how sure the doctor is. It only acts on causes it is at least "
                f"{get_config()['doctor']['confidence_threshold']:.0%} sure of.</p>", unsafe_allow_html=True)
    if incs:
        timeline_bar(incs[0]["id"])
    hypothesis_cards(incs[0]["id"] if incs else None)


# ---------------------------------------------------------- Technical log tab
@st.fragment(run_every="2s")
def activity_tab() -> None:
    st.markdown("<p class='empty'>Every step the doctor takes, newest first, in the short technical form it "
                "records. For a plain summary, see the Reports tab.</p>", unsafe_allow_html=True)
    events = data.doctor_feed(60)
    with st.container(height=620):
        if not events:
            st.markdown("<p class='empty'>Nothing yet. The doctor writes a line here for every step it takes.</p>",
                        unsafe_allow_html=True)
        rows = []
        for e in events:
            color = STAGE_COLOR.get(e["stage"], "#94a3b8")
            inc_id = f"<span class='inc'>#{e['incident_id']}</span>" if e["incident_id"] else ""
            rows.append(f"<div class='ev'><span class='ts'>{t(e['ts'])}</span>"
                        f"<span class='st' style='color:{color};background:{color}1a;border-color:{color}55'>"
                        f"{stage_icon(e['stage'], 12)} {esc(e['stage'].title())}</span>{inc_id}"
                        f"<span class='msg'>{esc(e['message'])}</span></div>")
        if rows:
            st.markdown("<div class='log'>" + "".join(rows) + "</div>", unsafe_allow_html=True)


# ---------------------------------------------------------- Reports tab
@st.fragment(run_every="5s")
def reports() -> None:
    incs = data.incidents()
    if not incs:
        st.markdown("<p class='empty'>Reports appear here after the doctor closes an incident.</p>",
                    unsafe_allow_html=True)
        return
    word = {"open": "in progress", "resolved": "fixed", "escalated": "handed to a person"}
    labels = {i["id"]: f"#{i['id']} · {word.get(i['status'], i['status'])} · {t(i['opened_at'])} · "
                       f"{names.causes_sentence(i['root_causes']) or '-'}" for i in incs}
    chosen = st.selectbox("Incident", list(labels), format_func=labels.get)
    inc = data.incident(chosen)
    if not inc or not inc["report_md"]:
        st.caption("The doctor is still working on this incident.")
        return
    # ?lang=ar in the address opens the Arabic report first.
    default = "العربية" if st.query_params.get("lang") == "ar" else "English"
    en, ar = st.tabs(["English", "العربية"], default=default)
    with en:
        with st.container(key="en_report"):
            st.markdown(names.report(inc["report_md"]))
    with ar:
        with st.container(key="ar_report"):
            st.markdown(inc["report_md_ar"])


st.markdown("<div style='height:22px'></div>", unsafe_allow_html=True)
default_tab = "Reports" if st.query_params.get("lang") == "ar" else "Overview"
tab_overview, tab_reasoning, tab_activity, tab_reports = st.tabs(
    ["Overview", "How the doctor decided", "Technical log", "Reports"], default=default_tab)
with tab_overview:
    overview_tab()
with tab_reasoning:
    reasoning_tab()
with tab_activity:
    activity_tab()
with tab_reports:
    reports()
