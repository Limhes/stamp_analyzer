"""IT8.7/2 patch grid location and RGB extraction (SPEC §4.3).

The user clicks the four corner-patch centres (A1, A22, L1, L22) of the
main 22x12 grid, which is bilinearly interpolated from those four points.
There is no auto-detection in v1.

The GS0-GS23 grey-ramp row is a *separate* physical strip on the Wolf Faust
IT8.7/2 layout, not a simple one-row extrapolation below row L: on batch
R240103 it sits a further ~2 row-pitches below row L (there is a row of
column-number labels between L and GS), and it spans *wider* than the main
grid -- 24 patches at roughly the same pitch as columns 1-22, rather than
22 patches squeezed into the same width. (An earlier version of this module
assumed "one row-pitch below L, same width as columns 1-22"; checked
against a real R240103 scan, that put every sampled GS patch in the plain
grey card stock next to the actual ramp, which is why GS corners are now
required inputs rather than derived.) Pass ``gs0``/``gs23`` on
:class:`IT8Corners`; omitting them falls back to the old extrapolation,
with a warning, for callers (and synthetic tests) that don't have them.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROWS = "ABCDEFGHIJKL"  # 12 rows
N_COLS = 22
N_GS = 24

PatchId = str


@dataclass
class IT8Corners:
    a1: tuple[float, float]
    a22: tuple[float, float]
    l1: tuple[float, float]
    l22: tuple[float, float]
    # Centres of the GS grey-ramp row's first (GS0, brightest) and last
    # (GS23, darkest) patches. Optional for backward compatibility; see the
    # module docstring for why the old extrapolation is unreliable.
    gs0: tuple[float, float] | None = None
    gs23: tuple[float, float] | None = None


@dataclass
class PatchSample:
    patch_id: PatchId
    centre_px: tuple[float, float]
    mean_rgb: tuple[float, float, float]
    std_rgb: tuple[float, float, float]
    n_pixels: int


def build_it8_grid(corners: IT8Corners) -> dict[PatchId, tuple[float, float]]:
    """Bilinearly interpolate patch centres for the full 22x12 grid, and
    linearly interpolate the GS0-GS23 row between its own two corners
    (falling back to the old below-row-L extrapolation if they're absent)."""
    a1, a22, l1, l22 = (
        np.array(corners.a1, dtype=np.float64),
        np.array(corners.a22, dtype=np.float64),
        np.array(corners.l1, dtype=np.float64),
        np.array(corners.l22, dtype=np.float64),
    )

    grid: dict[PatchId, tuple[float, float]] = {}
    n_rows = len(ROWS)
    for ri, row_letter in enumerate(ROWS):
        row_frac = ri / (n_rows - 1)
        for ci in range(N_COLS):
            col_frac = ci / (N_COLS - 1)
            top_point = a1 + (a22 - a1) * col_frac
            bot_point = l1 + (l22 - l1) * col_frac
            point = top_point + (bot_point - top_point) * row_frac
            grid[f"{row_letter}{ci + 1}"] = (float(point[0]), float(point[1]))

    if corners.gs0 is not None and corners.gs23 is not None:
        gs_left = np.array(corners.gs0, dtype=np.float64)
        gs_right = np.array(corners.gs23, dtype=np.float64)
    else:
        row_vec_left = (l1 - a1) / (n_rows - 1)
        row_vec_right = (l22 - a22) / (n_rows - 1)
        gs_left = l1 + row_vec_left
        gs_right = l22 + row_vec_right
    for gi in range(N_GS):
        frac = gi / (N_GS - 1)
        point = gs_left + (gs_right - gs_left) * frac
        grid[f"GS{gi}"] = (float(point[0]), float(point[1]))

    return grid


def _estimate_patch_pitch_px(corners: IT8Corners) -> float:
    a1 = np.array(corners.a1)
    a22 = np.array(corners.a22)
    return float(np.linalg.norm(a22 - a1) / (N_COLS - 1))


def sample_patch(
    image: np.ndarray,
    centre_px: tuple[float, float],
    sample_size_px: float,
    trim_fraction: float = 0.1,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Robust trimmed mean + std of an image region centred at ``centre_px``.

    ``sample_size_px`` is the side length of the square sampling window
    (intended to be the central ~60% of a patch, SPEC §4.3). ``image`` may
    be (H, W) or (H, W, C); returns per-channel arrays.
    """
    half = sample_size_px / 2.0
    x0 = int(round(centre_px[0] - half))
    x1 = int(round(centre_px[0] + half))
    y0 = int(round(centre_px[1] - half))
    y1 = int(round(centre_px[1] + half))
    h, w = image.shape[:2]
    x0, x1 = max(0, x0), min(w, x1)
    y0, y1 = max(0, y0), min(h, y1)
    region = image[y0:y1, x0:x1]
    if region.ndim == 2:
        region = region[..., None]
    region = region.reshape(-1, region.shape[-1]).astype(np.float64)

    n = region.shape[0]
    k = int(n * trim_fraction)
    means = np.empty(region.shape[-1])
    stds = np.empty(region.shape[-1])
    for c in range(region.shape[-1]):
        values = np.sort(region[:, c])
        trimmed = values[k : n - k] if n - 2 * k > 0 else values
        means[c] = trimmed.mean()
        stds[c] = trimmed.std()
    return means, stds, n


def sample_it8_grid(
    image: np.ndarray,
    corners: IT8Corners,
    area_fraction: float = 0.6,
    trim_fraction: float = 0.1,
) -> dict[PatchId, PatchSample]:
    """Sample all 288 IT8 patches given four clicked corner centres."""
    grid = build_it8_grid(corners)
    pitch_px = _estimate_patch_pitch_px(corners)
    sample_size = pitch_px * np.sqrt(area_fraction)

    samples: dict[PatchId, PatchSample] = {}
    for patch_id, centre in grid.items():
        mean_rgb, std_rgb, n = sample_patch(image, centre, sample_size, trim_fraction)
        samples[patch_id] = PatchSample(
            patch_id=patch_id,
            centre_px=centre,
            mean_rgb=tuple(mean_rgb.tolist()) if mean_rgb.size == 3 else (float(mean_rgb[0]),) * 3,
            std_rgb=tuple(std_rgb.tolist()) if std_rgb.size == 3 else (float(std_rgb[0]),) * 3,
            n_pixels=n,
        )
    return samples


def render_diagnostic(
    image: np.ndarray,
    corners: IT8Corners,
    samples: dict[PatchId, PatchSample],
    label_every: int = 1,
) -> bytes:
    """Render the scan with every sampled patch area drawn and labelled."""
    display = np.stack([image] * 3, axis=-1) if image.ndim == 2 else image
    # cv2.normalize avoids promoting the full (often reference-scan-sized,
    # i.e. hundreds of MB) image to a second float64 array just to rescale
    # it into 0-255.
    display8 = cv2.normalize(display, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
    display8 = cv2.cvtColor(display8, cv2.COLOR_RGB2BGR) if display8.shape[-1] == 3 else display8

    pitch_px = _estimate_patch_pitch_px(corners)
    half = int(round(pitch_px * np.sqrt(0.6) / 2.0))

    for i, (patch_id, sample) in enumerate(samples.items()):
        cx, cy = int(round(sample.centre_px[0])), int(round(sample.centre_px[1]))
        cv2.rectangle(display8, (cx - half, cy - half), (cx + half, cy + half), (0, 0, 255), 1)
        if i % label_every == 0:
            cv2.putText(
                display8,
                patch_id,
                (cx - half, cy - half - 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.3,
                (0, 255, 0),
                1,
                cv2.LINE_AA,
            )
    for _corner_name, point in (
        ("A1", corners.a1),
        ("A22", corners.a22),
        ("L1", corners.l1),
        ("L22", corners.l22),
    ):
        cv2.drawMarker(
            display8,
            (int(round(point[0])), int(round(point[1]))),
            (255, 0, 0),
            markerType=cv2.MARKER_CROSS,
            markerSize=14,
            thickness=2,
        )

    ok, buf = cv2.imencode(".png", display8)
    if not ok:
        raise RuntimeError("failed to encode diagnostic image")
    return buf.tobytes()


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
