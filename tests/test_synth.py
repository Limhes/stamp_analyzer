import numpy as np

from tests.synth import SideSpec, SynthSpec, generate_synthetic_stamp


def test_generate_basic_shape_and_dtype():
    spec = SynthSpec(seed=0)
    img, gt = generate_synthetic_stamp(spec)
    assert img.dtype == np.uint16
    assert img.shape == (gt.scene_size_px[1], gt.scene_size_px[0])


def test_background_is_dark_and_stamp_is_light():
    spec = SynthSpec(seed=1, rotation_deg=0.0)
    img, gt = generate_synthetic_stamp(spec)
    corner = img[:10, :10].mean()
    cx, cy = int(gt.stamp_centre_px[0]), int(gt.stamp_centre_px[1])
    centre = img[cy - 5 : cy + 5, cx - 5 : cx + 5].mean()
    assert corner < centre
    assert corner < 0.2 * 65535


def test_hole_counts_match_requested_gauge():
    spec = SynthSpec(
        width_mm=20.0,
        height_mm=25.0,
        seed=2,
        sides={s: SideSpec(gauge=12.0) for s in ("top", "bottom", "left", "right")},
    )
    _, gt = generate_synthetic_stamp(spec)
    top_n = len(gt.holes["top"].centres_px)
    expected = round(20.0 / (20.0 / 12.0)) + 1
    # tolerate off-by-one from rounding of (length / pitch)
    assert abs(top_n - expected) <= 1
    assert top_n >= 8


def test_imperf_side_has_no_holes():
    spec = SynthSpec(
        seed=3,
        sides={
            "top": SideSpec(imperf=True),
            "bottom": SideSpec(gauge=12.0),
            "left": SideSpec(gauge=12.0),
            "right": SideSpec(straight_edge=True),
        },
    )
    _, gt = generate_synthetic_stamp(spec)
    assert gt.holes["top"].centres_px == []
    assert gt.holes["top"].imperf is True
    assert gt.holes["right"].straight_edge is True
    assert len(gt.holes["bottom"].centres_px) > 0


def test_rotation_is_recorded_in_ground_truth():
    spec = SynthSpec(seed=4, rotation_deg=2.5)
    _, gt = generate_synthetic_stamp(spec)
    assert gt.rotation_deg == 2.5


def test_noise_increases_pixel_variance_in_background():
    base_spec = SynthSpec(seed=5, noise_sigma=0.0)
    noisy_spec = SynthSpec(seed=5, noise_sigma=500.0)
    img_base, _ = generate_synthetic_stamp(base_spec)
    img_noisy, _ = generate_synthetic_stamp(noisy_spec)
    assert img_noisy[:20, :20].std() > img_base[:20, :20].std()
