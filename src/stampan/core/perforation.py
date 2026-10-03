"""Perforation gauge measurement (SPEC §6). The core metrology module.

Pipeline per side: segment the stamp against its black margin, get the
outer contour at sub-pixel precision, fit robust tip (tooth-tip) and bottom
(hole-bottom) envelope lines per side with corner zones excluded, build a
1-D indentation profile sampled uniformly in mm along the side, and
estimate the period with two independent methods -- (A) spectral (FFT) and
(B) hole-position regression -- which must agree for high confidence.

Caveat on anisotropic scale: ``px_per_mm_x``/``px_per_mm_y`` here are the
crop's own local axes. If the crop was rotated to upright from a sample
sheet where the scanner's x and y px/mm genuinely differ, a side that runs
at an angle to the original sensor axes should strictly use a
direction-dependent effective scale, not a simple per-axis one. This module
does not do that (it assumes the crop's local x/y directions coincide with
the calibration's x/y, which holds exactly when the stamp wasn't rotated,
and approximately for small rotations or near-isotropic scale -- true for
every reference built so far, since isotropic scale was assumed). Flagged
here rather than silently ignored; revisit if a reference is ever built
with a materially anisotropic x/y scale.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import find_peaks

from stampan.core.models import PerforationSide

SIDES = ("top", "bottom", "left", "right")

GAUGE_MIN = 6.0
GAUGE_MAX = 18.0
DEFAULT_CORNER_EXCLUSION_MM = 3.5  # >= one pitch at the lowest plausible gauge
PROFILE_STEP_MM = 0.01


@dataclass
class HoleDetection:
    index: int
    s_mm: float  # position along the side
    depth_mm: float
    diameter_mm: float | None


@dataclass
class SideAnalysis:
    """Full internal result for one side, including diagnostic arrays."""

    side: str
    kind: str  # "perforated" | "imperf" | "straight_edge"
    gauge_spectral: float | None
    gauge_holes: float | None
    gauge: float | None
    uncertainty: float | None
    notation: str | None
    hole_count: int
    length_mm: float
    spacing_std_mm: float | None
    spacing_max_dev_mm: float | None
    hole_diameter_mm: float | None
    tooth_roughness_mm: float | None
    confidence: float
    warnings: list[str]
    profile_s_mm: np.ndarray
    profile_depth_mm: np.ndarray
    holes: list[HoleDetection]
    spectrum_freq_cyc_per_mm: np.ndarray
    spectrum_mag: np.ndarray
    spectral_peak_freq: float | None
    tip_line: tuple[np.ndarray, np.ndarray]  # (origin_px, unit_direction_px)
    bottom_line: tuple[np.ndarray, np.ndarray] | None

    def to_persisted(self) -> PerforationSide:
        return PerforationSide(
            kind=self.kind,
            gauge=self.gauge,
            uncertainty=self.uncertainty,
            notation=self.notation,
            hole_count=self.hole_count,
            length_mm=self.length_mm,
            spacing_std_mm=self.spacing_std_mm,
            spacing_max_dev_mm=self.spacing_max_dev_mm,
            hole_diameter_mm=self.hole_diameter_mm,
            tooth_roughness=self.tooth_roughness_mm,
        )


@dataclass
class PerforationAnalysis:
    sides: dict[str, SideAnalysis]
    confidence: float
    warnings: list[str]
    contour_px: np.ndarray  # (N, 2) outer contour, for diagnostics


def _notation(gauge: float) -> str:
    quarter = round(gauge * 4) / 4
    whole = int(quarter)
    frac = quarter - whole
    frac_str = {0.0: "", 0.25: " 1/4", 0.5: " 1/2", 0.75: " 3/4"}[round(frac, 2)]
    return f"{whole}{frac_str}"


def _segment(crop: np.ndarray) -> np.ndarray:
    """Foreground (stamp) mask. Perforation holes are deliberately *not*
    closed (they are tens of px across and are exactly what this module
    measures) -- but a tiny morphological closing (a few px) is applied to
    bridge thin dark cancellation-ink strokes that a global threshold can
    otherwise misread as background, pinching a channel from the stamp's
    interior out to its edge and corrupting the outer contour. Found on a
    real cancelled stamp: a cancellation mark passing close to the top
    edge connected to the background through the ink, producing a
    contaminated contour with a bogus "hole count" where the mark was.
    """
    intensity = crop.astype(np.float64).max(axis=-1) if crop.ndim == 3 else crop.astype(np.float64)
    smoothed = cv2.GaussianBlur(intensity, (0, 0), 1.0)
    smoothed8 = cv2.normalize(smoothed, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, mask = cv2.threshold(smoothed8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return mask
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return np.where(labels == largest, 255, 0).astype(np.uint8)


def _polygon_area(points: np.ndarray) -> float:
    """Shoelace formula, for picking the true outer boundary by enclosed
    area rather than by point count (see _outer_contour)."""
    x, y = points[:, 0], points[:, 1]
    return float(0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))))


def _outer_contour(mask: np.ndarray) -> np.ndarray:
    """Sub-pixel outer contour (N, 2) in (x, y) pixel coordinates.

    Picks the contour with the largest *enclosed area*, not the one with
    the most points: a heavy cancellation mark whose ink reads as
    background-dark can pinch a thin, jagged path from the stamp's
    interior into the segmentation mask that has more sample points than
    the smooth true outer rectangle, even though it encloses far less
    area. Found on a real cancelled stamp.
    """
    from skimage.measure import find_contours

    float_mask = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (0, 0), 0.6)
    contours = find_contours(float_mask, level=0.5)
    largest = max(contours, key=_polygon_area)
    # skimage returns (row, col) = (y, x); convert to (x, y).
    return largest[:, ::-1]


@dataclass
class EdgeFrame:
    """One side's own local geometry, built from the rotated bounding box's
    corners rather than cv2.minAreaRect's angle/width/height triple (whose
    width/height-vs-angle convention is ambiguous near multiples of 90 deg,
    which silently swapped top/bottom in early testing here)."""

    side: str
    origin: np.ndarray  # a point on the nominal (un-perforated) edge line, px
    direction: np.ndarray  # unit vector along the edge, px space
    normal: np.ndarray  # unit vector, points outward from the stamp centre
    half_length_px: float

    def project(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(along_px, outward_px) for each point, relative to this edge."""
        rel = points - self.origin
        along = rel @ self.direction
        outward = rel @ self.normal
        return along, outward


def _edge_frames(mask: np.ndarray) -> dict[str, EdgeFrame]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = max(contours, key=cv2.contourArea)
    rect = cv2.minAreaRect(contour)
    box = cv2.boxPoints(rect)  # 4 corners, unambiguous regardless of angle convention
    centre = box.mean(axis=0)

    raw_edges = []
    for i in range(4):
        p0, p1 = box[i], box[(i + 1) % 4]
        mid = (p0 + p1) / 2.0
        vec = p1 - p0
        length = float(np.linalg.norm(vec))
        direction = vec / length
        normal = np.array([direction[1], -direction[0]])
        if np.dot(normal, mid - centre) < 0:
            normal = -normal
        raw_edges.append((mid, direction, normal, length / 2.0))

    horizontal = [e for e in raw_edges if abs(e[1][0]) >= abs(e[1][1])]
    vertical = [e for e in raw_edges if abs(e[1][0]) < abs(e[1][1])]
    horizontal.sort(key=lambda e: e[0][1])  # by mid.y: smaller y = top
    vertical.sort(key=lambda e: e[0][0])  # by mid.x: smaller x = left

    names = {
        "top": horizontal[0],
        "bottom": horizontal[-1],
        "left": vertical[0],
        "right": vertical[-1],
    }
    return {
        name: EdgeFrame(
            side=name, origin=mid, direction=direction, normal=normal, half_length_px=hl
        )
        for name, (mid, direction, normal, hl) in names.items()
    }


def _classify_side(points: np.ndarray, frames: dict[str, EdgeFrame]) -> np.ndarray:
    """Nearest edge LINE (by perpendicular distance) for each contour point."""
    dists = np.stack([np.abs(f.project(points)[1]) for f in frames.values()], axis=1)
    names = np.array(list(frames.keys()))
    return names[np.argmin(dists, axis=1)]


def analyse_side(
    side: str,
    points_px: np.ndarray,
    edge: EdgeFrame,
    px_per_mm_x: float,
    px_per_mm_y: float,
    corner_exclusion_mm: float = DEFAULT_CORNER_EXCLUSION_MM,
) -> SideAnalysis:
    along_scale = px_per_mm_x if side in ("top", "bottom") else px_per_mm_y
    outward_scale = px_per_mm_y if side in ("top", "bottom") else px_per_mm_x
    warnings: list[str] = []

    along, outward = edge.project(points_px)
    along_mm = along / along_scale
    length_mm = 2 * edge.half_length_px / along_scale

    keep = np.abs(along_mm) <= (length_mm / 2.0 - corner_exclusion_mm)
    if keep.sum() < 10:
        keep = np.ones_like(along_mm, dtype=bool)  # too short a side to exclude corners
        warnings.append("side too short for corner exclusion; using full extent")

    along_mm, outward = along_mm[keep], outward[keep]
    order = np.argsort(along_mm)
    along_mm, outward = along_mm[order], outward[order]

    # Bin along the side and take the outward-most point per bin as the tip
    # envelope, and the inward-most as the bottom envelope.
    bin_mm = 0.15
    bins = np.round(along_mm / bin_mm).astype(int)
    tip_pts, bottom_pts = [], []
    for b in np.unique(bins):
        sel = bins == b
        a, o = along_mm[sel], outward[sel]
        tip_pts.append((a[np.argmax(o)], o.max()))
        bottom_pts.append((a[np.argmin(o)], o.min()))
    tip_pts = np.array(tip_pts)
    bottom_pts = np.array(bottom_pts)

    tip_fit = np.polyfit(tip_pts[:, 0], tip_pts[:, 1], 1)
    tooth_roughness_mm = float(
        np.std(tip_pts[:, 1] - np.polyval(tip_fit, tip_pts[:, 0])) / outward_scale
    )

    bottom_fit = np.polyfit(bottom_pts[:, 0], bottom_pts[:, 1], 1)

    # Depth profile: distance from the tip line to the contour, uniformly
    # resampled in mm along the side.
    depth_px = np.polyval(tip_fit, along_mm) - outward
    s_min, s_max = along_mm.min(), along_mm.max()
    s_grid = np.arange(s_min, s_max, PROFILE_STEP_MM)
    depth_grid_px = np.interp(s_grid, along_mm, depth_px)
    depth_grid_mm = depth_grid_px / outward_scale

    perf_depth_mm = float(np.polyval(bottom_fit, 0) - tip_fit[1]) / outward_scale

    result = _estimate_period(s_grid, depth_grid_mm)
    gauge_spectral, spectral_snr, freqs, mag, peak_freq = result

    holes, gauge_holes, spacing_std, spacing_max_dev = _detect_holes(
        s_grid, depth_grid_mm, gauge_spectral
    )

    hole_diameters = [h.diameter_mm for h in holes if h.diameter_mm is not None]
    hole_diameter_mm = float(np.mean(hole_diameters)) if hole_diameters else None

    kind, gauge, uncertainty, confidence = _classify_and_pick(
        gauge_spectral,
        spectral_snr,
        gauge_holes,
        holes,
        depth_grid_mm,
        perf_depth_mm,
        warnings,
    )

    notation = _notation(gauge) if gauge is not None else None

    # Tip line back in pixel space, for diagnostics: at along_mm=0 the tip
    # line sits at outward=tip_fit[1] px from edge.origin; its direction in
    # (along, outward) pixel units is (along_scale, tip_fit[0]) per mm along.
    tip_origin_px = edge.origin + edge.normal * tip_fit[1]
    tip_dir_px = edge.direction * along_scale + edge.normal * tip_fit[0]
    tip_dir_px = tip_dir_px / np.linalg.norm(tip_dir_px)

    return SideAnalysis(
        side=side,
        kind=kind,
        gauge_spectral=gauge_spectral,
        gauge_holes=gauge_holes,
        gauge=gauge,
        uncertainty=uncertainty,
        notation=notation,
        hole_count=len(holes),
        length_mm=length_mm,
        spacing_std_mm=spacing_std,
        spacing_max_dev_mm=spacing_max_dev,
        hole_diameter_mm=hole_diameter_mm,
        tooth_roughness_mm=tooth_roughness_mm,
        confidence=confidence,
        warnings=warnings,
        profile_s_mm=s_grid,
        profile_depth_mm=depth_grid_mm,
        holes=holes,
        spectrum_freq_cyc_per_mm=freqs,
        spectrum_mag=mag,
        spectral_peak_freq=peak_freq,
        tip_line=(tip_origin_px, tip_dir_px),
        bottom_line=None,
    )


def _estimate_period(
    s_grid: np.ndarray, depth_mm: np.ndarray
) -> tuple[float | None, float, np.ndarray, np.ndarray, float | None]:
    """Spectral (FFT) period estimate. Returns (gauge, SNR, freqs, magnitude,
    peak_freq)."""
    n = len(depth_mm)
    if n < 20:
        return None, 0.0, np.array([]), np.array([]), None

    detrended = depth_mm - np.mean(depth_mm)
    window = np.hanning(n)
    padded_n = int(2 ** np.ceil(np.log2(n * 8)))
    spectrum = np.fft.rfft(detrended * window, n=padded_n)
    freqs = np.fft.rfftfreq(padded_n, d=PROFILE_STEP_MM)
    mag = np.abs(spectrum)

    band = (freqs >= 1.0 / (20.0 / GAUGE_MIN)) & (freqs <= 1.0 / (20.0 / GAUGE_MAX))
    if not np.any(band):
        return None, 0.0, freqs, mag, None

    band_idx = np.where(band)[0]
    peak_rel = int(np.argmax(mag[band_idx]))
    peak_idx = band_idx[peak_rel]

    # Parabolic refinement in the magnitude around the peak bin.
    if 0 < peak_idx < len(mag) - 1:
        y0, y1, y2 = mag[peak_idx - 1], mag[peak_idx], mag[peak_idx + 1]
        denom = y0 - 2 * y1 + y2
        offset = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-12 else 0.0
        offset = float(np.clip(offset, -1.0, 1.0))
    else:
        offset = 0.0
    bin_width = freqs[1] - freqs[0]
    peak_freq = freqs[peak_idx] + offset * bin_width

    background = np.median(mag[band_idx])
    snr = float(mag[peak_idx] / background) if background > 0 else 0.0
    gauge = peak_freq * 20.0
    return gauge, snr, freqs, mag, peak_freq


def _detect_holes(
    s_grid: np.ndarray, depth_mm: np.ndarray, gauge_hint: float | None
) -> tuple[list[HoleDetection], float | None, float | None, float | None]:
    pitch_hint_mm = 20.0 / gauge_hint if gauge_hint else 20.0 / 12.0
    min_distance = max(1, int(0.5 * pitch_hint_mm / PROFILE_STEP_MM))
    amplitude = depth_mm.max() - depth_mm.min()
    if amplitude < 1e-6:
        return [], None, None, None

    peaks, _ = find_peaks(depth_mm, distance=min_distance, prominence=amplitude * 0.25)
    if len(peaks) < 2:
        return [], None, None, None

    s_peaks = s_grid[peaks]
    holes = [
        HoleDetection(index=i, s_mm=float(s), depth_mm=float(depth_mm[p]), diameter_mm=None)
        for i, (s, p) in enumerate(zip(s_peaks, peaks, strict=True))
    ]

    idx = np.arange(len(s_peaks))
    period, origin = np.polyfit(idx, s_peaks, 1)
    gauge_holes = 20.0 / period if period > 0 else None

    spacings = np.diff(s_peaks)
    spacing_std = float(np.std(spacings)) if len(spacings) else None
    spacing_max_dev = float(np.max(np.abs(spacings - period))) if len(spacings) else None

    # Hole diameter: full width at half depth around each peak, in the profile.
    half_prominences = []
    for p in peaks:
        base = depth_mm[max(0, p - min_distance) : p + min_distance]
        local_min = base.min() if len(base) else 0.0
        half = (depth_mm[p] + local_min) / 2.0
        left = p
        while left > 0 and depth_mm[left] > half:
            left -= 1
        right = p
        while right < len(depth_mm) - 1 and depth_mm[right] > half:
            right += 1
        width_mm = (right - left) * PROFILE_STEP_MM
        half_prominences.append(width_mm)
    for h, w in zip(holes, half_prominences, strict=True):
        h.diameter_mm = w

    return holes, gauge_holes, spacing_std, spacing_max_dev


def _classify_and_pick(
    gauge_spectral: float | None,
    spectral_snr: float,
    gauge_holes: float | None,
    holes: list[HoleDetection],
    depth_mm: np.ndarray,
    perf_depth_mm: float,
    warnings: list[str],
) -> tuple[str, float | None, float | None, float]:
    amplitude = float(depth_mm.max() - depth_mm.min()) if len(depth_mm) else 0.0

    # Real perforation holes cut substantially into the paper (tenths of a
    # mm at least); this check must run *before* trusting the spectral SNR,
    # because a flat edge's sub-pixel rendering/scan texture can present a
    # small but spectrally "clean" false periodicity (seen on a rotated
    # synthetic straight edge: ~0.05 mm amplitude, SNR > 3, landing
    # plausibly in the 6-18 gauge band) that a periodicity-only check will
    # not catch.
    MIN_PERFORATION_AMPLITUDE_MM = 0.08
    if amplitude < MIN_PERFORATION_AMPLITUDE_MM:
        warnings.append(
            f"amplitude {amplitude:.3f}mm too small for real perforation "
            "(sub-pixel edge texture, not holes): classified as straight edge"
        )
        return "straight_edge", None, None, 0.9

    # len(holes) < 3: np.polyfit(..., cov=True) below needs more points than
    # the fit order (1) to estimate a covariance at all; found by a crash on
    # a real, badly torn stamp edge with only 2 spurious "holes" detected.
    not_periodic = spectral_snr < 3.0 or gauge_spectral is None or len(holes) < 3
    if not_periodic:
        warnings.append("no periodic structure detected: classified as imperforate")
        return "imperf", None, None, 0.8

    if gauge_holes is None:
        warnings.append("holes detected but regression failed; using spectral estimate only")
        return "perforated", gauge_spectral, None, 0.4

    agree = abs(gauge_spectral - gauge_holes) <= 0.1
    confidence = 1.0
    if not agree:
        warnings.append(
            f"spectral ({gauge_spectral:.2f}) and hole-position ({gauge_holes:.2f}) "
            "estimates disagree by > 0.1 gauge"
        )
        confidence *= 0.5
    if len(holes) < 8:
        warnings.append(f"only {len(holes)} holes; gauge less reliable")
        confidence *= 0.6
    elif len(holes) < 15:
        # Still usable (SPEC's own accuracy requirement only applies from 10
        # holes up), but synthetic validation shows the regression's
        # variance is still non-negligible in the 10-15 range under
        # realistic noise -- a real stamp with a short, high-gauge side
        # lands here often, so don't report full confidence for it.
        warnings.append(f"only {len(holes)} holes; gauge moderately reliable")
        confidence *= 0.85
    if spectral_snr < 5.0:
        confidence *= 0.7

    # Use the hole-position regression as the primary value: it carries a
    # proper per-side standard error and isn't biased by spectral leakage.
    idx = np.arange(len(holes))
    s_peaks = np.array([h.s_mm for h in holes])
    (period, _), cov = np.polyfit(idx, s_peaks, 1, cov=True)
    se_period = float(np.sqrt(cov[0, 0]))
    gauge = 20.0 / period
    uncertainty = 20.0 * se_period / period**2

    return "perforated", gauge, uncertainty, max(0.0, min(1.0, confidence))


def analyse_perforation(
    crop: np.ndarray,
    px_per_mm_x: float,
    px_per_mm_y: float,
    corner_exclusion_mm: float = DEFAULT_CORNER_EXCLUSION_MM,
) -> PerforationAnalysis:
    """Measure perforation gauge on all four sides of an upright stamp crop
    (SPEC §6)."""
    mask = _segment(crop)
    contour_px = _outer_contour(mask)
    edge_frames = _edge_frames(mask)
    side_labels = _classify_side(contour_px, edge_frames)

    sides: dict[str, SideAnalysis] = {}
    for side in SIDES:
        sel = side_labels == side
        if sel.sum() < 10:
            sides[side] = SideAnalysis(
                side=side,
                kind="imperf",
                gauge_spectral=None,
                gauge_holes=None,
                gauge=None,
                uncertainty=None,
                notation=None,
                hole_count=0,
                length_mm=0.0,
                spacing_std_mm=None,
                spacing_max_dev_mm=None,
                hole_diameter_mm=None,
                tooth_roughness_mm=None,
                confidence=0.0,
                warnings=["too few contour points for this side"],
                profile_s_mm=np.array([]),
                profile_depth_mm=np.array([]),
                holes=[],
                spectrum_freq_cyc_per_mm=np.array([]),
                spectrum_mag=np.array([]),
                spectral_peak_freq=None,
                tip_line=(edge_frames[side].origin, edge_frames[side].direction),
                bottom_line=None,
            )
            continue
        sides[side] = analyse_side(
            side, contour_px[sel], edge_frames[side], px_per_mm_x, px_per_mm_y, corner_exclusion_mm
        )

    confidences = [s.confidence for s in sides.values()]
    overall_warnings = [w for s in sides.values() for w in s.warnings]
    return PerforationAnalysis(
        sides=sides,
        confidence=float(np.mean(confidences)) if confidences else 0.0,
        warnings=overall_warnings,
        contour_px=contour_px,
    )


def perforation_result_notation(sides: dict[str, SideAnalysis]) -> str:
    """Compound notation, e.g. '11 1/2 x 12' (top/bottom x left/right)."""

    def fmt(side_names: tuple[str, str]) -> str:
        a, b = sides[side_names[0]], sides[side_names[1]]
        vals = []
        for s in (a, b):
            if s.kind == "imperf":
                vals.append("imperf")
            elif s.kind == "straight_edge":
                vals.append("straight edge")
            elif s.notation:
                vals.append(s.notation)
        if a.kind == b.kind == "perforated" and a.notation == b.notation:
            return a.notation or "?"
        return "/".join(vals) if vals else "?"

    horiz = fmt(("top", "bottom"))
    vert = fmt(("left", "right"))
    return f"{horiz} x {vert}"


# --------------------------------------------------------------------------
# Manual mode
# --------------------------------------------------------------------------


def manual_side_from_points(
    points_px: list[tuple[float, float]], px_per_mm_along: float
) -> tuple[float, float]:
    """Gauge (and its uncertainty) from user-clicked tooth-tip or hole-centre
    points along one side, via the same regression as estimator (B)."""
    pts = np.array(points_px)
    d = np.diff(pts, axis=0)
    s = np.concatenate([[0.0], np.cumsum(np.hypot(d[:, 0], d[:, 1]))]) / px_per_mm_along
    idx = np.arange(len(s))
    (period, _), cov = np.polyfit(idx, s, 1, cov=True)
    se_period = float(np.sqrt(cov[0, 0])) if len(s) > 2 else float("nan")
    gauge = 20.0 / period
    uncertainty = 20.0 * se_period / period**2 if np.isfinite(se_period) else float("nan")
    return gauge, uncertainty


# --------------------------------------------------------------------------
# Diagnostics (SPEC §6.3)
# --------------------------------------------------------------------------


def render_diagnostic(crop: np.ndarray, analysis: PerforationAnalysis) -> bytes:
    """Annotated crop with fitted lines, detected holes, and per-side
    profile/spectrum mini-plots."""
    display = crop.astype(np.float64)
    display = (display / max(display.max(), 1) * 255).astype(np.uint8)
    if display.ndim == 2:
        display = cv2.cvtColor(display, cv2.COLOR_GRAY2RGB)

    n_sides = len(analysis.sides)
    fig = plt.figure(figsize=(10, 5 + 2.2 * n_sides))
    gs = fig.add_gridspec(1 + n_sides, 2, height_ratios=[4] + [1] * n_sides)

    ax_img = fig.add_subplot(gs[0, :])
    ax_img.imshow(display)
    ax_img.plot(analysis.contour_px[:, 0], analysis.contour_px[:, 1], "c-", lw=0.5)

    for side_name, side in analysis.sides.items():
        colour = {"perforated": "lime", "imperf": "yellow", "straight_edge": "orange"}[side.kind]
        origin, direction = side.tip_line
        p0 = origin - direction * 2000
        p1 = origin + direction * 2000
        ax_img.plot([p0[0], p1[0]], [p0[1], p1[1]], color=colour, lw=1, ls="--")
        label = (
            f"{side_name}: {side.notation}"
            if side.kind == "perforated"
            else f"{side_name}: {side.kind}"
        )
        ax_img.text(
            origin[0],
            origin[1],
            label,
            color=colour,
            fontsize=8,
            ha="center",
            va="center",
            backgroundcolor="black",
        )
    ax_img.set_xlim(0, display.shape[1])
    ax_img.set_ylim(display.shape[0], 0)
    ax_img.set_title(f"overall confidence {analysis.confidence:.2f}")
    ax_img.axis("off")

    for i, (side_name, side) in enumerate(analysis.sides.items()):
        ax_p = fig.add_subplot(gs[1 + i, 0])
        if len(side.profile_s_mm):
            ax_p.plot(side.profile_s_mm, side.profile_depth_mm, lw=0.6)
            for h in side.holes:
                ax_p.axvline(h.s_mm, color="red", lw=0.3, alpha=0.5)
        ax_p.set_title(f"{side_name} profile ({side.hole_count} holes)", fontsize=8)
        ax_p.set_xlabel("mm", fontsize=7)
        ax_p.tick_params(labelsize=6)

        ax_s = fig.add_subplot(gs[1 + i, 1])
        if len(side.spectrum_freq_cyc_per_mm):
            gauges = side.spectrum_freq_cyc_per_mm * 20.0
            band = (gauges >= GAUGE_MIN) & (gauges <= GAUGE_MAX)
            ax_s.plot(gauges[band], side.spectrum_mag[band], lw=0.6)
            if side.spectral_peak_freq:
                ax_s.axvline(side.spectral_peak_freq * 20.0, color="red", lw=0.5)
        ax_s.set_title(f"{side_name} spectrum (gauge axis)", fontsize=8)
        ax_s.tick_params(labelsize=6)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    return buf.getvalue()
