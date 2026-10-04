import numpy as np
import pytest

from stampan.core.perforation import (
    PROFILE_STEP_MM,
    _detect_holes,
    _detect_profile_roi,
    _mask_from_roi,
    _robust_envelope_fit,
    _robust_poly_fit,
    _roi_runs_from_mask,
    analyse_perforation,
    detect_stamp_box,
    manual_side_from_points,
    render_diagnostic,
)
from tests.synth import SideSpec, SynthSpec, generate_synthetic_stamp

PPM = 20.0


def _spec(seed: int, **side_overrides) -> SynthSpec:
    sides = {s: SideSpec(gauge=12.0) for s in ("top", "bottom", "left", "right")}
    sides.update(side_overrides)
    return SynthSpec(width_mm=20, height_mm=25, px_per_mm=PPM, sides=sides, seed=seed)


def test_clean_gauge_12_all_sides_accurate():
    img, _gt = generate_synthetic_stamp(_spec(1))
    analysis = analyse_perforation(img, PPM, PPM)
    for side_name, side in analysis.sides.items():
        assert side.kind == "perforated"
        assert side.gauge == pytest.approx(12.0, abs=0.05), side_name
        assert side.confidence > 0.5


@pytest.mark.parametrize("gauge", [8.0, 10.5, 12.0, 14.25, 16.0])
def test_gauge_sweep_clean(gauge):
    spec = _spec(2)
    for s in spec.sides.values():
        s.gauge = gauge
    img, _gt = generate_synthetic_stamp(spec)
    analysis = analyse_perforation(img, PPM, PPM)
    for side_name, side in analysis.sides.items():
        if side.hole_count >= 10:
            assert side.gauge == pytest.approx(gauge, abs=0.05), side_name


def test_rotation_does_not_break_measurement():
    spec = _spec(3)
    spec.rotation_deg = 2.5
    img, _gt = generate_synthetic_stamp(spec)
    analysis = analyse_perforation(img, PPM, PPM)
    for side_name, side in analysis.sides.items():
        assert side.kind == "perforated", side_name
        assert side.gauge == pytest.approx(12.0, abs=0.1), side_name


def test_flat_edges_not_misread_as_perforated_when_rotated():
    # Regression test: a rotated flat edge's sub-pixel render texture once
    # produced a spuriously "clean" ~14-gauge false periodicity because the
    # amplitude sanity check only ran after an (incorrectly passed)
    # periodicity check. See core/perforation.py's _classify_and_pick.
    spec = _spec(
        3,
        top=SideSpec(imperf=True),
        bottom=SideSpec(gauge=12.0),
        left=SideSpec(gauge=12.0),
        right=SideSpec(straight_edge=True),
    )
    spec.rotation_deg = 2.0
    img, _gt = generate_synthetic_stamp(spec)
    analysis = analyse_perforation(img, PPM, PPM)
    assert analysis.sides["top"].kind in ("imperf", "straight_edge")
    assert analysis.sides["top"].gauge is None
    assert analysis.sides["right"].kind in ("imperf", "straight_edge")
    assert analysis.sides["right"].gauge is None
    assert analysis.sides["bottom"].kind == "perforated"
    assert analysis.sides["bottom"].gauge == pytest.approx(12.0, abs=0.1)
    assert analysis.sides["left"].kind == "perforated"
    assert analysis.sides["left"].gauge == pytest.approx(12.0, abs=0.1)


def test_all_flat_edges_regardless_of_rotation():
    for rot in (0.0, -3.0, 2.0, 3.0):
        spec = _spec(
            4,
            top=SideSpec(imperf=True),
            bottom=SideSpec(imperf=True),
            left=SideSpec(imperf=True),
            right=SideSpec(imperf=True),
        )
        spec.rotation_deg = rot
        img, _gt = generate_synthetic_stamp(spec)
        analysis = analyse_perforation(img, PPM, PPM)
        for side_name, side in analysis.sides.items():
            assert side.gauge is None, f"rot={rot} side={side_name}"
            assert side.kind in ("imperf", "straight_edge")


def test_hole_diameter_and_spacing_reported():
    img, gt = generate_synthetic_stamp(_spec(5))
    analysis = analyse_perforation(img, PPM, PPM)
    side = analysis.sides["bottom"]
    assert side.hole_diameter_mm is not None
    assert 0.3 < side.hole_diameter_mm < 1.5  # plausible range for a 0.8mm hole
    assert side.spacing_std_mm is not None
    assert side.spacing_std_mm < 0.1


def test_manual_mode_matches_ground_truth_points():
    img, gt = generate_synthetic_stamp(_spec(6))
    holes = gt.holes["left"].centres_px
    gauge, uncertainty = manual_side_from_points(holes, PPM)
    assert gauge == pytest.approx(12.0, abs=0.01)
    assert uncertainty < 0.01


def test_render_diagnostic_does_not_crash():
    img, _gt = generate_synthetic_stamp(_spec(7))
    analysis = analyse_perforation(img, PPM, PPM)
    png = render_diagnostic(img, analysis)
    assert len(png) > 1000


def test_low_confidence_warns_on_few_holes():
    spec = _spec(8)
    for s in spec.sides.values():
        s.gauge = 16.0  # fewest holes at highest gauge on a 20x25mm stamp
    img, _gt = generate_synthetic_stamp(spec)
    analysis = analyse_perforation(img, PPM, PPM)
    top = analysis.sides["top"]  # shorter (20mm) side -> fewer holes
    assert top.confidence < 1.0


# --------------------------------------------------------------------------
# ROI masking (SPEC §6: exclude cancellation/broken-teeth noise from the
# gauge estimate, not just from the picture)
# --------------------------------------------------------------------------


def _clean_profile(gauge: float = 12.0, length_mm: float = 20.0):
    pitch = 20.0 / gauge
    s = np.arange(0, length_mm, PROFILE_STEP_MM)
    depth = 0.3 + 0.25 * np.sin(2 * np.pi * s / pitch)
    return s, depth


def test_detect_profile_roi_flags_an_injected_noise_burst():
    s, depth = _clean_profile()
    noisy = depth.copy()
    burst = (s >= 8.0) & (s <= 11.0)
    rng = np.random.default_rng(0)
    noisy[burst] += rng.normal(scale=2.0, size=int(burst.sum()))  # cancellation-scale chaos

    good = _detect_profile_roi(s, noisy, pitch_hint_mm=20.0 / 12.0)
    # The injected burst (plus its margin) must be excluded...
    assert not np.any(good[(s >= 8.3) & (s <= 10.7)])
    # ...but clean stretches well away from it must survive.
    assert np.all(good[(s >= 0.5) & (s <= 7.0)])
    assert np.all(good[(s >= 12.0) & (s <= 19.0)])


def test_detect_profile_roi_leaves_a_clean_profile_untouched():
    s, depth = _clean_profile()
    good = _detect_profile_roi(s, depth, pitch_hint_mm=20.0 / 12.0)
    assert np.all(good)


def test_roi_runs_from_mask_drops_short_fragments_and_reports_gaps():
    s = np.arange(0, 20.0, PROFILE_STEP_MM)
    good = np.ones(len(s), dtype=bool)
    good[(s >= 8.0) & (s <= 11.0)] = False  # a real excluded gap
    good[(s >= 15.0) & (s <= 15.1)] = False  # a short blip, but it still splits the long run
    runs = _roi_runs_from_mask(s, good)
    # The blip splits [11, 20) into two good runs, each still long enough
    # to keep on its own.
    assert len(runs) == 3
    assert runs[0][1] < 8.0
    assert runs[1][0] > 11.0 and runs[1][1] < 15.0
    assert runs[2][0] > 15.1


def test_mask_from_roi_round_trips_runs():
    s = np.arange(0, 20.0, PROFILE_STEP_MM)
    roi = [(0.0, 7.9), (11.1, 20.0)]
    mask = _mask_from_roi(s, roi)
    assert not mask[np.searchsorted(s, 9.0)]
    assert mask[np.searchsorted(s, 2.0)]


def test_detect_holes_excludes_peaks_outside_good_mask_and_handles_gaps():
    s, depth = _clean_profile()
    good = np.ones(len(s), dtype=bool)
    good[(s >= 8.0) & (s <= 11.0)] = False  # drop a couple of real holes in this gap

    holes_unmasked, gauge_unmasked, *_ = _detect_holes(s, depth, gauge_hint=12.0)
    holes_masked, gauge_masked, *_ = _detect_holes(s, depth, gauge_hint=12.0, good_mask=good)

    assert len(holes_masked) < len(holes_unmasked)
    assert all(not (8.0 <= h.s_mm <= 11.0) for h in holes_masked)
    # Losing a contiguous run of holes must not bias the gauge estimate --
    # position-based indexing (not detection order) has to absorb the gap.
    assert gauge_masked == pytest.approx(gauge_unmasked, abs=0.02)


def test_detect_stamp_box_matches_manual_box_result():
    # The GUI seeds its editable rectangle from detect_stamp_box, then feeds
    # whatever the user ends up with back in as manual_box_px -- round-tripping
    # the untouched auto box must reproduce the plain auto-detected result.
    img, _gt = generate_synthetic_stamp(_spec(10))
    box = detect_stamp_box(img)
    assert box.shape == (4, 2)
    auto = analyse_perforation(img, PPM, PPM)
    manual = analyse_perforation(img, PPM, PPM, manual_box_px=box)
    for side_name in ("top", "bottom", "left", "right"):
        assert manual.sides[side_name].gauge == pytest.approx(auto.sides[side_name].gauge, abs=1e-6)


def test_manual_box_nudges_still_recover_the_gauge():
    # Simulate what a user's drag-handle edit looks like in the GUI: the
    # auto box, nudged by a few px. A user-supplied box must be honoured
    # (not silently replaced by a fresh auto-detection) and still work.
    img, _gt = generate_synthetic_stamp(_spec(11))
    box = detect_stamp_box(img)
    nudged = box + np.array([1.5, -1.0])
    analysis = analyse_perforation(img, PPM, PPM, manual_box_px=nudged)
    for side_name, side in analysis.sides.items():
        assert side.kind == "perforated", side_name
        assert side.gauge == pytest.approx(12.0, abs=0.1), side_name


# --------------------------------------------------------------------------
# Condition/multiplicity wiring (SPEC §6: these are Stamp's own catalogue
# fields, threaded through because they bear on how trustworthy a gauge
# is, even though neither changes the measurement itself)
# --------------------------------------------------------------------------


def test_on_paper_condition_caps_confidence_and_warns():
    img, _gt = generate_synthetic_stamp(_spec(12))
    plain = analyse_perforation(img, PPM, PPM)
    on_paper = analyse_perforation(img, PPM, PPM, condition="on_paper")
    for side_name, side in on_paper.sides.items():
        plain_side = plain.sides[side_name]
        if plain_side.confidence > 0.0:
            assert side.confidence <= 0.5
            assert side.confidence <= plain_side.confidence
            assert any("on paper" in w for w in side.warnings)
    assert any("on paper" in w for w in on_paper.warnings)


def test_soaked_off_condition_does_not_cap_confidence():
    img, _gt = generate_synthetic_stamp(_spec(13))
    plain = analyse_perforation(img, PPM, PPM)
    soaked = analyse_perforation(img, PPM, PPM, condition="soaked_off")
    for side_name, side in soaked.sides.items():
        assert side.confidence == pytest.approx(plain.sides[side_name].confidence)
    assert not any("on paper" in w for w in soaked.warnings)


def test_grouped_multiplicity_adds_a_warning_single_does_not():
    img, _gt = generate_synthetic_stamp(_spec(14))
    single = analyse_perforation(img, PPM, PPM, multiplicity="single")
    pair = analyse_perforation(img, PPM, PPM, multiplicity="pair", multiplicity_count=2)
    assert not any("shared with a neighbouring" in w for w in single.warnings)
    assert any("shared with a neighbouring" in w for w in pair.warnings)
    assert any("pair of 2" in w for w in pair.warnings)


def test_manual_roi_override_is_recorded_and_used():
    img, _gt = generate_synthetic_stamp(_spec(9))
    # The profile's own coordinate origin is each side's midpoint, not its
    # start -- use a generously wide override rather than assume the exact
    # range, while still proving the override is actually threaded through.
    override = {"top": [(-100.0, 100.0)]}
    analysis = analyse_perforation(img, PPM, PPM, manual_roi=override)
    top = analysis.sides["top"]
    assert top.roi_manual
    assert top.roi_mm == [(-100.0, 100.0)]
    assert top.gauge == pytest.approx(12.0, abs=0.1)


# --------------------------------------------------------------------------
# Robust tip-line fit (SPEC §6: "the line isn't on the teeth" -- a stretch
# of contour corrupted by cancellation ink must not drag the tip-line fit
# off the true tooth-tip envelope)
# --------------------------------------------------------------------------


def test_robust_line_fit_rejects_a_corrupted_stretch():
    rng = np.random.default_rng(0)
    x = np.linspace(-10, 10, 200)
    true_slope, true_intercept = 0.0, 5.0
    y = true_slope * x + true_intercept + rng.normal(scale=0.02, size=len(x))

    # Corrupt a contiguous minority stretch the way cancellation ink merging
    # with the background would: a large, one-sided offset, not just noise.
    corrupt = (x >= 2.0) & (x <= 6.0)
    y_corrupt = y.copy()
    y_corrupt[corrupt] += 3.0

    naive = np.polyfit(x, y_corrupt, 1)
    robust, inliers = _robust_poly_fit(x, y_corrupt, degree=1)

    # The naive OLS fit is visibly dragged off the true (near-zero slope,
    # intercept 5) line by the corrupted stretch; the robust fit recovers it
    # and flags the corrupted points as outliers.
    assert abs(robust[1] - true_intercept) < 0.1
    assert abs(naive[1] - true_intercept) > 0.3
    assert not np.any(inliers[corrupt])
    assert np.all(inliers[~corrupt])


def test_robust_line_fit_matches_plain_polyfit_on_clean_data():
    x = np.linspace(-10, 10, 100)
    y = 0.05 * x + 2.0
    robust, inliers = _robust_poly_fit(x, y, degree=1)
    plain = np.polyfit(x, y, 1)
    assert np.allclose(robust, plain, atol=1e-6)
    assert np.all(inliers)


def test_envelope_fit_stays_linear_on_clean_flat_data():
    rng = np.random.default_rng(1)
    x = np.linspace(-10, 10, 200)
    y = 0.05 * x + 2.0 + rng.normal(scale=0.01, size=len(x))
    coeffs, mask = _robust_envelope_fit(x, y)
    assert len(coeffs) == 2
    assert mask.mean() > 0.95  # a stray point near the clip threshold is fine


def test_envelope_fit_upgrades_to_quadratic_on_genuine_curvature():
    rng = np.random.default_rng(2)
    x = np.linspace(-10, 10, 200)
    true_quad = 0.03
    y = true_quad * x**2 + 0.05 * x + 2.0 + rng.normal(scale=0.01, size=len(x))
    coeffs, _mask = _robust_envelope_fit(x, y)
    assert len(coeffs) == 3
    assert coeffs[0] == pytest.approx(true_quad, abs=0.01)


def test_envelope_fit_stays_linear_when_too_few_points_for_quadratic():
    rng = np.random.default_rng(3)
    x = np.linspace(-10, 10, 15)
    y = 0.03 * x**2 + 0.05 * x + 2.0 + rng.normal(scale=0.01, size=len(x))
    coeffs, _mask = _robust_envelope_fit(x, y)
    assert len(coeffs) == 2
