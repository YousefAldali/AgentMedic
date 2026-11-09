# Small set of line icons (Lucide style, 24x24, stroke-based) used in the dashboard's HTML.
# They take the colour of the surrounding text unless a colour is given.
PATHS = {
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "clipboard": '<rect width="8" height="4" x="8" y="2" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6'
                 'a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4M12 16h4M8 11h.01M8 16h.01"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>'
            '<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "cpu": '<rect width="16" height="16" x="4" y="4" rx="2"/><rect width="6" height="6" x="9" y="9" rx="1"/>'
           '<path d="M15 2v2M15 20v2M2 15h2M2 9h2M20 15h2M20 9h2M9 2v2M9 20v2"/>',
    "wrench": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 '
              '6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    "check": '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    "file": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>'
            '<path d="M16 13H8M16 17H8M10 9H8"/>',
    "user": '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "hand": '<path d="M18 11V6a2 2 0 0 0-4 0v5"/><path d="M14 10V4a2 2 0 0 0-4 0v6"/><path d="M10 10.5V6a2 2 0 0 0-4 '
            '0v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 '
            '2.83-2.82L7 15"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>'
               '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    "x": '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>',
    "help": '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
             '<path d="M12 9v4M12 17h.01"/>',
    "stethoscope": '<path d="M11 2v2M5 2v2M5 3H4a2 2 0 0 0-2 2v4a6 6 0 0 0 12 0V5a2 2 0 0 0-2-2h-1"/>'
                   '<path d="M8 15a6 6 0 0 0 12 0v-3"/><circle cx="20" cy="10" r="2"/>',
    "activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "dot": '<circle cx="12" cy="12" r="4" fill="currentColor"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "zap": '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 '
           '.78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
    "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
    "store": '<path d="m2 7 4.41-4.41A2 2 0 0 1 7.83 2h8.34a2 2 0 0 1 1.42.59L22 7"/><path d="M4 12v8a2 2 0 0 0 2 2h12a2 '
             '2 0 0 0 2-2v-8"/><path d="M2 7h20"/><path d="M22 7v3a2 2 0 0 1-2 2a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 '
             '2.7 0 0 1 16 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 12 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 '
             '0-.82 0A2.7 2.7 0 0 1 8 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 4 12a2 2 0 0 1-2-2V7"/>',
    "book": '<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 '
            '1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>',
    "shield": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 '
              '6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    "trend": '<path d="M22 7 13.5 15.5 8.5 10.5 2 17"/><path d="M16 7h6v6"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 '
             '4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "briefcase": '<path d="M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/><rect width="20" height="14" x="2" y="6" rx="2"/>',
    "cart": '<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2.05 2.05h2l2.66 12.42a2 2 0 0 0 '
            '2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"/>',
    "receipt": '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M16 8h-6a2 2 '
               '0 1 0 0 4h4a2 2 0 1 1 0 4H8"/><path d="M12 17.5v-11"/>',
    "headset": '<path d="M3 11h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5Zm0 0a9 9 0 1 1 18 0m0 0v5a2 2 0 0 1-2 '
               '2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3Z"/><path d="M21 16v2a4 4 0 0 1-4 4h-5"/>',
}

STAGE = {"DETECT": "search", "MONITOR": "clipboard", "CORRELATE": "link", "DIAGNOSE": "cpu", "TREAT": "wrench",
         "VERIFY": "check", "REPORT": "file", "ESCALATE": "user", "RESTORE": "refresh"}


def svg(name: str, size: int = 16, color: str | None = None, stroke: float = 2) -> str:
    style = f"color:{color};" if color else ""
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}' viewBox='0 0 24 24' "
            f"fill='none' stroke='currentColor' stroke-width='{stroke}' stroke-linecap='round' "
            f"stroke-linejoin='round' aria-hidden='true' focusable='false' style='vertical-align:-0.18em;{style}'>"
            f"{PATHS.get(name, PATHS['dot'])}</svg>")


def stage(name: str, size: int = 15, color: str | None = None) -> str:
    return svg(STAGE.get(name, "dot"), size, color)


def sparkline(values: list, color: str, width: int = 160, height: int = 40, fill: bool = True, label: str = "") -> str:
    # A tiny line chart as inline SVG. Missing values (None) are skipped.
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return (f"<svg width='100%' height='{height}' viewBox='0 0 {width} {height}' preserveAspectRatio='none'>"
                f"<line x1='0' y1='{height - 2}' x2='{width}' y2='{height - 2}' stroke='{color}' stroke-opacity='.35' "
                f"stroke-dasharray='3 4'/></svg>")
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    # Small wobbles stay small: the chart never zooms in closer than 30% of the highest value.
    span = max(hi - lo, abs(hi) * 0.3, 1)
    lo = hi - span if hi - lo < span else lo
    n = max(len(values) - 1, 1)
    xy = [(i * width / n, height - 4 - (v - lo) / span * (height - 10)) for i, v in pts]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    area = f"M{xy[0][0]:.1f},{height} L" + " L".join(f"{x:.1f},{y:.1f}" for x, y in xy) + f" L{xy[-1][0]:.1f},{height} Z"
    gid = f"g{abs(hash((color, line))) % 10**8}"
    grad = (f"<defs><linearGradient id='{gid}' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='{color}' "
            f"stop-opacity='.35'/><stop offset='1' stop-color='{color}' stop-opacity='0'/></linearGradient></defs>")
    last = xy[-1]
    seen = [v for v in values if v is not None]
    summary = f"{label}: from {seen[0]:g} to {seen[-1]:g}, highest {max(seen):g}" if label else ""
    aria = f"role='img' aria-label='{summary}'" if summary else "aria-hidden='true'"
    return (f"<svg width='100%' height='{height}' viewBox='0 0 {width} {height}' preserveAspectRatio='none' "
            f"{aria}>{grad}" + (f"<path d='{area}' fill='url(#{gid})'/>" if fill else "")
            + f"<polyline points='{line}' fill='none' stroke='{color}' stroke-width='2' stroke-linejoin='round' "
            f"stroke-linecap='round' vector-effect='non-scaling-stroke'/>"
            f"<circle cx='{last[0]:.1f}' cy='{last[1]:.1f}' r='3' fill='{color}'/></svg>")


def bars(values: list, color: str, width: int = 160, height: int = 40, label: str = "") -> str:
    # Small bar chart, one bar per value (oldest first), the newest one brightest.
    vals = [v for v in values if v is not None][-12:]
    if not vals:
        return ""
    hi = max(vals) or 1
    gap, n = 4, len(vals)
    w = min(26, (width - gap * (n - 1)) / n)
    out = []
    for i, v in enumerate(vals):
        h = max(3, (height - 4) * v / hi)
        x = width - (n - i) * (w + gap) + gap
        op = 1 if i == n - 1 else .45
        out.append(f"<rect x='{x:.1f}' y='{height - h:.1f}' width='{w:.1f}' height='{h:.1f}' rx='3' "
                   f"fill='{color}' fill-opacity='{op}'/>")
    aria = (f"role='img' aria-label='{label}: last {n} incidents, " + ", ".join(f"{v:g}" for v in vals) + "'"
            if label else "aria-hidden='true'")
    return (f"<svg width='100%' height='{height}' viewBox='0 0 {width} {height}' preserveAspectRatio='xMaxYMax meet' "
            f"{aria}>{''.join(out)}</svg>")
