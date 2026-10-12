"""Comparison charts for the report (SPEC §10): every stamp's ink colour and
perforation plotted together, grouped by catalogue number, perforation,
cancellation date or type. Points carry their stamp number so they can be
traced to the stamp cards.

Each function returns PNG bytes, or None when there is nothing to compare
(for example no cancellation dates recorded).
"""

from __future__ import annotations

import io
from collections import Counter
from collections.abc import Callable

import matplotlib
import matplotlib.lines

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from stampan.core.overview import OverviewEntry  # noqa: E402

# Okabe-Ito (colour-blind safe) plus a few distinct extras.
PALETTE = [
    "#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00",
    "#F0E442", "#000000", "#882255", "#44AA99", "#999933", "#AA4499",
]  # fmt: skip
OTHER = "#BBBBBB"
MAX_GROUPS = 10  # more distinct groups than this are merged into "other"

_STYLE = {
    "font.size": 8,
    "axes.titlesize": 9,
    "axes.labelsize": 8,
    "axes.edgecolor": "#888888",
    "axes.grid": True,
    "grid.color": "#e6e6e6",
    "grid.linewidth": 0.5,
    "legend.fontsize": 7,
    "legend.frameon": False,
}


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=170, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _number(e: OverviewEntry) -> str:
    return e.stamp_label.replace("Stamp ", "")


def _annotate(ax, xs, ys, labels) -> None:
    for x, y, text in zip(xs, ys, labels, strict=True):
        ax.annotate(
            text, (x, y), xytext=(2.5, 2.5), textcoords="offset points",
            fontsize=5, color="#555555",
        )  # fmt: skip


def _colour_axes(fig):
    ax_ab, ax_lc = fig.subplots(1, 2, gridspec_kw={"width_ratios": [1, 1]})
    ax_ab.set_title("CIELAB a*b*")
    ax_ab.set_xlabel("a*")
    ax_ab.set_ylabel("b*")
    ax_ab.set_aspect("equal", adjustable="datalim")
    ax_lc.set_title("Lightness vs chroma")
    ax_lc.set_xlabel("C*")
    ax_lc.set_ylabel("L*")
    return ax_ab, ax_lc


def _plot_colour(ax_ab, ax_lc, entries, **scatter_kw) -> None:
    if not entries:
        return
    a = [e.lab[1] for e in entries]
    b = [e.lab[2] for e in entries]
    c = [e.lch[1] for e in entries]
    lightness = [e.lch[0] for e in entries]
    labels = [_number(e) for e in entries]
    kw = {"s": 26, "edgecolors": "#222222", "linewidths": 0.4, "zorder": 3} | scatter_kw
    ax_ab.scatter(a, b, **kw)
    ax_lc.scatter(c, lightness, **kw)
    _annotate(ax_ab, a, b, labels)
    _annotate(ax_lc, c, lightness, labels)


def _groups(entries, key: Callable[[OverviewEntry], str]) -> list[tuple[str, str, list]]:
    """(name, colour, entries) for the most common groups; the rest is
    merged into "other" so the legend stays readable."""
    counts = Counter(key(e) for e in entries)
    named = [name for name, _ in counts.most_common(MAX_GROUPS)]
    named.sort(key=lambda n: (n.startswith("no ") or n.startswith("not "), n))
    result = []
    for i, name in enumerate(named):
        colour = OTHER if name.startswith(("no ", "not ")) else PALETTE[i % len(PALETTE)]
        result.append((name, colour, [e for e in entries if key(e) == name]))
    rest = [e for e in entries if key(e) not in named]
    if rest:
        result.append((f"other ({len({key(e) for e in rest})} groups)", OTHER, rest))
    return result


def colour_by_group(entries, key, title: str) -> bytes | None:
    """a*b* and L*–C* scatter, one colour per group. None when every entry
    falls in the same group (nothing to compare)."""
    groups = _groups(entries, key)
    if len(groups) < 2:
        return None
    with plt.rc_context(_STYLE):
        fig = plt.figure(figsize=(9.2, 4.4))
        ax_ab, ax_lc = _colour_axes(fig)
        for name, colour, members in groups:
            _plot_colour(ax_ab, ax_lc, members, c=colour, label=f"{name} ({len(members)})")
        fig.legend(
            *ax_ab.get_legend_handles_labels(), loc="lower center",
            ncol=min(4, len(groups)), bbox_to_anchor=(0.5, -0.02),
        )  # fmt: skip
        fig.suptitle(title, fontsize=10, y=1.0)
        fig.subplots_adjust(bottom=0.24, wspace=0.28)
        return _png(fig)


def colour_by_year(entries) -> bytes | None:
    """a*b* and L*–C* coloured by cancellation year on a continuous scale;
    undated stamps as hollow grey circles, estimated dates as squares."""
    dated = [e for e in entries if e.year is not None]
    if len({e.year for e in dated}) < 2:
        return None
    undated = [e for e in entries if e.year is None]
    years = [e.year for e in dated]
    with plt.rc_context(_STYLE):
        fig = plt.figure(figsize=(9.2, 4.4))
        ax_ab, ax_lc = _colour_axes(fig)
        norm = plt.Normalize(min(years), max(years))
        for estimated, marker in ((False, "o"), (True, "s")):
            subset = [e for e in dated if e.cancellation_estimated == estimated]
            if subset:
                _plot_colour(
                    ax_ab, ax_lc, subset, c=[e.year for e in subset], cmap="viridis",
                    norm=norm, marker=marker,
                    label="estimated date" if estimated else "date from postmark",
                )  # fmt: skip
        if undated:
            _plot_colour(
                ax_ab, ax_lc, undated, facecolors="none", edgecolors="#999999",
                label=f"no date ({len(undated)})",
            )  # fmt: skip
        mappable = plt.cm.ScalarMappable(norm=norm, cmap="viridis")
        fig.subplots_adjust(bottom=0.2, wspace=0.28, right=0.88)
        bar = fig.colorbar(mappable, cax=fig.add_axes([0.905, 0.3, 0.014, 0.55]))
        bar.set_label("cancellation year")
        bar.ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
        fig.legend(
            *ax_ab.get_legend_handles_labels(), loc="lower center", ncol=3,
            bbox_to_anchor=(0.45, -0.02),
        )  # fmt: skip
        fig.suptitle("Ink colour by cancellation date", fontsize=10, y=1.0)
        return _png(fig)


def shade_over_time(entries, key, group_title: str) -> bytes | None:
    """Hue angle and chroma against cancellation year -- does the shade
    drift across printings? Coloured by ``key`` (e.g. catalogue number)."""
    dated = [e for e in entries if e.year is not None]
    if len({e.year for e in dated}) < 2:
        return None
    groups = _groups(dated, key)
    with plt.rc_context(_STYLE):
        fig, (ax_h, ax_c) = plt.subplots(1, 2, figsize=(9.2, 3.8))
        for name, colour, members in groups:
            x = np.array([e.year for e in members], dtype=float)
            x = x + np.array([0.0 if not e.cancellation_estimated else 0.15 for e in members])
            for ax, values in ((ax_h, [e.lch[2] for e in members]),
                               (ax_c, [e.lch[1] for e in members])):  # fmt: skip
                ax.scatter(
                    x, values, c=colour, s=24, edgecolors="#222222", linewidths=0.4,
                    label=f"{name} ({len(members)})", zorder=3,
                )  # fmt: skip
                _annotate(ax, x, values, [_number(e) for e in members])
        ax_h.set_title("Hue angle over time")
        ax_h.set_ylabel("h (°)")
        ax_c.set_title("Chroma over time")
        ax_c.set_ylabel("C*")
        for ax in (ax_h, ax_c):
            ax.set_xlabel("cancellation year")
            ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
        if len(groups) > 1:
            fig.legend(
                *ax_h.get_legend_handles_labels(), loc="lower center",
                ncol=min(4, len(groups)), bbox_to_anchor=(0.5, -0.04),
                title=group_title,
            )  # fmt: skip
            fig.subplots_adjust(bottom=0.3, wspace=0.28)
        return _png(fig)


def _axis_gauge(stamp, sides: tuple[str, str]) -> float | None:
    values = [
        s.gauge
        for name, s in stamp.perforation.effective().items()
        if name in sides and s.kind == "perforated" and s.gauge
    ]
    return float(np.mean(values)) if values else None


def perforation_map(stamps, title: str) -> bytes | None:
    """Horizontal (top/bottom) against vertical (left/right) gauge, one point
    per stamp -- compound perforations sit off the diagonal. ``stamps`` is a
    list of (label_number, stamp, group_name)."""
    points = []
    for number, stamp, group in stamps:
        h = _axis_gauge(stamp, ("top", "bottom"))
        v = _axis_gauge(stamp, ("left", "right"))
        if h is not None and v is not None:
            points.append((number, h, v, group, bool(stamp.perforation.manual)))
    if len(points) < 2:
        return None
    counts = Counter(p[3] for p in points)
    names = [n for n, _ in counts.most_common(MAX_GROUPS)]
    with plt.rc_context(_STYLE):
        fig, ax = plt.subplots(figsize=(6.0, 5.2))
        lo = min(min(p[1], p[2]) for p in points) - 0.5
        hi = max(max(p[1], p[2]) for p in points) + 0.5
        ax.plot([lo, hi], [lo, hi], color="#cccccc", lw=0.8, zorder=1)
        for q in np.arange(np.floor(lo), np.ceil(hi) + 0.01, 0.25):
            if q % 1:
                continue
            ax.axhline(q, color="#eeeeee", lw=0.4, zorder=0)
            ax.axvline(q, color="#eeeeee", lw=0.4, zorder=0)
        legend_handles: list = []
        # Stamps with the same gauges coincide (common with hand-set
        # values): draw one point per position and group, sized by count and
        # labelled with its stamp numbers.
        for i, name in enumerate([*names, None]):
            members = [p for p in points if (p[3] == name if name else p[3] not in names)]
            if not members:
                continue
            colour = PALETTE[i % len(PALETTE)] if name else OTHER
            clusters: dict[tuple, list] = {}
            for p in members:
                clusters.setdefault((round(p[1], 2), round(p[2], 2), p[4]), []).append(p[0])
            # Groups at the same position are spread slightly sideways.
            dodge = (i - (len(names) - 1) / 2) * 0.06
            legend_handles.append(
                matplotlib.lines.Line2D(
                    [], [], marker="o", ls="", markerfacecolor=colour,
                    markeredgecolor="#222222", markersize=6,
                    label=f"{name or 'other'} ({len(members)})",
                )
            )  # fmt: skip
            for (h, v, manual), numbers in clusters.items():
                h = h + dodge
                ax.scatter(
                    [h], [v], c=colour, marker="D" if manual else "o",
                    s=22 + 14 * (len(numbers) - 1) ** 0.8, edgecolors="#222222",
                    linewidths=0.4, zorder=3, alpha=0.85,
                )  # fmt: skip
                # Small clusters name their stamps; big ones just count them
                # (the summary's perforation table has the breakdown).
                text = ",".join(numbers) if len(numbers) <= 3 else f"{len(numbers)}×"
                ax.annotate(
                    text, (h, v), xytext=(5, -2 - 7 * i), textcoords="offset points",
                    fontsize=5, color=colour,
                )  # fmt: skip
        ax.set_xlim(lo, hi)
        ax.set_ylim(lo, hi)
        ax.set_aspect("equal")
        ax.set_xlabel("horizontal gauge (top/bottom mean)")
        ax.set_ylabel("vertical gauge (left/right mean)")
        ax.set_title(title)
        legend_handles.append(
            matplotlib.lines.Line2D(
                [], [], marker="D", ls="", markerfacecolor="white", markeredgecolor="#222222",
                markersize=5, label="set by hand",
            )
        )  # fmt: skip
        ax.legend(handles=legend_handles, loc="upper left", fontsize=6.5)
        return _png(fig)
