import cv2
import numpy as np
import pytest

from stampan.core.calibration.geometry import (
    calibrate_axis,
    detect_ticks,
    fit_scale,
)


def make_synthetic_ruler(
    px_per_mm: float,
    n_ticks: int = 40,
    tick_spacing_mm: float = 1.0,
    angle_deg: float = 0.0,
    noise_sigma: float = 2.0,
    blur_sigma: float = 0.6,
    tick_width_px: float = 1.5,
    seed: int = 0,
) -> tuple[np.ndarray, tuple[float, float]]:
    """A light ruler strip with dark tick marks, embedded in a larger image,
    optionally rotated by angle_deg."""
    rng = np.random.default_rng(seed)
    pitch_px = tick_spacing_mm * px_per_mm
    length_px = int(pitch_px * (n_ticks - 1) + 40)
    width_px = 30
    margin = 60

    canvas_w = length_px + 2 * margin
    canvas_h = width_px + 2 * margin
    img = np.full((canvas_h, canvas_w), 55000.0, dtype=np.float64)

    strip_y0 = margin
    strip = np.full((width_px, length_px), 55000.0, dtype=np.float64)
    for i in range(n_ticks):
        x = 20 + i * pitch_px
        x0 = int(round(x - tick_width_px))
        x1 = int(round(x + tick_width_px)) + 1
        x0, x1 = max(0, x0), min(length_px, x1)
        strip[:, x0:x1] = 5000.0

    img[strip_y0 : strip_y0 + width_px, margin : margin + length_px] = strip

    if blur_sigma > 0:
        img = cv2.GaussianBlur(img, (0, 0), blur_sigma)
    if noise_sigma > 0:
        img += rng.normal(0, noise_sigma, img.shape)

    centre = (margin + length_px / 2.0, strip_y0 + width_px / 2.0)

    if angle_deg != 0.0:
        rot_mat = cv2.getRotationMatrix2D(centre, -angle_deg, 1.0)
        img = cv2.warpAffine(
            img, rot_mat, (canvas_w, canvas_h), flags=cv2.INTER_LINEAR, borderValue=55000.0
        )

    return img.astype(np.uint16), centre


def test_detect_ticks_count_and_spacing():
    img, centre = make_synthetic_ruler(px_per_mm=40.0, n_ticks=30, noise_sigma=0.0)
    length_px = img.shape[1] - 60
    result, _profile = calibrate_axis(
        img,
        centre_xy=centre,
        length_px=length_px,
        width_px=20,
        angle_deg=0.0,
        tick_spacing_mm=1.0,
        px_per_mm_hint=40.0,
    )
    assert result.n_ticks >= 25
    assert result.px_per_mm == pytest.approx(40.0, rel=0.01)


def test_scale_fit_recovers_known_scale_with_noise():
    true_scale = 23.7
    img, centre = make_synthetic_ruler(
        px_per_mm=true_scale, n_ticks=35, noise_sigma=500.0, blur_sigma=0.8
    )
    length_px = img.shape[1] - 60
    result, _profile = calibrate_axis(
        img,
        centre_xy=centre,
        length_px=length_px,
        width_px=20,
        angle_deg=0.0,
        tick_spacing_mm=1.0,
        px_per_mm_hint=true_scale,
    )
    assert result.px_per_mm == pytest.approx(true_scale, rel=0.01)
    assert result.se_px_per_mm < 0.05
    assert result.residual_max_px < 1.0


def test_scale_fit_handles_rotation():
    true_scale = 30.0
    img, centre = make_synthetic_ruler(
        px_per_mm=true_scale, n_ticks=25, angle_deg=4.0, noise_sigma=100.0
    )
    length_px = img.shape[1] - 120
    result, _profile = calibrate_axis(
        img,
        centre_xy=centre,
        length_px=length_px,
        width_px=20,
        angle_deg=4.0,
        tick_spacing_mm=1.0,
        px_per_mm_hint=true_scale,
    )
    assert result.px_per_mm == pytest.approx(true_scale, rel=0.02)


def test_fit_scale_rejects_too_few_ticks():
    with pytest.raises(ValueError):
        fit_scale(np.array([1.0]), tick_spacing_mm=1.0)


def test_fit_scale_quadratic_term_near_zero_for_linear_ticks():
    positions = np.arange(10) * 12.3 + 5.0
    result = fit_scale(positions, tick_spacing_mm=1.0)
    assert abs(result.quadratic_term_px_per_mm2) < 1e-6
    assert result.residual_rms_px < 1e-6


def test_detect_ticks_warns_on_too_few_peaks():
    profile = np.full(200, 50000.0)
    profile[100] = 1000.0  # single tick
    result = detect_ticks(profile, expected_spacing_px=20.0)
    assert len(result.tick_positions_px) < 3
    assert result.warnings
