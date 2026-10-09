"""IT8.7/2 patch layout constants and sample types (SPEC §4.3).

Patch *location* (finding where each of the 288 patches is in a scan) is
not done here any more -- see `core.calibration.scanin`, which shells out
to ArgyllCMS's `scanin` to locate the chart's own printed fiducial marks
and patch grid directly, at René's explicit direction. An earlier version
of this module did that by having the user click 4-6 corner points and
bilinearly interpolating between them; that needed pixel-precise manual
clicks (small errors compounded across the 22x12 grid) and its point
labels were visually ambiguous enough to cause a real misclick (row L
read as row I in the UI font), both of which `scanin`'s own chart
recognition sidesteps entirely. What's left here -- the patch layout
constants and the `PatchSample` result type -- is still shared with
`core.calibration.scanin` and `core.calibration.reference`.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROWS = "ABCDEFGHIJKL"  # 12 rows
N_COLS = 22
N_GS = 24

PatchId = str


@dataclass
class PatchSample:
    patch_id: PatchId
    centre_px: tuple[float, float]
    mean_rgb: tuple[float, float, float]
    std_rgb: tuple[float, float, float]
    n_pixels: int


def render_std_heatmap(samples: dict[PatchId, PatchSample]) -> bytes:
    """A quality-indicator heatmap of per-patch RGB std over the target layout."""
    grid_std = np.full((len(ROWS), N_COLS), np.nan)
    for ri, row_letter in enumerate(ROWS):
        for ci in range(N_COLS):
            s = samples.get(f"{row_letter}{ci + 1}")
            if s:
                grid_std[ri, ci] = float(np.mean(s.std_rgb))
    gs_std = np.array(
        [
            float(np.mean(samples[f"GS{i}"].std_rgb)) if f"GS{i}" in samples else np.nan
            for i in range(N_GS)
        ]
    )

    fig, (ax_main, ax_gs) = plt.subplots(
        2, 1, figsize=(8, 5), gridspec_kw={"height_ratios": [N_COLS / N_COLS * 12, 1.2]}
    )
    im = ax_main.imshow(grid_std, cmap="viridis", aspect="auto")
    ax_main.set_yticks(range(len(ROWS)), list(ROWS))
    ax_main.set_xticks(range(N_COLS), [str(i + 1) for i in range(N_COLS)])
    ax_main.set_title("per-patch RGB std (main grid)")
    fig.colorbar(im, ax=ax_main, fraction=0.03)

    ax_gs.imshow(gs_std[None, :], cmap="viridis", aspect="auto")
    ax_gs.set_yticks([])
    ax_gs.set_xticks(range(N_GS), [str(i) for i in range(N_GS)])
    ax_gs.set_title("GS row")

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    return buf.getvalue()
