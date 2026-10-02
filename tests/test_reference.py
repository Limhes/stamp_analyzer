from pathlib import Path

import numpy as np
import pytest

from stampan.core.calibration.it8 import N_COLS, ROWS, IT8Corners
from stampan.core.calibration.reference import (
    RulerAxisSpec,
    build_reference,
    list_references,
    load_colour_model,
    load_reference,
    save_reference,
)
from stampan.core.cgats import parse_cgats_file
from stampan.core.io import ScanMetadata

REF_PATH = Path(__file__).parent.parent / "reference_data" / "R240103.txt"

# Same fixed synthetic "camera" matrix used in test_characterisation.py, so
# a scan built from real IT8 XYZ values round-trips through a 3x3 matrix fit
# essentially exactly. This validates the reference-building pipeline's
# plumbing (geometry + IT8 sampling + model selection + persistence), not
# a real scanner's characteristics -- there is no real reference scan yet.
_XYZ_TO_RGB = np.array(
    [
        [2.5, -1.2, -0.3],
        [-0.9, 1.9, 0.0],
        [0.05, -0.2, 1.1],
    ]
)


def _build_synthetic_reference_scan(
    pitch_px: float = 40.0,
    ruler_px_per_mm: float = 10.0,
) -> tuple[np.ndarray, IT8Corners, RulerAxisSpec, RulerAxisSpec]:
    table = parse_cgats_file(REF_PATH)
    by_id = table.by_sample_id()

    margin = 80
    it8_w = int(pitch_px * (N_COLS - 1) + pitch_px * 2)
    it8_h = int(pitch_px * (len(ROWS) - 1) + pitch_px * 4)  # + room for GS row
    ruler_len_mm = 60
    ruler_len_px = int(ruler_len_mm * ruler_px_per_mm)

    canvas_w = max(it8_w, ruler_len_px) + 2 * margin
    canvas_h = it8_h + margin * 3 + 30  # + horizontal ruler strip height
    canvas = np.full((canvas_h, canvas_w, 3), 40000.0, dtype=np.float64)

    def patch_centre(row_idx: int, col_idx: int) -> tuple[float, float]:
        return (margin + col_idx * pitch_px, margin + row_idx * pitch_px)

    half = pitch_px * 0.85 / 2.0
    for ri, row_letter in enumerate(ROWS):
        for ci in range(N_COLS):
            cx, cy = patch_centre(ri, ci)
            row = by_id[f"{row_letter}{ci + 1}"]
            xyz = np.array([row["XYZ_X"], row["XYZ_Y"], row["XYZ_Z"]])
            rgb = xyz @ _XYZ_TO_RGB.T
            rgb_scaled = np.clip(rgb / 100.0 * 65535.0, 0, 65535)
            x0, x1 = int(cx - half), int(cx + half)
            y0, y1 = int(cy - half), int(cy + half)
            canvas[y0:y1, x0:x1] = rgb_scaled

    # build_it8_grid places the GS row exactly one row-pitch below row L.
    gs_y_px = margin + (len(ROWS) - 1) * pitch_px + pitch_px
    for gi in range(24):
        gx = margin + gi * (pitch_px * (N_COLS - 1) / 23)
        row = by_id[f"GS{gi}"]
        xyz = np.array([row["XYZ_X"], row["XYZ_Y"], row["XYZ_Z"]])
        rgb = xyz @ _XYZ_TO_RGB.T
        rgb_scaled = np.clip(rgb / 100.0 * 65535.0, 0, 65535)
        x0, x1 = int(gx - half), int(gx + half)
        y0, y1 = int(gs_y_px - half), int(gs_y_px + half)
        canvas[y0:y1, x0:x1] = rgb_scaled

    corners = IT8Corners(
        a1=patch_centre(0, 0),
        a22=patch_centre(0, N_COLS - 1),
        l1=patch_centre(len(ROWS) - 1, 0),
        l22=patch_centre(len(ROWS) - 1, N_COLS - 1),
    )

    # Horizontal ruler strip below the IT8 grid, with 1 mm ticks.
    ruler_y = it8_h + margin * 2
    ruler_x0 = margin
    n_ticks = 30
    strip = np.full((20, ruler_len_px + 20), 55000.0)
    for i in range(n_ticks):
        x = 10 + i * ruler_px_per_mm
        xi0, xi1 = int(x - 1), int(x + 2)
        strip[:, xi0:xi1] = 4000.0
    canvas[ruler_y : ruler_y + 20, ruler_x0 : ruler_x0 + strip.shape[1], :] = strip[..., None]

    x_axis = RulerAxisSpec(
        centre_xy=(ruler_x0 + strip.shape[1] / 2.0, ruler_y + 10),
        length_px=strip.shape[1],
        width_px=16,
        angle_deg=0.0,
        tick_spacing_mm=1.0,
        px_per_mm_hint=ruler_px_per_mm,
    )
    # Reuse the same strip for the y axis spec too (isotropic test fixture);
    # a real reference would have a second, vertically-oriented strip.
    y_axis = RulerAxisSpec(
        centre_xy=x_axis.centre_xy,
        length_px=x_axis.length_px,
        width_px=x_axis.width_px,
        angle_deg=0.0,
        tick_spacing_mm=1.0,
        px_per_mm_hint=ruler_px_per_mm,
    )

    return canvas.astype(np.uint16), corners, x_axis, y_axis


def test_build_reference_recovers_scale_and_low_cv_delta_e00():
    scan, corners, x_axis, y_axis = _build_synthetic_reference_scan()
    result = build_reference(
        name="synthetic test reference",
        scan=scan,
        scan_metadata=ScanMetadata(scanner_options={"resolution": 1200, "mode": "Color"}),
        x_axis=x_axis,
        it8_corners=corners,
        cgats_reference_path=REF_PATH,
        y_axis=y_axis,
    )
    ref = result.reference
    assert ref.scale.px_per_mm_x == pytest.approx(10.0, rel=0.02)
    assert ref.colour_model.kind in ("matrix3x3", "root_poly2", "root_poly3")
    assert ref.colour_model.cv_delta_e00["mean"] < 1.0
    assert set(result.diagnostics) >= {
        "geometry_x.png",
        "it8_grid.png",
        "it8_std_heatmap.png",
        "colour_model_error_heatmap.png",
    }


def test_save_and_load_reference_roundtrip(tmp_path):
    scan, corners, x_axis, y_axis = _build_synthetic_reference_scan()
    result = build_reference(
        name="roundtrip test",
        scan=scan,
        scan_metadata=ScanMetadata(scanner_options={"resolution": 1200}),
        x_axis=x_axis,
        it8_corners=corners,
        cgats_reference_path=REF_PATH,
        y_axis=y_axis,
    )
    ref_dir = save_reference(result, tmp_path)
    assert (ref_dir / "reference.json").exists()
    assert (ref_dir / "reference_scan.tif").exists()
    assert (ref_dir / "model.npz").exists()
    assert (ref_dir / "diagnostics" / "it8_grid.png").exists()

    loaded = load_reference(tmp_path, result.reference.ref_id)
    assert loaded == result.reference

    refs = list_references(tmp_path)
    assert len(refs) == 1
    assert refs[0].ref_id == result.reference.ref_id

    model = load_colour_model(tmp_path, result.reference.ref_id)
    rgb_sample = np.array([[20000.0, 15000.0, 10000.0]])
    lab = model.predict_lab(rgb_sample)
    assert lab.shape == (1, 3)
    assert np.all(np.isfinite(lab))
