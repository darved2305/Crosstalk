"""Static report figures (PNG). Colors: validated categorical slots 1-3, light surface."""

from pathlib import Path
from typing import Protocol

import matplotlib

matplotlib.use("Agg")

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
# Categorical slots in fixed order; each regime always gets the same slot.
REGIME_COLORS = {"NAT": "#2a78d6", "MATCH-P": "#eb6834", "MATCH-PR": "#1baf7a"}
# Marker shape is the secondary encoding, so identity never rests on color alone.
REGIME_MARKERS = {"NAT": "o", "MATCH-P": "s", "MATCH-PR": "D"}


class _Axes(Protocol):
    def errorbar(self, *args: object, **kwargs: object) -> object: ...
    def axhline(self, *args: object, **kwargs: object) -> object: ...
    def text(self, *args: object, **kwargs: object) -> object: ...
    def set_xticks(self, *args: object, **kwargs: object) -> object: ...
    def set_ylabel(self, *args: object, **kwargs: object) -> object: ...
    def set_ylim(self, *args: object, **kwargs: object) -> object: ...
    def set_title(self, *args: object, **kwargs: object) -> object: ...
    def set_facecolor(self, *args: object, **kwargs: object) -> object: ...
    def grid(self, *args: object, **kwargs: object) -> object: ...
    def legend(self, *args: object, **kwargs: object) -> object: ...
    def tick_params(self, *args: object, **kwargs: object) -> object: ...
    def get_yaxis_transform(self) -> object: ...
    @property
    def spines(self) -> dict[str, "_Spine"]: ...


class _Spine(Protocol):
    def set_visible(self, visible: bool) -> object: ...
    def set_color(self, color: str) -> object: ...


class _Figure(Protocol):
    def savefig(self, *args: object, **kwargs: object) -> object: ...


def balanced_accuracy_by_regime(
    points: list[tuple[str, str, float, float, float]], chance: float, output: Path
) -> None:
    """Dot-and-whisker plot: one column per attacker model, one dot per regime.

    ``points`` holds (regime, model, value, ci_lo, ci_hi).
    """
    import matplotlib.pyplot as plt

    models = list(dict.fromkeys(model for _, model, *_ in points))
    regimes = [r for r in REGIME_COLORS if any(p[0] == r for p in points)]
    figure_and_axes: tuple[_Figure, _Axes] = plt.subplots(figsize=(7.5, 4.2), dpi=150)  # type: ignore[assignment]
    figure, axes = figure_and_axes
    axes.set_facecolor(SURFACE)
    width = 0.22
    for offset, regime in enumerate(regimes):
        for position, model in enumerate(models):
            match = [p for p in points if p[0] == regime and p[1] == model]
            if not match:
                continue
            _, _, value, low, high = match[0]
            x = position + (offset - (len(regimes) - 1) / 2) * width
            axes.errorbar(
                [x],
                [value],
                yerr=[[value - low], [high - value]],
                fmt=REGIME_MARKERS[regime],
                color=REGIME_COLORS[regime],
                markersize=7,
                markeredgecolor=SURFACE,
                markeredgewidth=1.5,
                elinewidth=2,
                capsize=0,
                label=regime if position == 0 else None,
            )
    axes.axhline(chance, color=MUTED, linewidth=1, linestyle=(0, (4, 3)))
    # Label sits in the right margin so it never collides with a whisker.
    axes.text(
        1.01,
        chance,
        f"chance\n{chance:.2f}",
        color=INK_SECONDARY,
        fontsize=8,
        va="center",
        ha="left",
        transform=axes.get_yaxis_transform(),
        clip_on=False,
    )
    axes.set_xticks(range(len(models)), models, color=INK)
    axes.set_ylabel("Balanced accuracy (95% cluster-bootstrap CI)", color=INK_SECONDARY)
    axes.set_ylim(0, 1)
    axes.set_title("E0: topic inference from TLS record features (R1)", color=INK, loc="left")
    axes.grid(axis="y", color=GRID, linewidth=0.6)
    axes.tick_params(colors=MUTED)
    for name in ("top", "right"):
        axes.spines[name].set_visible(False)
    for name in ("left", "bottom"):
        axes.spines[name].set_color(BASELINE)
    axes.legend(frameon=False, title="Regime", loc="upper left")
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, facecolor=SURFACE, bbox_inches="tight")
    plt.close("all")
