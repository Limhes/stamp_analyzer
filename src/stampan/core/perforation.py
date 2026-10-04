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
from dataclasses import dataclass, field

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import find_peaks

from stampan.core.models import (
    CONDITION_ON_PAPER,
    MULTIPLICITY_SINGLE,
    PerforationSide,
)

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
    # The (start_mm, end_mm) interval(s) along the profile actually used for
    # the spectral/hole-regression estimate -- excludes stretches flagged as
    # noisy (cancellation, broken teeth) or a user's manual override.
    roi_mm: list[tuple[float, float]] = field(default_factory=list)
    roi_manual: bool = False

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
            roi_mm=self.roi_mm,
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


def segment_stamp(crop: np.ndarray) -> np.ndarray:
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


def _robust_poly_fit(
    x: np.ndarray, y: np.ndarray, degree: int, n_iter: int = 5, k: float = 2.5
) -> tuple[np.ndarray, np.ndarray]:
    """OLS polynomial fit with iterative sigma-clipping (SPEC §6: "the line
    isn't on the teeth" -- an unweighted fit through the tooth-tip envelope
    is dragged off the true envelope by any stretch of contour corrupted by
    cancellation ink merging with the background, even though that stretch
    is usually a minority of the side's points). Returns (coeffs, inlier
    mask); ``coeffs`` is unchanged from a plain ``np.polyfit`` when nothing
    gets rejected.
    """
    mask = np.ones(len(x), dtype=bool)
    coeffs = np.polyfit(x, y, degree)
    for _ in range(n_iter):
        residuals = y - np.polyval(coeffs, x)
        # A plain std is itself inflated by the very outliers being
        # rejected -- with a contiguous corrupted stretch covering a
        # material fraction of the side (seen on real cancelled stamps,
        # 20%+), that inflation can be enough that nothing ever clears the
        # threshold. MAD (median-based) stays robust up to ~50% outliers.
        median_resid = np.median(residuals[mask])
        mad = np.median(np.abs(residuals[mask] - median_resid))
        scale = mad * 1.4826  # consistent estimator of std under Gaussian inliers
        if scale < 1e-9:
            break
        new_mask = np.abs(residuals - median_resid) <= k * scale
        # Never reject so much that the fit is left with too little to mean
        # anything -- a genuinely corrupted majority is a different problem
        # (ROI exclusion / manual override), not one a line fit can solve.
        if new_mask.sum() < max(degree + 4, int(0.3 * len(x))):
            break
        if np.array_equal(new_mask, mask):
            break
        mask = new_mask
        coeffs = np.polyfit(x[mask], y[mask], degree)
    return coeffs, mask


def _robust_envelope_fit(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Robust tip/bottom envelope fit, preferring a straight line (the
    physically expected shape for a flat rectangular stamp) but allowing a
    gentle quadratic curve when it demonstrably fits much better -- found on
    a real stamp where the envelope was clearly bowed (a ~35% RMS
    improvement from the quadratic term), not just noisy, most likely from
    a residual sub-degree error in the overall crop's rotation-to-upright
    estimate (small, but perforation depth itself is only tenths of a mm).
    A plain per-side line fit cannot absorb a curved bias no matter how
    robust its outlier handling is, since the whole side is biased the same
    smooth way, not a minority stretch -- hence allowing degree 2 here,
    gated on a clear improvement so flat/clean sides still get the simpler,
    more constrained line.
    """
    linear, linear_mask = _robust_poly_fit(x, y, degree=1)
    if len(x) < 25:
        return linear, linear_mask  # too little data to trust a 2nd-order term
    linear_rms = float(np.std((y - np.polyval(linear, x))[linear_mask]))

    quad, quad_mask = _robust_poly_fit(x, y, degree=2)
    quad_rms = float(np.std((y - np.polyval(quad, x))[quad_mask]))

    if linear_rms > 1e-9 and quad_rms < 0.85 * linear_rms:
        return quad, quad_mask
    return linear, linear_mask


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


def detect_stamp_box(crop: np.ndarray) -> np.ndarray:
    """Auto-detected stamp rectangle (4 corners, px, sequential winding) from
    the crop's own segmentation -- this is what ``_edge_frames`` uses
    internally, but also what the GUI seeds its editable perforation-box
    overlay with before the user adjusts it (SPEC §6: "you see the four
    autodetected lines, that the user can then change if necessary")."""
    mask = segment_stamp(crop)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = max(contours, key=cv2.contourArea)
    rect = cv2.minAreaRect(contour)
    return cv2.boxPoints(rect)  # 4 corners, unambiguous regardless of angle convention


def _edge_frames_from_box(box: np.ndarray) -> dict[str, EdgeFrame]:
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


def _edge_frames(mask: np.ndarray) -> dict[str, EdgeFrame]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = max(contours, key=cv2.contourArea)
    rect = cv2.minAreaRect(contour)
    box = cv2.boxPoints(rect)
    return _edge_frames_from_box(box)


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
    manual_roi_mm: list[tuple[float, float]] | None = None,
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

    tip_fit, tip_inliers = _robust_envelope_fit(tip_pts[:, 0], tip_pts[:, 1])
    tooth_roughness_mm = float(
        np.std(tip_pts[tip_inliers, 1] - np.polyval(tip_fit, tip_pts[tip_inliers, 0]))
        / outward_scale
    )
    rejected_frac = 1.0 - tip_inliers.mean()
    if rejected_frac > 0.1:
        warnings.append(
            f"tip-line fit rejected {rejected_frac * 100:.0f}% of the tooth-tip envelope as "
            "outliers (likely cancellation ink or damage distorting the contour there)"
        )

    bottom_fit, _bottom_inliers = _robust_envelope_fit(bottom_pts[:, 0], bottom_pts[:, 1])

    # Depth profile: distance from the tip line to the contour, uniformly
    # resampled in mm along the side.
    depth_px = np.polyval(tip_fit, along_mm) - outward
    s_min, s_max = along_mm.min(), along_mm.max()
    s_grid = np.arange(s_min, s_max, PROFILE_STEP_MM)
    depth_grid_px = np.interp(s_grid, along_mm, depth_px)
    depth_grid_mm = depth_grid_px / outward_scale

    perf_depth_mm = float(np.polyval(bottom_fit, 0) - np.polyval(tip_fit, 0)) / outward_scale

    # Provisional full-profile estimate, used only to size the ROI-detection
    # windows below -- a rough gauge guess is enough for that, and this
    # runs before any noise exclusion so it has to tolerate noisy input.
    provisional_gauge, *_ = _estimate_period(s_grid, depth_grid_mm)
    pitch_hint_mm = 20.0 / provisional_gauge if provisional_gauge else 20.0 / 12.0

    if manual_roi_mm is not None:
        roi_mm = manual_roi_mm
        good_mask = _mask_from_roi(s_grid, roi_mm)
        roi_manual = True
    else:
        good_mask = _detect_profile_roi(s_grid, depth_grid_mm, pitch_hint_mm)
        roi_mm = _roi_runs_from_mask(s_grid, good_mask)
        roi_manual = False
        if roi_mm and not (len(roi_mm) == 1 and roi_mm[0] == (float(s_grid[0]), float(s_grid[-1]))):
            excluded_mm = length_mm - sum(e - s for s, e in roi_mm)
            if excluded_mm > 0.5:
                warnings.append(
                    f"excluded {excluded_mm:.1f}mm of noisy profile (cancellation/broken "
                    "teeth) from the gauge estimate; see the ROI on the diagnostic"
                )
        elif not roi_mm:
            # Nothing passed the minimum-run-length bar; fall back to the
            # full profile rather than estimating from nothing (SPEC: never
            # invent a result, but an empty ROI is worse than an honestly
            # unfiltered one when every stretch is short).
            good_mask = np.ones(len(s_grid), dtype=bool)
            roi_mm = [(float(s_grid[0]), float(s_grid[-1]))]
            warnings.append("ROI detection found no stable stretch; using the full profile")

    result = _estimate_period(s_grid[good_mask], depth_grid_mm[good_mask])
    gauge_spectral, spectral_snr, freqs, mag, peak_freq = result

    holes, gauge_holes, spacing_std, spacing_max_dev = _detect_holes(
        s_grid, depth_grid_mm, gauge_spectral, good_mask=good_mask
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
    # line sits at outward=tip_fit(0) px from edge.origin; its direction in
    # (along, outward) pixel units is (along_scale, tip_fit'(0)) per mm
    # along -- the slope *at the origin*, degree-agnostic (tip_fit may be
    # linear or a gently-bowed quadratic, see _robust_envelope_fit), since
    # this is only a straight dashed line drawn on the diagnostic image.
    tip_origin_px = edge.origin + edge.normal * np.polyval(tip_fit, 0)
    tip_slope_at_origin = np.polyval(np.polyder(tip_fit), 0)
    tip_dir_px = edge.direction * along_scale + edge.normal * tip_slope_at_origin
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
        roi_mm=roi_mm,
        roi_manual=roi_manual,
    )


#: A local noise level more than this multiple of the profile's own median
#: local noise is flagged as an anomalous stretch (cancellation ink reading
#: as background, a torn/broken run of teeth) rather than normal tooth
#: shape -- the "high-amplitude noise on a baseline that should look like a
#: sine wave" René described.
ROI_NOISE_FACTOR = 2.5
#: A detected bad stretch is widened by this many mm each side, so the
#: transition into/out of the anomaly (which itself isn't clean signal)
#: doesn't leak into the kept ROI.
ROI_MARGIN_MM = 0.3
#: A good run shorter than this isn't enough to estimate a period from on
#: its own; such slivers are dropped rather than kept as a near-useless ROI.
ROI_MIN_RUN_MM = 2.5


def _detect_profile_roi(
    s_grid: np.ndarray, depth_mm: np.ndarray, pitch_hint_mm: float
) -> np.ndarray:
    """Per-sample boolean mask: True where the profile looks like normal,
    regular tooth shape; False in anomalous stretches (SPEC §6: cancellation
    ink misread as background, broken/missing teeth).

    Method: a robust local baseline (median filter, window ~1 pitch) gives
    the expected smooth envelope; the residual's local RMS (same window)
    is the "roughness" at each point. A healthy profile's roughness is
    fairly uniform along its length (teeth are teeth), so the profile's own
    median roughness is a robust reference -- no absolute threshold, no
    gauge-accuracy dependency (``pitch_hint_mm`` only sizes the windows, a
    rough guess is enough for that).
    """
    n = len(depth_mm)
    step = s_grid[1] - s_grid[0] if n > 1 else PROFILE_STEP_MM
    window = max(5, int(round(pitch_hint_mm / step)))
    if window % 2 == 0:
        window += 1
    if n < 3 * window:
        return np.ones(n, dtype=bool)  # too short to judge locally; trust it whole

    baseline = median_filter(depth_mm, size=window, mode="nearest")
    residual = depth_mm - baseline
    local_rms = np.sqrt(
        median_filter(residual**2, size=window, mode="nearest")
    )  # a robust (median, not mean) local-energy estimate

    reference = float(np.median(local_rms))
    if reference < 1e-6:
        return np.ones(n, dtype=bool)  # flat/near-zero signal; nothing to flag

    bad = local_rms > reference * ROI_NOISE_FACTOR
    margin_samples = max(1, int(round(ROI_MARGIN_MM / step)))
    bad = np.convolve(bad.astype(np.uint8), np.ones(2 * margin_samples + 1), mode="same") > 0
    return ~bad


def _roi_runs_from_mask(s_grid: np.ndarray, good: np.ndarray) -> list[tuple[float, float]]:
    """Contiguous True runs of ``good``, as (start_mm, end_mm) pairs, with
    runs shorter than ``ROI_MIN_RUN_MM`` dropped."""
    if not np.any(good):
        return []
    edges = np.diff(good.astype(np.int8))
    starts = list(np.where(edges == 1)[0] + 1)
    ends = list(np.where(edges == -1)[0] + 1)
    if good[0]:
        starts = [0, *starts]
    if good[-1]:
        ends = [*ends, len(good)]
    runs = []
    for s, e in zip(starts, ends, strict=True):
        start_mm, end_mm = s_grid[s], s_grid[e - 1]
        if end_mm - start_mm >= ROI_MIN_RUN_MM:
            runs.append((float(start_mm), float(end_mm)))
    return runs


def _mask_from_roi(s_grid: np.ndarray, roi_mm: list[tuple[float, float]]) -> np.ndarray:
    if not roi_mm:
        return np.ones(len(s_grid), dtype=bool)
    mask = np.zeros(len(s_grid), dtype=bool)
    for start, end in roi_mm:
        mask |= (s_grid >= start) & (s_grid <= end)
    return mask


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
    s_grid: np.ndarray,
    depth_mm: np.ndarray,
    gauge_hint: float | None,
    good_mask: np.ndarray | None = None,
) -> tuple[list[HoleDetection], float | None, float | None, float | None]:
    """Peak-find holes on the full profile, then keep only those inside
    ``good_mask`` (the ROI, SPEC §6) for the gauge regression -- a hole
    right at a masked region's edge is still real, just not usable for the
    period fit. Index assignment uses *position*, not detection order
    (``round((s - s0) / pitch)``), so a masked gap or a missed hole
    anywhere doesn't throw off every hole counted after it -- the
    rectangular/periodic assumption René asked to lean on.
    """
    pitch_hint_mm = 20.0 / gauge_hint if gauge_hint else 20.0 / 12.0
    min_distance = max(1, int(0.5 * pitch_hint_mm / PROFILE_STEP_MM))
    amplitude = depth_mm.max() - depth_mm.min()
    if amplitude < 1e-6:
        return [], None, None, None

    peaks, _ = find_peaks(depth_mm, distance=min_distance, prominence=amplitude * 0.25)
    if good_mask is not None:
        peaks = np.array([p for p in peaks if good_mask[p]], dtype=int)
    if len(peaks) < 2:
        return [], None, None, None

    s_peaks = s_grid[peaks]
    holes = [
        HoleDetection(index=i, s_mm=float(s), depth_mm=float(depth_mm[p]), diameter_mm=None)
        for i, (s, p) in enumerate(zip(s_peaks, peaks, strict=True))
    ]

    raw_spacing = np.median(np.diff(s_peaks)) if len(s_peaks) > 1 else pitch_hint_mm
    idx = np.round((s_peaks - s_peaks[0]) / raw_spacing)
    period, origin = np.polyfit(idx, s_peaks, 1)
    gauge_holes = 20.0 / period if period > 0 else None

    # Spacing regularity is only meaningful between *physically adjacent*
    # holes -- a masked gap's jump must not count as one giant "spacing".
    adjacent = np.diff(idx) <= 1.5
    spacings = np.diff(s_peaks)[adjacent]
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
    manual_roi: dict[str, list[tuple[float, float]]] | None = None,
    manual_box_px: np.ndarray | list[tuple[float, float]] | None = None,
    condition: str | None = None,
    multiplicity: str | None = None,
    multiplicity_count: int | None = None,
) -> PerforationAnalysis:
    """Measure perforation gauge on all four sides of an upright stamp crop
    (SPEC §6). ``manual_roi``, keyed by side name, overrides auto ROI
    detection for that side with the given (start_mm, end_mm) interval(s).
    ``manual_box_px``, 4 (x, y) corners in sequential winding order, overrides
    the auto-detected stamp rectangle (``detect_stamp_box``) that every
    side's geometry is built from -- the GUI's editable perforation-box
    overlay, for when the segmentation-derived rectangle doesn't actually
    hug the stamp (e.g. cancellation ink merging with a dark mount/paper
    background corrupts the outer contour).

    ``condition``/``multiplicity``/``multiplicity_count`` are ``Stamp``'s
    own catalogue fields (SPEC §12), threaded through here because they
    bear directly on how trustworthy a measured gauge is, even though
    neither changes the measurement itself:

    - ``condition == CONDITION_ON_PAPER``: the backing paper/mount's own
      torn or cut edge can run close to, or merge with, the stamp's true
      edge in the segmentation mask, the same way cancellation ink merging
      with a dark background corrupts a contour (``segment_stamp``'s own
      docstring). There is no reliable way to separate "paper's edge" from
      "stamp's edge" automatically here, so rather than silently returning
      a number that may actually be describing the paper, every side's
      confidence is capped and an explicit warning is attached -- "say so
      instead of returning a plausible-looking number" (CLAUDE.md).
    - ``multiplicity`` other than single: a pair/strip/block shares
      perforation lines between neighbours, so a side classified here as
      ``imperf``/``straight_edge`` is not necessarily the stamp's own
      natural edge -- it may be the (unmeasured) line shared with a
      neighbour that this crop simply doesn't show. Flagged as a warning
      rather than guessed at, since which side(s) are shared isn't
      information this function has.
    """
    mask = segment_stamp(crop)
    contour_px = _outer_contour(mask)
    if manual_box_px is not None:
        edge_frames = _edge_frames_from_box(np.asarray(manual_box_px, dtype=np.float64))
    else:
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
            side,
            contour_px[sel],
            edge_frames[side],
            px_per_mm_x,
            px_per_mm_y,
            corner_exclusion_mm,
            manual_roi_mm=(manual_roi or {}).get(side),
        )

    on_paper = condition == CONDITION_ON_PAPER
    if on_paper:
        on_paper_warning = (
            "stamp is on paper: the backing paper/mount's own edge can "
            "contaminate the segmentation near the stamp's true edge the "
            "same way cancellation ink does -- gauge values below are "
            "provisional, confidence capped"
        )
        for side in sides.values():
            if side.confidence > 0.0:
                side.confidence = min(side.confidence, 0.5)
                side.warnings.append(on_paper_warning)

    grouped = multiplicity is not None and multiplicity != MULTIPLICITY_SINGLE
    multiplicity_warning = None
    if grouped:
        count_str = f" of {multiplicity_count}" if multiplicity_count else ""
        multiplicity_warning = (
            f"stamp is recorded as a {multiplicity}{count_str}: a side "
            "classified as imperf/straight_edge here may actually be a "
            "perforation line shared with a neighbouring stamp, not this "
            "stamp's own natural edge -- which side(s) are shared is not "
            "determined automatically"
        )

    confidences = [s.confidence for s in sides.values()]
    overall_warnings = [w for s in sides.values() for w in s.warnings]
    if multiplicity_warning:
        overall_warnings.append(multiplicity_warning)
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


def _shade_roi(ax, side: SideAnalysis) -> None:
    """Shade the profile's excluded (non-ROI) stretches in red and the
    kept ROI in green, so a noisy/masked region is visible on the plot
    itself (SPEC §6), not just implied by the gauge number."""
    if not len(side.profile_s_mm):
        return
    s_min, s_max = float(side.profile_s_mm[0]), float(side.profile_s_mm[-1])
    roi = sorted(side.roi_mm) if side.roi_mm else [(s_min, s_max)]
    cursor = s_min
    for start, end in roi:
        if start > cursor:
            ax.axvspan(cursor, start, color="red", alpha=0.12, lw=0)
        ax.axvspan(start, end, color="green", alpha=0.08, lw=0)
        cursor = max(cursor, end)
    if cursor < s_max:
        ax.axvspan(cursor, s_max, color="red", alpha=0.12, lw=0)


def render_side_profile(side: SideAnalysis) -> bytes:
    """One side's profile plot alone (no spectrum, no stamp photo) -- for
    the GUI's per-side ROI range-slider, drawn directly under this image so
    the user can see exactly what stretch they're selecting (SPEC §6: "I
    would like to select a region from that plot to run the fft on")."""
    fig, ax = plt.subplots(figsize=(5.0, 1.8))
    if len(side.profile_s_mm):
        ax.plot(side.profile_s_mm, side.profile_depth_mm, lw=0.6)
        for h in side.holes:
            ax.axvline(h.s_mm, color="red", lw=0.3, alpha=0.5)
        _shade_roi(ax, side)
        ax.set_xlim(float(side.profile_s_mm[0]), float(side.profile_s_mm[-1]))
    ax.set_title(f"{side.side} profile ({side.hole_count} holes)", fontsize=8)
    ax.tick_params(labelsize=6)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    return buf.getvalue()


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
            _shade_roi(ax_p, side)
        roi_tag = " [manual ROI]" if side.roi_manual else ""
        ax_p.set_title(f"{side_name} profile ({side.hole_count} holes){roi_tag}", fontsize=8)
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
