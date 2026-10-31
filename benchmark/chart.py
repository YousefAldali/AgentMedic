# Benchmark chart for the pitch deck: 4 small panels (one measure each, one axis each),
# AgentMedic vs the naive baseline, on a dark 16:9 slide background.
# Colours were checked with a colour-blind / contrast validator against the slide surface.
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from common.config import ROOT  # noqa: E402

OUT = ROOT / "benchmark" / "results"
SURFACE = "#0f1b2d"
TEXT, TEXT_2, GRID = "#e2e8f0", "#94a3b8", "#1e2a44"
SERIES = {"AgentMedic": "#0fa595", "Naive retry + restart": "#9085e9"}


def _metrics(summary: dict) -> list[tuple[str, str, dict, str]]:
    def pct(v):
        return None if v is None else v * 100
    return [
        ("Recovery rate", "higher is better", {k: pct(v["recovery_rate"]) for k, v in summary.items()}, "{:.0f}%"),
        ("Mean time to recover", "lower is better", {k: v["mean_recover_s"] for k, v in summary.items()}, "{:.0f} s"),
        ("Root causes found", "higher is better", {k: pct(v["root_cause_accuracy"]) for k, v in summary.items()}, "{:.0f}%"),
        ("Wrong actions", "lower is better", {k: v["wrong_actions"] for k, v in summary.items()}, "{:.0f}"),
    ]


def draw(rows=None, summary: dict | None = None, mode: str | None = None, path=None) -> str:
    if summary is None:
        data = json.loads((OUT / "benchmark.json").read_text(encoding="utf-8"))
        summary, mode = data["summary"], data["mode"]
    n_scen = len({r.scenario for r in rows}) if rows else None
    path = path or OUT / "benchmark.png"
    plt.rcParams.update({"font.family": "sans-serif", "font.size": 11})
    fig, axes = plt.subplots(1, 4, figsize=(8, 4.5), dpi=200, facecolor=SURFACE)
    labels = list(summary)
    for ax, (title, hint, values, fmt) in zip(axes, _metrics(summary)):
        ax.set_facecolor(SURFACE)
        top = max([v for v in values.values() if v is not None] + [1]) * 1.25
        for i, label in enumerate(labels):
            v = values.get(label)
            if v is None:
                ax.text(i, top * 0.04, "never", ha="center", va="bottom", color=TEXT_2, fontsize=9)
                continue
            ax.bar(i, max(v, top * 0.008), width=0.55, color=SERIES.get(label, "#64748b"), linewidth=0, zorder=3)
            ax.text(i, v + top * 0.03, fmt.format(v), ha="center", va="bottom", color=TEXT, fontsize=11,
                    fontweight="bold", zorder=4)
        ax.set_ylim(0, top)
        ax.set_xlim(-0.6, len(labels) - 0.4)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(["AgentMedic" if "Agent" in x else "Naive" for x in labels], color=TEXT_2, fontsize=9)
        ax.set_yticks([])
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(axis="x", length=0, pad=6)
        ax.set_title(f"{title}\n", color=TEXT, fontsize=10.5, fontweight="bold", loc="left", pad=2)
        ax.text(0, 1.02, hint, transform=ax.transAxes, color=TEXT_2, fontsize=8.5, va="bottom")
    handles = [plt.Rectangle((0, 0), 1, 1, color=SERIES.get(x, "#64748b")) for x in labels]
    fig.legend(handles, labels, loc="upper right", ncol=2, frameon=False, labelcolor=TEXT, fontsize=9.5,
               bbox_to_anchor=(0.985, 0.985), handlelength=1.2, handleheight=1.0)
    scope = f"{n_scen} scenarios" if n_scen else "all scenarios"
    fig.text(0.02, 0.955, "AgentMedic vs naive retry + restart", color=TEXT, fontsize=14, fontweight="bold")
    fig.text(0.02, 0.905, f"{scope}, same faults and the same neutral judge · "
             f"{'scripted model (MOCK)' if mode == 'mock' else 'real models'}", color=TEXT_2, fontsize=9)
    fig.subplots_adjust(left=0.03, right=0.985, top=0.74, bottom=0.1, wspace=0.35)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return str(path)


if __name__ == "__main__":
    print(draw())
