"""Diagrams and charts for the project report. Run: uv run python docs/report/figures.py

Charts read the committed evidence files directly, so every plotted value is a
measured value from docs/evidence/.
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

HERE = Path(__file__).parent
OUT = HERE / "figures"
EVIDENCE = HERE.parent / "evidence"
INK = "#1f2933"
MUTED = "#52606d"
LINE = "#7b8794"
FILL = {
    "client": "#e3edf7",
    "control": "#e6f4ea",
    "data": "#fdf1e3",
    "store": "#f1e8f8",
    "lab": "#fbe9e9",
}
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "text.color": INK})


def box(ax, x, y, w, h, title, lines, kind):
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
            facecolor=FILL[kind], edgecolor=LINE, linewidth=0.9,
        )
    )
    ax.text(x + w / 2, y + h - 0.22, title, ha="center", va="top", fontsize=9.5, weight="bold")
    for index, line in enumerate(lines):
        ax.text(x + w / 2, y + h - 0.58 - index * 0.27, line, ha="center", va="top",
                fontsize=7.6, color=MUTED)


def arrow(ax, start, end, label="", offset=(0, 0.12), style="-|>", dashed=False):
    ax.add_patch(
        FancyArrowPatch(start, end, arrowstyle=style, mutation_scale=10, color=INK,
                        linewidth=0.9, linestyle=(0, (4, 3)) if dashed else "solid",
                        shrinkA=2, shrinkB=2)
    )
    if label:
        ax.text((start[0] + end[0]) / 2 + offset[0], (start[1] + end[1]) / 2 + offset[1], label,
                ha="center", va="bottom", fontsize=7.2, color=MUTED)


def architecture() -> None:
    fig, ax = plt.subplots(figsize=(10, 5.6))
    ax.set_xlim(0, 15)
    ax.set_ylim(0, 8.4)
    ax.axis("off")
    # Control plane (top row)
    box(ax, 0.2, 6.0, 2.8, 2.0, "Research console", ["React 19 · TypeScript", "Vite · TanStack Query", "Zod-validated responses"], "client")
    box(ax, 4.2, 6.0, 2.9, 2.0, "Control API", ["FastAPI /v1 · bearer scopes", "RFC 9457 problems", "idempotent run creation"], "control")
    box(ax, 8.4, 6.0, 2.9, 2.0, "PostgreSQL", ["experiments · sessions", "jobs (SKIP LOCKED leases)", "events · idempotency keys"], "store")
    box(ax, 12.0, 6.0, 2.8, 2.0, "Worker", ["crosstalk-worker", "3 queues · leases", "stage handlers"], "control")
    arrow(ax, (3.0, 7.0), (4.2, 7.0), "HTTPS /v1")
    arrow(ax, (7.1, 7.0), (8.4, 7.0), "SQL")
    arrow(ax, (12.0, 7.0), (11.3, 7.0))
    ax.text(11.65, 8.12, "claim · finish", ha="center", fontsize=7.2, color=MUTED)
    # Data path (middle row)
    ax.add_patch(FancyBboxPatch((0.1, 2.75), 11.4, 2.6, boxstyle="round,pad=0.02,rounding_size=0.1",
                                facecolor="none", edgecolor="#c4302b", linestyle=(0, (5, 3)), linewidth=0.9))
    ax.text(0.3, 5.2, "Collection child process (per session)", fontsize=8, color="#c4302b", va="top")
    box(ax, 0.4, 3.0, 2.6, 1.85, "Load generator", ["asyncio + h11 client", "seeded block plan", "per-read timestamps"], "data")
    box(ax, 3.6, 3.0, 2.3, 1.85, "Capture", ["dumpcap (lab) or", "recording relay (dev)"], "lab")
    box(ax, 6.5, 3.0, 2.5, 1.85, "TLS shaper", ["TLS 1.3 SSE proxy", "D0–D4 policies", "emission oracle log"], "data")
    box(ax, 9.5, 3.0, 1.8, 1.85, "Upstream", ["vLLM (GPU lab)", "or mock SSE"], "lab")
    arrow(ax, (3.0, 3.9), (3.6, 3.9))
    arrow(ax, (5.9, 3.9), (6.5, 3.9), "TLS")
    arrow(ax, (9.0, 3.9), (9.5, 3.9), "SSE")
    arrow(ax, (13.4, 6.0), (11.5, 4.9), "spawn", offset=(0.5, -0.05), dashed=True)
    # Analysis (bottom row)
    box(ax, 0.4, 0.2, 3.2, 1.9, "Reconstruction", ["pcapng → TCP reassembly", "TLS records → flow map"], "data")
    box(ax, 4.2, 0.2, 3.2, 1.9, "Validation", ["emission ↔ record fidelity", "tshark / keylog oracles"], "data")
    box(ax, 8.0, 0.2, 3.2, 1.9, "Analysis E0–E2", ["features · LR / LightGBM", "family-disjoint CV", "cluster bootstrap · Holm"], "data")
    box(ax, 11.8, 0.2, 3.0, 1.9, "Artifacts", ["Parquet · manifests", "report.md · figures", "served by API (DuckDB)"], "store")
    arrow(ax, (4.75, 3.0), (2.6, 2.1), "pcapng", offset=(0.75, -0.12))
    arrow(ax, (3.6, 1.15), (4.2, 1.15))
    arrow(ax, (7.4, 1.15), (8.0, 1.15))
    arrow(ax, (11.2, 1.15), (11.8, 1.15))
    fig.savefig(OUT / "fig_architecture.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def pipeline() -> None:
    fig, ax = plt.subplots(figsize=(10, 2.9))
    ax.set_xlim(0, 15)
    ax.set_ylim(0, 4.2)
    ax.axis("off")
    stages = [
        ("plan", "collect", "PLANNED"),
        ("collect", "collect", "COLLECTING →\nCOLLECTED"),
        ("reconstruct", "attack", "RECONSTRUCTED"),
        ("validate", "evaluate", "VALID / INVALID /\nPARTIAL"),
        ("analyze", "evaluate", "experiment"),
        ("report", "evaluate", "experiment"),
    ]
    width, gap = 2.05, 0.45
    for index, (name, queue, state) in enumerate(stages):
        x = 0.2 + index * (width + gap)
        kind = "control" if index >= 4 else "data"
        ax.add_patch(FancyBboxPatch((x, 1.7), width, 1.5, boxstyle="round,pad=0.02,rounding_size=0.08",
                                    facecolor=FILL[kind], edgecolor=LINE, linewidth=0.9))
        ax.text(x + width / 2, 2.85, name, ha="center", va="center", fontsize=10, weight="bold")
        ax.text(x + width / 2, 2.3, f"queue: {queue}", ha="center", va="center", fontsize=7.6, color=MUTED)
        ax.text(x + width / 2, 1.2, state, ha="center", va="top", fontsize=7.4, color=MUTED)
        if index < len(stages) - 1:
            arrow(ax, (x + width, 2.45), (x + width + gap, 2.45))
    ax.text(0.2, 3.85, "Per session", fontsize=8.5, weight="bold", color=INK)
    ax.text(0.2 + 4 * (width + gap), 3.85, "Per experiment (once all sessions are terminal)",
            fontsize=8.5, weight="bold", color=INK)
    ax.plot([0.2 + 4 * (width + gap) - gap / 2] * 2, [0.6, 3.9], color=LINE, linestyle=":", linewidth=0.9)
    ax.text(0.2, 0.15, "Each stage enqueues its successor with a deterministic stage key (idempotent); failures retry with "
            "full-jitter backoff, up to 5 attempts, then DEAD.", fontsize=7.4, color=MUTED)
    fig.savefig(OUT / "fig_pipeline.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    architecture()
    pipeline()
    runtime = sorted(EVIDENCE.glob("runtime-linux-v2-*.json"))
    if runtime:
        runtime_chart(json.loads(runtime[-1].read_text(encoding="utf-8")))


def runtime_chart(result: dict[str, object]) -> None:
    del result  # filled in once the v2 runtime evidence exists


if __name__ == "__main__":
    main()
