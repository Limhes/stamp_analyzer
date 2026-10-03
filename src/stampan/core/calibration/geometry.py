"""Ruler-based geometric calibration: px<->mm scale, per axis (SPEC §4.2).

The user (or, later, auto-detection) gives a rough rectangular region on the
ruler and its orientation. We straighten that region, collapse it to a 1-D
intensity profile along the ruler, find the dark tick marks with sub-pixel
precision, and fit position (px) against assigned mm values. x and y must be
calibrated independently -- there is no dpi constant anywhere in this
module.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import find_peaks


@dataclass
class TickDetectionResult:
    profile: np.ndarray  # 1-D intensity profile along the ruler axis
    tick_positions_px: np.ndarray  # sub-pixel tick centres, in profile-index units
    warnings: list[str] = field(default_factory=list)


@dataclass
class ScaleFitResult:
    px_per_mm: float
    se_px_per_mm: float
    residual_rms_px: float
    residual_max_px: float
    quadratic_term_px_per_mm2: float
    n_ticks: int
    tick_positions_px: list[float]
    tick_mm_values: list[float]
    residuals_px: list[float]
    confidence: float
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "px_per_mm": self.px_per_mm,
            "se_px_per_mm": self.se_px_per_mm,
            "residual_rms_px": self.residual_rms_px,
            "residual_max_px": self.residual_max_px,
            "quadratic_term_px_per_mm2": self.quadratic_term_px_per_mm2,
            "n_ticks": self.n_ticks,
            "confidence": self.confidence,
            "warnings": self.warnings,
        }


def extract_ruler_profile(
    image: np.ndarray,
    centre_xy: tuple[float, float],
    length_px: float,
    width_px: float,
    angle_deg: float,
) -> np.ndarray:
    """Straighten a rotated rectangular strip along the ruler and collapse it
    to a 1-D intensity profile (mean across the strip width) along its length.

    ``angle_deg`` is the direction of increasing tick position, measured
    from the +x axis.
    """
    size = (int(round(length_px)), int(round(width_px)))

    # Crop a generous ROI around the ruler strip *before* rotating or
    # collapsing channels: `image` here is the full reference/sample scan
    # (hundreds of MB at 1200dpi), and both `.mean(axis=-1)` and the
    # subsequent warpAffine used to run on the whole thing just to read out
    # a thin strip a few hundred px long. Sized so the ROI contains the
    # strip for any rotation angle (its half-diagonal is the farthest any
    # output pixel's source can be from the strip centre).
    roi_radius = int(np.ceil(np.hypot(length_px, width_px) / 2.0)) + 4
    cx, cy = centre_xy
    h, w = image.shape[:2]
    x0 = max(0, int(cx) - roi_radius)
    y0 = max(0, int(cy) - roi_radius)
    x1 = min(w, int(cx) + roi_radius)
    y1 = min(h, int(cy) + roi_radius)
    roi = image[y0:y1, x0:x1]
    roi_centre = (cx - x0, cy - y0)

    if roi.ndim == 3:
        roi = roi.mean(axis=-1)

    # getRectSubPix doesn't rotate, so rotate the ROI about the strip
    # centre first (so increasing x in the crop is the ruler's own axis),
    # then crop.
    rot_mat = cv2.getRotationMatrix2D(roi_centre, angle_deg, 1.0)
    rh, rw = roi.shape[:2]
    rotated = cv2.warpAffine(roi.astype(np.float32), rot_mat, (rw, rh), flags=cv2.INTER_LINEAR)
    straightened = cv2.getRectSubPix(rotated, size, roi_centre)
    # getRectSubPix returns shape (size[1], size[0]) = (width_px, length_px);
    # collapse across the strip width (axis 0) to get the 1-D profile.
    profile = straightened.mean(axis=0)
    return profile


def detect_ticks(
    profile: np.ndarray,
    expected_spacing_px: float,
    tick_polarity: str = "dark",
) -> TickDetectionResult:
    """Find tick centres in a 1-D profile with sub-pixel (parabolic) refinement.

    Ticks are assumed evenly spaced at approximately ``expected_spacing_px``.
    """
    warnings: list[str] = []
    signal = -profile if tick_polarity == "dark" else profile
    signal = signal - signal.min()

    min_distance = max(1, int(round(expected_spacing_px * 0.6)))
    peaks, _ = find_peaks(signal, distance=min_distance, prominence=signal.std() * 0.3)

    refined = []
    for p in peaks:
        if 0 < p < len(signal) - 1:
            y0, y1, y2 = signal[p - 1], signal[p], signal[p + 1]
            denom = y0 - 2 * y1 + y2
            offset = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-9 else 0.0
            offset = float(np.clip(offset, -1.0, 1.0))
            refined.append(p + offset)
        else:
            refined.append(float(p))

    if len(refined) < 3:
        warnings.append(f"only {len(refined)} ticks detected; need >= 3 for a reliable fit")

    return TickDetectionResult(
        profile=profile, tick_positions_px=np.array(refined), warnings=warnings
    )


def fit_scale(
    tick_positions_px: np.ndarray,
    tick_spacing_mm: float,
    start_mm: float = 0.0,
) -> ScaleFitResult:
    """Fit px-per-mm from detected tick positions, assuming they are
    consecutive ticks spaced ``tick_spacing_mm`` apart.

    Reports the standard error of the scale, residual RMS/max, and a
    quadratic term as a non-linearity check (SPEC §4.2).
    """
    warnings: list[str] = []
    positions = np.asarray(tick_positions_px, dtype=np.float64)
    n = len(positions)
    if n < 2:
        raise ValueError("need at least 2 ticks to fit a scale")

    mm_values = start_mm + tick_spacing_mm * np.arange(n)

    # Linear fit: position_px = a + b * mm. Use polyfit with cov for the SE.
    (b, a), cov = np.polyfit(mm_values, positions, deg=1, cov=True)
    px_per_mm = float(b)
    se_px_per_mm = float(np.sqrt(cov[0, 0])) if np.all(np.isfinite(cov)) else float("nan")

    predicted = a + b * mm_values
    residuals = positions - predicted
    residual_rms = float(np.sqrt(np.mean(residuals**2)))
    residual_max = float(np.max(np.abs(residuals)))

    # Quadratic term as a non-linearity check.
    quad_term = 0.0
    if n >= 4:
        coeffs2 = np.polyfit(mm_values, positions, deg=2)
        quad_term = float(coeffs2[0])

    rel_se = se_px_per_mm / px_per_mm if px_per_mm else float("inf")
    confidence = 1.0
    if n < 5:
        warnings.append(f"only {n} ticks used for the fit; scale uncertainty is poorly estimated")
        confidence *= 0.5
    if rel_se > 0.0005:
        warnings.append(f"relative scale uncertainty {rel_se * 100:.3f}% exceeds the 0.05% target")
        confidence *= 0.6
    if residual_max > 0.5:
        warnings.append(f"max residual {residual_max:.2f} px is large for a linear tick spacing")
        confidence *= 0.7
    confidence = max(0.0, min(1.0, confidence))

    return ScaleFitResult(
        px_per_mm=px_per_mm,
        se_px_per_mm=se_px_per_mm,
        residual_rms_px=residual_rms,
        residual_max_px=residual_max,
        quadratic_term_px_per_mm2=quad_term,
        n_ticks=n,
        tick_positions_px=positions.tolist(),
        tick_mm_values=mm_values.tolist(),
        residuals_px=residuals.tolist(),
        confidence=confidence,
        warnings=warnings,
    )


def calibrate_axis(
    image: np.ndarray,
    centre_xy: tuple[float, float],
    length_px: float,
    width_px: float,
    angle_deg: float,
    tick_spacing_mm: float,
    px_per_mm_hint: float,
    tick_polarity: str = "dark",
) -> tuple[ScaleFitResult, np.ndarray]:
    """Full pipeline: extract profile -> detect ticks -> fit scale, for one axis.

    ``px_per_mm_hint`` is a rough a-priori scale (e.g. from the nominal scan
    resolution) used only to set the tick-search distance; the fitted result
    does not depend on its accuracy, just on it being within roughly 2x.

    Returns ``(result, profile)``; ``profile`` is kept alongside the result
    so :func:`render_diagnostic` can plot it without re-extracting it.
    """
    profile = extract_ruler_profile(image, centre_xy, length_px, width_px, angle_deg)
    expected_spacing_px = max(3.0, tick_spacing_mm * px_per_mm_hint)
    ticks = detect_ticks(profile, expected_spacing_px, tick_polarity=tick_polarity)
    result = fit_scale(ticks.tick_positions_px, tick_spacing_mm)
    result.warnings = ticks.warnings + result.warnings
    return result, profile


def render_diagnostic(result: ScaleFitResult, profile: np.ndarray) -> bytes:
    """Render the tick-detection profile and residual plot as a PNG (bytes)."""
    fig, (ax_profile, ax_resid) = plt.subplots(1, 2, figsize=(9, 3.2))

    ax_profile.plot(profile, lw=0.8, color="black")
    for x in result.tick_positions_px:
        ax_profile.axvline(x, color="crimson", lw=0.5, alpha=0.6)
    ax_profile.set_title("ruler profile + detected ticks")
    ax_profile.set_xlabel("position (px)")
    ax_profile.set_ylabel("intensity")

    ax_resid.scatter(result.tick_mm_values, result.residuals_px, s=12, color="steelblue")
    ax_resid.axhline(0, color="black", lw=0.5)
    ax_resid.set_title(f"residuals (RMS {result.residual_rms_px:.3f} px)")
    ax_resid.set_xlabel("mm")
    ax_resid.set_ylabel("residual (px)")

    fig.suptitle(
        f"{result.px_per_mm:.4f} +/- {result.se_px_per_mm:.4f} px/mm (conf {result.confidence:.2f})"
    )
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    return buf.getvalue()
