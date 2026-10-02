from pathlib import Path

import numpy as np
import pytest

from stampan.core.calibration.characterisation import (
    choose_best_model,
    delta_e00_stats,
    evaluate_model,
    fit_linearisation,
    predict_lab_icc_profile,
    xyz_to_lab_array,
)
from stampan.core.cgats import parse_cgats_file

REF_PATH = Path(__file__).parent.parent / "reference_data" / "R240103.txt"
ICC_PATH = (
    Path(__file__).parent.parent.parent
    / "ColorIdentification"
    / "color_detect_gui"
    / "icc_profiles"
    / "epson1200u.icc"
)

# A fixed, plausible invertible "camera" matrix (XYZ -> RGB) used to derive
# synthetic scanner RGB from the real IT8 XYZ data, since there is no real
# reference scan yet. All numbers from these tests are synthetic-data
# sanity checks of the fitting code, not real characterisation results.
_XYZ_TO_RGB = np.array(
    [
        [2.5, -1.2, -0.3],
        [-0.9, 1.9, 0.0],
        [0.05, -0.2, 1.1],
    ]
)


@pytest.fixture(scope="module")
def patches():
    table = parse_cgats_file(REF_PATH)
    ids = [r["SAMPLE_ID"] for r in table.rows]
    xyz = np.array([[r["XYZ_X"], r["XYZ_Y"], r["XYZ_Z"]] for r in table.rows])
    rgb_linear = xyz @ _XYZ_TO_RGB.T
    return ids, xyz, rgb_linear


def test_matrix3x3_recovers_exact_linear_transform(patches):
    ids, xyz, rgb_linear = patches
    report = evaluate_model("matrix3x3", rgb_linear, xyz, ids)
    assert report.train.mean < 1e-8
    assert report.cv.mean < 1e-8


def test_root_polynomial_degree2_does_not_make_a_linear_fit_worse(patches):
    ids, xyz, rgb_linear = patches
    rep3x3 = evaluate_model("matrix3x3", rgb_linear, xyz, ids)
    rep_poly2 = evaluate_model("root_poly2", rgb_linear, xyz, ids, degree=2)
    assert rep_poly2.train.mean < 1e-4
    assert rep_poly2.cv.mean < max(1e-2, rep3x3.cv.mean + 1e-2)


def test_delta_e00_stats_zero_for_identical_lab():
    lab = np.array([[50.0, 10.0, -5.0], [20.0, 0.0, 0.0]])
    stats = delta_e00_stats(lab, lab)
    assert stats.mean == pytest.approx(0.0, abs=1e-9)
    assert stats.max == pytest.approx(0.0, abs=1e-9)


def test_choose_best_model_prefers_lower_cv_mean():
    class _Stats:
        def __init__(self, mean):
            self.mean = mean

    class _Report:
        def __init__(self, mean):
            self.cv = _Stats(mean)

    reports = {"matrix3x3": _Report(1.0), "root_poly2": _Report(0.5)}
    assert choose_best_model(reports) == "root_poly2"


def test_choose_best_model_rejects_degree3_without_clear_improvement():
    class _Stats:
        def __init__(self, mean):
            self.mean = mean

    class _Report:
        def __init__(self, mean):
            self.cv = _Stats(mean)

    # root_poly3 only marginally better than root_poly2 -> should be dropped
    # in favour of not overfitting, per SPEC §4.3.
    reports = {
        "matrix3x3": _Report(2.0),
        "root_poly2": _Report(1.0),
        "root_poly3": _Report(0.95),
    }
    assert choose_best_model(reports) == "root_poly2"

    reports["root_poly3"] = _Report(0.5)  # now clearly better
    assert choose_best_model(reports) == "root_poly3"


def test_fit_linearisation_undoes_a_known_gamma(patches):
    ids, xyz, rgb_linear = patches
    by_id = dict(zip(ids, range(len(ids)), strict=True))
    gs_idx = [by_id[f"GS{i}"] for i in range(24)]

    gamma = 1.8
    rgb_linear_clipped = np.clip(rgb_linear, 1e-6, None)
    rgb_gamma_encoded = rgb_linear_clipped ** (1.0 / gamma)

    gs_rgb_raw = rgb_gamma_encoded[gs_idx]
    gs_y = xyz[gs_idx, 1]

    curve = fit_linearisation(gs_rgb_raw, gs_y)
    linearised = curve.apply(gs_rgb_raw)

    # linearised should now be monotone with gs_y and close to gs_y/max(gs_y)
    target = gs_y / gs_y.max()
    assert np.corrcoef(linearised[:, 0], target)[0, 1] > 0.999


def test_xyz_to_lab_array_matches_cgats_within_mean_tolerance(patches):
    ids, xyz, _ = patches
    table = parse_cgats_file(REF_PATH)
    lab_file = np.array([[r["LAB_L"], r["LAB_A"], r["LAB_B"]] for r in table.rows])
    lab_computed = xyz_to_lab_array(xyz)
    assert np.mean(np.linalg.norm(lab_computed - lab_file, axis=1)) < 0.05


@pytest.mark.skipif(not ICC_PATH.exists(), reason="legacy ICC profile not available")
def test_predict_lab_icc_profile_runs_and_returns_plausible_lab():
    rgb_uint8 = np.array([[0, 0, 0], [255, 255, 255], [128, 64, 200]], dtype=np.uint8)
    lab = predict_lab_icc_profile(rgb_uint8, ICC_PATH)
    assert lab.shape == (3, 3)
    assert np.all(lab[:, 0] >= 0) and np.all(lab[:, 0] <= 100)
    # black should be darker than white
    assert lab[0, 0] < lab[1, 0]
