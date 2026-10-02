import numpy as np
import pytest

from stampan.core.calibration.it8 import (
    N_COLS,
    N_GS,
    ROWS,
    IT8Corners,
    build_it8_grid,
    sample_it8_grid,
)


def make_synthetic_it8_scan(
    pitch_px: float = 40.0,
    margin_px: float = 60.0,
    patch_fill: float = 0.85,
    seed: int = 0,
) -> tuple[np.ndarray, IT8Corners, dict[str, tuple[float, float, float]]]:
    """A flat-colour patch grid (not colorimetrically meaningful, just
    distinct known RGB per patch) at an exactly-known grid geometry, to
    validate grid interpolation and patch sampling independent of colour
    science."""
    rng = np.random.default_rng(seed)
    width = int(margin_px * 2 + pitch_px * (N_COLS - 1) + pitch_px)
    height = int(margin_px * 2 + pitch_px * (len(ROWS) - 1) + pitch_px + pitch_px * 2)
    img = np.full((height, width, 3), 200.0, dtype=np.float64)

    rgb_truth: dict[str, tuple[float, float, float]] = {}

    def patch_centre(row_idx: int, col_idx: int) -> tuple[float, float]:
        return (margin_px + col_idx * pitch_px, margin_px + row_idx * pitch_px)

    for ri, row_letter in enumerate(ROWS):
        for ci in range(N_COLS):
            cx, cy = patch_centre(ri, ci)
            colour = rng.uniform(10, 245, size=3)
            rgb_truth[f"{row_letter}{ci + 1}"] = tuple(colour.tolist())
            half = pitch_px * patch_fill / 2.0
            x0, x1 = int(cx - half), int(cx + half)
            y0, y1 = int(cy - half), int(cy + half)
            img[y0:y1, x0:x1] = colour

    gs_y = margin_px + (len(ROWS) - 1) * pitch_px + pitch_px
    for gi in range(N_GS):
        gx = margin_px + gi * (pitch_px * (N_COLS - 1) / (N_GS - 1))
        colour = rng.uniform(10, 245, size=3)
        rgb_truth[f"GS{gi}"] = tuple(colour.tolist())
        half = pitch_px * patch_fill / 2.0
        x0, x1 = int(gx - half), int(gx + half)
        y0, y1 = int(gs_y - half), int(gs_y + half)
        img[y0:y1, x0:x1] = colour

    corners = IT8Corners(
        a1=patch_centre(0, 0),
        a22=patch_centre(0, N_COLS - 1),
        l1=patch_centre(len(ROWS) - 1, 0),
        l22=patch_centre(len(ROWS) - 1, N_COLS - 1),
    )
    return img.astype(np.uint16), corners, rgb_truth


def test_grid_has_288_patches():
    _, corners, _ = make_synthetic_it8_scan()
    grid = build_it8_grid(corners)
    assert len(grid) == len(ROWS) * N_COLS + N_GS == 288


def test_grid_corner_positions_match_input():
    _, corners, _ = make_synthetic_it8_scan()
    grid = build_it8_grid(corners)
    assert grid["A1"] == pytest.approx(corners.a1)
    assert grid["A22"] == pytest.approx(corners.a22)
    assert grid["L1"] == pytest.approx(corners.l1)
    assert grid["L22"] == pytest.approx(corners.l22)


def test_sample_it8_grid_recovers_patch_colours():
    img, corners, rgb_truth = make_synthetic_it8_scan()
    samples = sample_it8_grid(img, corners, area_fraction=0.5)

    assert len(samples) == 288
    for patch_id, truth in rgb_truth.items():
        got = samples[patch_id].mean_rgb
        assert got == pytest.approx(truth, abs=1.0), patch_id
        # flat synthetic patches: std should be tiny (no noise added)
        assert max(samples[patch_id].std_rgb) < 1.0


def test_sample_it8_grid_is_robust_to_dust_outliers():
    img, corners, rgb_truth = make_synthetic_it8_scan(seed=1)
    # Add a few bright "dust" pixels inside the A1 patch.
    cx, cy = corners.a1
    img = img.copy()
    for dx in range(-3, 3):
        img[int(cy) + dx, int(cx) + dx] = [65535, 65535, 65535]

    samples = sample_it8_grid(img, corners, area_fraction=0.5, trim_fraction=0.15)
    got = samples["A1"].mean_rgb
    assert got == pytest.approx(rgb_truth["A1"], abs=2.0)


def test_gs_row_uses_explicit_corners_when_given():
    corners = IT8Corners(
        a1=(0, 0), a22=(2100, 0), l1=(0, 1650), l22=(2100, 1650), gs0=(50, 2000), gs23=(3700, 2000)
    )
    grid = build_it8_grid(corners)
    assert grid["GS0"] == pytest.approx((50, 2000))
    assert grid["GS23"] == pytest.approx((3700, 2000))
    # evenly spaced between the two given corners
    assert grid["GS12"][0] == pytest.approx(50 + (3700 - 50) * 12 / 23, abs=1e-6)


def test_gs_row_falls_back_to_extrapolation_without_explicit_corners():
    corners = IT8Corners(a1=(0, 0), a22=(2100, 0), l1=(0, 1650), l22=(2100, 1650))
    grid = build_it8_grid(corners)
    # old behaviour: one row-pitch below L, same span as columns 1-22
    row_pitch = 1650 / 11
    assert grid["GS0"] == pytest.approx((0, 1650 + row_pitch))
    assert grid["GS23"] == pytest.approx((2100, 1650 + row_pitch))
