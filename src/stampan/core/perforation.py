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
from scipy.signal import butter, find_peaks, sosfiltfilt

from stampan.core.models import (
    CONDITION_ON_PAPER,
    MULTIPLICITY_SINGLE,
    PerforationSide,
)

SIDES = ("top", "bottom", "left", "right")

GAUGE_MIN = 6.0
GAUGE_MAX = 18.0
#: The gauge-range slider's starting selection (SPEC §6) -- most
#: definitive-issue perforations fall in this band, a better default to
#: land on than the full physically-plausible GAUGE_MIN-GAUGE_MAX extent.
#: Shared between the GUI and the CLI's bulk re-measure so both land on
#: the same starting point for a stamp that's never been measured before.
DEFAULT_GAUGE_RANGE = (10.0, 15.0)
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
    gauge_range: tuple[float, float] = (GAUGE_MIN, GAUGE_MAX)


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
    gauge_range: tuple[float, float] = (GAUGE_MIN, GAUGE_MAX),
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

    # The tip line is the box edge itself -- SPEC §6: "assume rectangular
    # stamps", and the box is how the user asserts "this edge is right"
    # (drawn by auto-detection, or dragged by hand to visually hug the
    # tooth tips). It used to be independently re-fit from the contour's
    # own outermost points per side, which could drift away from the box
    # at an angle -- "the edges of the box should be identical to the
    # profile lines" -- especially on a side with cancellation ink or
    # damage distorting the contour, exactly the stretches a robust fit
    # was least able to anchor reliably. In this edge's own (along,
    # outward) coordinates the box edge is simply outward=0 for every
    # along -- a degree-1 fit with zero slope and zero intercept.
    tip_fit = np.array([0.0, 0.0])
    mean_tip_offset_mm = float(np.mean(tip_pts[:, 1])) / outward_scale
    tooth_roughness_mm = float(np.std(tip_pts[:, 1])) / outward_scale
    if abs(mean_tip_offset_mm) > 0.15:
        where = "outside" if mean_tip_offset_mm > 0 else "inside"
        warnings.append(
            f"tooth tips sit {abs(mean_tip_offset_mm):.2f}mm {where} the drawn box edge on "
            "average; depth/gauge are measured from the box edge, so consider nudging it to "
            "better hug the tips"
        )

    bottom_fit, _bottom_inliers = _robust_envelope_fit(bottom_pts[:, 0], bottom_pts[:, 1])

    # Depth profile: distance from the tip line (the box edge) to the
    # contour, uniformly resampled in mm along the side.
    depth_px = np.polyval(tip_fit, along_mm) - outward
    s_min, s_max = along_mm.min(), along_mm.max()
    s_grid = np.arange(s_min, s_max, PROFILE_STEP_MM)
    depth_grid_px = np.interp(s_grid, along_mm, depth_px)
    depth_grid_raw_mm = depth_grid_px / outward_scale

    # Low-pass filter: the raw profile is built from individual contour
    # points, which carry pixel/sub-pixel-scale extraction jitter (a
    # staircase-y mask boundary, scan noise, cancellation-ink edge
    # texture) with no physical meaning -- real teeth simply cannot
    # oscillate faster than the finest gauge the user says is plausible
    # (``gauge_range``'s upper end), so anything faster is unambiguously
    # noise, not signal, and can be removed outright rather than merely
    # damped. This is exactly the "so much noise, even though the picture
    # is really quite nice... filter out all the high frequency crap"
    # profile plots were showing (SPEC §6): a real photo at gauge ~12.5
    # was clearly periodic by eye, but a flat Gaussian smooth too small
    # to touch sub-0.1mm jitter left enough of it to tank the FFT's SNR
    # on several real cancelled stamps. A 4th-order zero-phase (filtfilt,
    # no lag/shift of the real peaks) Butterworth low-pass with its
    # cutoff set a comfortable margin above the finest plausible tooth
    # frequency removes that jitter wholesale while leaving every
    # physically-possible period untouched. Used for the FFT/hole
    # detection and the displayed profile -- *not* for
    # _detect_profile_roi below, whose anomaly threshold is relative to
    # the profile's own local-vs-global roughness and is already tuned
    # against the raw (unsmoothed) signal; filtering shifts that ratio
    # and started flagging good stretches as noisy that weren't before.
    finest_plausible_freq_cyc_per_mm = gauge_range[1] / 20.0
    cutoff_freq_cyc_per_mm = 1.5 * finest_plausible_freq_cyc_per_mm
    nyquist_cyc_per_mm = 0.5 / PROFILE_STEP_MM
    normalized_cutoff = min(cutoff_freq_cyc_per_mm / nyquist_cyc_per_mm, 0.99)
    if len(depth_grid_raw_mm) > 30:
        sos = butter(4, normalized_cutoff, btype="low", output="sos")
        depth_grid_mm = sosfiltfilt(sos, depth_grid_raw_mm)
    else:
        depth_grid_mm = depth_grid_raw_mm

    perf_depth_mm = float(np.polyval(bottom_fit, 0) - np.polyval(tip_fit, 0)) / outward_scale

    # Provisional full-profile estimate, used only to size the ROI-detection
    # windows below -- a rough gauge guess is enough for that, and this
    # runs before any noise exclusion so it has to tolerate noisy input.
    provisional_gauge, *_ = _estimate_period(s_grid, depth_grid_mm, gauge_range)
    pitch_hint_mm = 20.0 / provisional_gauge if provisional_gauge else 20.0 / 12.0

    if manual_roi_mm is not None:
        roi_mm = manual_roi_mm
        good_mask = _mask_from_roi(s_grid, roi_mm)
        roi_manual = True
    else:
        good_mask = _detect_profile_roi(s_grid, depth_grid_raw_mm, pitch_hint_mm)
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

    result = _estimate_period(s_grid[good_mask], depth_grid_mm[good_mask], gauge_range)
    gauge_spectral, spectral_snr, freqs, mag, peak_freq = result

    holes, gauge_holes, spacing_std, spacing_max_dev = _detect_holes(
        s_grid, depth_grid_mm, gauge_spectral, good_mask=good_mask
    )

    hole_diameters = [h.diameter_mm for h in holes if h.diameter_mm is not None]
    hole_diameter_mm = float(np.mean(hole_diameters)) if hole_diameters else None

    # The amplitude sanity check inside _classify_and_pick must look only at
    # the ROI actually being judged (auto-detected or the user's slider
    # selection) -- using the full unmasked profile defeated the entire
    # point of excluding a noisy stretch: a clean, genuinely-perforated
    # selection could still get graded on amplitude dominated by an
    # unrelated noisy part of the side outside that selection.
    roi_depth_mm = depth_grid_mm[good_mask] if good_mask.any() else depth_grid_mm
    # A manually-drawn spatial ROI or a gauge-range slider narrowed away
    # from its full default both mean the same thing here: the user is
    # vouching for this result, not just the automatic pipeline.
    user_confirmed = roi_manual or gauge_range != (GAUGE_MIN, GAUGE_MAX)
    kind, gauge, uncertainty, confidence = _classify_and_pick(
        gauge_spectral,
        spectral_snr,
        gauge_holes,
        holes,
        roi_depth_mm,
        perf_depth_mm,
        warnings,
        user_confirmed=user_confirmed,
        gauge_range=gauge_range,
    )

    notation = _notation(gauge) if gauge is not None else None

    # Tip line back in pixel space, for diagnostics: it's just the box edge
    # itself (tip_fit is identically zero), so this is exactly edge.origin
    # / edge.direction -- the dashed line drawn on the diagnostic image
    # coincides with the box edge by construction.
    tip_origin_px = edge.origin
    tip_dir_px = edge.direction

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
    s_grid: np.ndarray,
    depth_mm: np.ndarray,
    gauge_range: tuple[float, float] = (GAUGE_MIN, GAUGE_MAX),
) -> tuple[float | None, float, np.ndarray, np.ndarray, float | None]:
    """Spectral (FFT) period estimate. Returns (gauge, SNR, freqs, magnitude,
    peak_freq). ``gauge_range`` restricts which band the highest peak is
    picked from -- the GUI's single top-of-panel range slider (SPEC §6):
    narrowing it to where the user can see the real tooth-spacing peak in
    the spectrum plot keeps a spurious peak elsewhere in the full 6-18
    default band from winning instead."""
    n = len(depth_mm)
    if n < 20:
        return None, 0.0, np.array([]), np.array([]), None

    gauge_lo, gauge_hi = gauge_range

    detrended = depth_mm - np.mean(depth_mm)
    window = np.hanning(n)
    padded_n = int(2 ** np.ceil(np.log2(n * 8)))
    spectrum = np.fft.rfft(detrended * window, n=padded_n)
    freqs = np.fft.rfftfreq(padded_n, d=PROFILE_STEP_MM)
    mag = np.abs(spectrum)

    band = (freqs >= 1.0 / (20.0 / gauge_lo)) & (freqs <= 1.0 / (20.0 / gauge_hi))
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

    # The noise floor must come from a band wide enough to actually be
    # representative of "background", independent of how narrow the
    # user's peak-search gauge_range is -- using the *search* band itself
    # (as this used to) means narrowing it to tightly bracket a real peak
    # shrinks the median toward the peak's own skirt, inflating the
    # apparent background and tanking the SNR for exactly the clean,
    # narrowly-bracketed peaks a narrow range is meant to isolate (a real
    # 12-ish gauge peak went from accepted at the full 6-18 band to
    # "no periodic structure" once narrowed to 10-15, even though the peak
    # itself was untouched). Always use the full default band for this.
    noise_band = (freqs >= 1.0 / (20.0 / GAUGE_MIN)) & (freqs <= 1.0 / (20.0 / GAUGE_MAX))
    background = np.median(mag[noise_band]) if np.any(noise_band) else np.median(mag[band_idx])
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
    user_confirmed: bool = False,
    gauge_range: tuple[float, float] = (GAUGE_MIN, GAUGE_MAX),
) -> tuple[str, float | None, float | None, float]:
    amplitude = float(depth_mm.max() - depth_mm.min()) if len(depth_mm) else 0.0
    gauge_range_lo, gauge_range_hi = gauge_range
    # Small slack around the selected band: the slider marks roughly where
    # the real peak is, not a hard boundary to the mm.
    _RANGE_TOLERANCE = 0.5

    # Real perforation holes cut substantially into the paper (tenths of a
    # mm at least); this check must run *before* trusting the spectral SNR,
    # because a flat edge's sub-pixel rendering/scan texture can present a
    # small but spectrally "clean" false periodicity (seen on a rotated
    # synthetic straight edge: ~0.05 mm amplitude, SNR > 3, landing
    # plausibly in the 6-18 gauge band) that a periodicity-only check will
    # not catch.
    #
    # This floor is tuned for the *automatic* case, where nothing else has
    # vouched for the result. ``user_confirmed`` is set when the user has
    # narrowed the gauge-range slider away from its full default (SPEC §6)
    # -- they are themselves asserting "the real peak is in here", so a
    # found peak is trusted more than the automatic floor would otherwise
    # allow. Real scans have shown genuine, unmistakably periodic tooth
    # patterns with amplitude well under 0.08mm (thinner paper, gentler
    # scan depth response, etc.) that this floor would reject outright
    # regardless of how clean the periodicity is. Confirmation still has to
    # pass every other check (hole count, spectral/hole-regression
    # agreement) -- this only lowers the floor that would otherwise block
    # it from ever being judged on that evidence at all.
    MIN_PERFORATION_AMPLITUDE_MM = 0.02 if user_confirmed else 0.08
    if amplitude < MIN_PERFORATION_AMPLITUDE_MM:
        warnings.append(
            f"amplitude {amplitude:.3f}mm too small for real perforation "
            "(sub-pixel edge texture, not holes): classified as straight edge"
        )
        return "straight_edge", None, None, 0.9

    # Spectral SNR >= 3.0 is strong enough evidence entirely on its own --
    # a clean FFT peak doesn't need corroborating hole detections, and
    # requiring them anyway used to block an otherwise-clear spectral
    # result whenever contamination suppressed individual hole detections
    # (cancellation ink can wipe out ``_detect_holes``'s spatial peaks
    # while leaving the side's overall periodicity, which the FFT looks at
    # in aggregate, intact). Below that automatic bar, fall back to the
    # user-confirmed bypass: the user has narrowed the gauge range to
    # where they can see the real peak, and >= 3 real hole detections are
    # enough to trust it -- "3 holes should be enough to make a best
    # guess" once a human has vouched for the band.
    spectrally_periodic = spectral_snr >= 3.0 and gauge_spectral is not None
    if not spectrally_periodic and not (
        user_confirmed and gauge_holes is not None and len(holes) >= 3
    ):
        warnings.append("no periodic structure detected: classified as imperforate")
        return "imperf", None, None, 0.8

    # The hole-position regression's own uncertainty (standard error from
    # the linear fit of hole index -> position) -- computed whenever there
    # are enough holes to fit (np.polyfit's cov=True needs more points than
    # the fit order to estimate a covariance at all; found by a crash on a
    # real, badly torn stamp edge with only 2 spurious "holes" detected),
    # independent of whether its point estimate ends up being the reported
    # gauge.
    regression_gauge = None
    regression_uncertainty = None
    if len(holes) >= 3:
        idx = np.arange(len(holes))
        s_peaks = np.array([h.s_mm for h in holes])
        (period, _), cov = np.polyfit(idx, s_peaks, 1, cov=True)
        if period > 0:
            se_period = float(np.sqrt(cov[0, 0]))
            regression_gauge = 20.0 / period
            regression_uncertainty = 20.0 * se_period / period**2

    confidence = 1.0

    # The spectral peak -- the direct FFT peak position, exactly what's
    # drawn on the spectrum plot -- is the primary reported value whenever
    # we have one, not the hole-position regression. Real cancelled-stamp
    # data has shown the regression disagree with, and be *less* reliable
    # than, a clean spectral peak: a single misdetected or missed hole
    # (cancellation ink, a broken tooth) skews a linear fit over the whole
    # side, while the FFT peak stays anchored by the side's overall
    # periodicity. SPEC §6: "just take the peak position [...] and report
    # that" -- the regression is still used for its uncertainty estimate
    # when it agrees, and as the fallback value on the rare side where no
    # spectral peak was found at all.
    if gauge_spectral is not None:
        gauge = gauge_spectral
        uncertainty = None
        if regression_gauge is not None:
            agree = abs(gauge_spectral - regression_gauge) <= 0.1
            if agree:
                uncertainty = regression_uncertainty
            else:
                warnings.append(
                    f"spectral ({gauge_spectral:.2f}) and hole-position "
                    f"({regression_gauge:.2f}) estimates disagree by > 0.1 gauge; "
                    "using the spectral peak"
                )
                confidence *= 0.6
        if spectrally_periodic:
            if spectral_snr < 5.0:
                confidence *= 0.7
        else:
            warnings.append(
                "spectral periodicity check did not clear its automatic threshold "
                "on this manual selection, but the peak sits in the selected gauge "
                "range and holes were detected there; trusting the peak"
            )
            confidence *= 0.6
    elif regression_gauge is not None:
        # No spectral peak at all (can only reach here via the
        # user_confirmed bypass above) -- the regression isn't constrained
        # to gauge_range the way the spectral search is, so a bad
        # detection latching onto noise can land well outside the band
        # the user actually selected. Reject rather than report something
        # the user's own selection already contradicts.
        regression_in_range = (
            gauge_range_lo - _RANGE_TOLERANCE
            <= regression_gauge
            <= gauge_range_hi + _RANGE_TOLERANCE
        )
        if not regression_in_range:
            warnings.append(
                f"hole-spacing estimate ({regression_gauge:.2f}) falls outside "
                f"the selected gauge range ({gauge_range_lo:.2f}-{gauge_range_hi:.2f}); "
                "not trusting it"
            )
            return "imperf", None, None, 0.8
        gauge = regression_gauge
        uncertainty = regression_uncertainty
        warnings.append("no spectral peak found; gauge estimated from hole spacing alone")
        confidence *= 0.5
    else:
        warnings.append("no periodic structure detected: classified as imperforate")
        return "imperf", None, None, 0.8

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
    gauge_range: tuple[float, float] = (GAUGE_MIN, GAUGE_MAX),
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

    ``gauge_range`` restricts every side's spectral peak search to that
    (min, max) gauge band instead of the default 6-18 -- the GUI's single
    top-of-panel range slider, for narrowing the FFT's peak search to where
    the user can actually see the real tooth-spacing peak in the spectrum
    plot, rather than risking a spurious peak elsewhere in the full band
    winning (SPEC §6).
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
            gauge_range=gauge_range,
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
        gauge_range=gauge_range,
    )


def perforation_result_notation(sides: dict) -> str:
    """Compound notation, e.g. '11 1/2 x 12' (top/bottom x left/right).

    Works on any mapping of side name to an object with ``kind`` and
    ``notation`` -- a fresh :class:`SideAnalysis` or a persisted
    ``PerforationSide`` (auto merged with manual corrections) -- and
    tolerates missing sides.
    """

    def fmt(names: tuple[str, str]) -> str:
        vals = []
        for name in names:
            side = sides.get(name)
            if side is None:
                continue
            if side.kind == "imperf":
                vals.append("imperf")
            elif side.kind == "straight_edge":
                vals.append("straight edge")
            elif side.notation:
                vals.append(side.notation)
        a, b = sides.get(names[0]), sides.get(names[1])
        if a and b and a.kind == b.kind == "perforated" and a.notation == b.notation:
            return a.notation or "?"
        return "/".join(vals) if vals else "?"

    return f"{fmt(('top', 'bottom'))} x {fmt(('left', 'right'))}"


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
            gauge_lo, gauge_hi = analysis.gauge_range
            if (gauge_lo, gauge_hi) != (GAUGE_MIN, GAUGE_MAX):
                ax_s.axvspan(gauge_lo, gauge_hi, color="green", alpha=0.1, lw=0)
            if side.spectral_peak_freq:
                ax_s.axvline(side.spectral_peak_freq * 20.0, color="red", lw=0.5)
        ax_s.set_title(f"{side_name} spectrum (gauge axis)", fontsize=8)
        ax_s.tick_params(labelsize=6)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    return buf.getvalue()
