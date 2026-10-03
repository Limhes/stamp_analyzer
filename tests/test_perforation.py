import pytest

from stampan.core.perforation import analyse_perforation, manual_side_from_points, render_diagnostic
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
