import numpy as np
import pytest

from stampan.core.colour import (
    analyse_colour,
    build_colour_result_from_rois,
    circular_hue_mean,
    interior_mask,
    lab_to_display_srgb,
    measure_roi_colour,
    render_cluster_overlay,
    render_colour_overview,
    render_roi_scatter,
    render_scatter_plots,
    render_swatch_card,
    stamp_mask_from_crop,
)
from stampan.core.models import ColourCluster

PPM = 20.0


def _fake_predict_lab(rgb_flat: np.ndarray) -> np.ndarray:
    """A crude but deterministic stand-in for a real characterisation
    model, good enough to exercise clustering/statistics logic without
    needing a real Reference."""
    rgb = rgb_flat / 65535.0
    lightness = rgb[:, 0] * 60 + 20
    a = (rgb[:, 0] - rgb[:, 1]) * 100
    b = (rgb[:, 1] - rgb[:, 2]) * 100
    return np.stack([lightness, a, b], axis=1)


def _two_tone_crop(
    paper_rgb=(45000, 42000, 40000), ink_rgb=(20000, 6000, 6000), background=3000
) -> np.ndarray:
    h, w = 300, 250
    crop = np.full((h, w, 3), background, dtype=np.uint16)
    crop[20:280, 20:230] = paper_rgb
    crop[100:200, 80:170] = ink_rgb
    return crop


def test_stamp_mask_recovers_full_rectangle_despite_dark_ink():
    # Regression test: a sizeable solid-ink region darker than paper once
    # made a single global Otsu threshold split *between* ink and paper
    # (not between background and ink+paper), silently excising the ink
    # region as "background" and vanishing it from every colour statistic.
    crop = _two_tone_crop()
    mask = stamp_mask_from_crop(crop)
    # both paper and ink regions must be included
    assert mask[50, 50] == 255  # paper region
    assert mask[150, 120] == 255  # ink region
    assert mask[5, 5] == 0  # background margin


def test_interior_mask_erodes_by_margin():
    crop = _two_tone_crop()
    mask = stamp_mask_from_crop(crop)
    interior = interior_mask(mask, margin_mm=1.0, px_per_mm_x=PPM, px_per_mm_y=PPM)
    # 1mm * 20px/mm = 20px eroded in from the paper boundary at x=20
    assert interior[150, 25] == 0  # just inside the stamp edge: eroded away
    assert interior[150, 60] == 255  # well inside: still present


def test_circular_hue_mean_wraps_correctly():
    # Hues straddling the 0/360 boundary should average near 0, not 180.
    hues = np.array([350.0, 10.0])
    weights = np.array([1.0, 1.0])
    mean = circular_hue_mean(hues, weights)
    assert mean == pytest.approx(0.0, abs=1.0) or mean == pytest.approx(360.0, abs=1.0)


def test_lab_to_display_srgb_white_is_near_white():
    srgb, out_of_gamut = lab_to_display_srgb(np.array([95.0, 0.0, 0.0]))
    assert not out_of_gamut
    assert all(v > 220 for v in srgb)


def test_lab_to_display_srgb_flags_extreme_out_of_gamut():
    _srgb, out_of_gamut = lab_to_display_srgb(np.array([50.0, 150.0, 150.0]))
    assert out_of_gamut


def test_analyse_colour_separates_paper_and_ink():
    crop = _two_tone_crop()
    analysis = analyse_colour(crop, _fake_predict_lab, PPM, PPM, n_inks=1, tau=0.9)
    assert len(analysis.result.clusters) == 2
    labels = {c.label for c in analysis.result.clusters}
    assert labels == {"paper", "ink1"}

    paper = next(c for c in analysis.result.clusters if c.label == "paper")
    ink = next(c for c in analysis.result.clusters if c.label == "ink1")
    assert paper.area_fraction > ink.area_fraction
    assert paper.mean_lab[0] > ink.mean_lab[0]  # paper is lighter
    assert ink.core_mean_lab is not None
    assert paper.core_mean_lab is None  # core-ink estimate only applies to ink clusters
    assert "ink1" in analysis.result.ink_paper_delta_e00
    assert analysis.result.ink_paper_delta_e00["ink1"] > 5  # clearly distinct colours

    assert paper.munsell is not None
    assert ink.munsell is not None
    assert paper.philatelic_name is not None


def test_analyse_colour_warns_without_inclusion_mask():
    # There is no automatic cancellation exclusion (SPEC §7 pipeline was
    # dropped as unreliable); the user is expected to select the area to
    # analyse directly instead, and skipping that is worth a warning.
    crop = _two_tone_crop()
    analysis = analyse_colour(crop, _fake_predict_lab, PPM, PPM)
    assert any("inclusion" in w for w in analysis.warnings)


def test_analyse_colour_restricts_to_user_inclusion_mask():
    # The intended replacement for automatic cancellation exclusion: the
    # user selects a clean area (e.g. paper only, away from any
    # cancellation or stray ink) and only that area is analysed.
    crop = _two_tone_crop()
    paper_only_mask = np.zeros(crop.shape[:2], dtype=np.uint8)
    paper_only_mask[200:270, 30:220] = 255  # a strip of paper, below the ink block

    analysis = analyse_colour(
        crop,
        _fake_predict_lab,
        PPM,
        PPM,
        n_inks=1,
        tau=0.9,
        user_inclusion_mask=paper_only_mask,
    )
    labels = {c.label for c in analysis.result.clusters}
    assert "ink1" not in labels
    assert not any("inclusion" in w for w in analysis.warnings)


def test_tau_threshold_increases_mixed_fraction():
    crop = _two_tone_crop()
    loose = analyse_colour(crop, _fake_predict_lab, PPM, PPM, tau=0.5)
    strict = analyse_colour(crop, _fake_predict_lab, PPM, PPM, tau=0.999)
    assert strict.result.mixed_pixel_fraction >= loose.result.mixed_pixel_fraction


def test_diagnostics_render_without_crashing():
    crop = _two_tone_crop()
    analysis = analyse_colour(crop, _fake_predict_lab, PPM, PPM)
    assert len(render_cluster_overlay(crop, analysis)) > 1000
    assert len(render_scatter_plots(analysis)) > 1000
    assert len(render_swatch_card(analysis)) > 1000


def test_too_few_pixels_warns_instead_of_crashing():
    # Regression test: an interior-mask margin that consumes a tiny region
    # entirely used to crash inside sklearn (0 samples) rather than fail
    # gracefully.
    tiny_crop = np.full((20, 20, 3), 3000, dtype=np.uint16)
    tiny_crop[5:15, 5:15] = [40000, 40000, 40000]
    analysis = analyse_colour(tiny_crop, _fake_predict_lab, PPM, PPM)
    assert any("pixels" in w for w in analysis.warnings)
    assert analysis.result.clusters == []


def test_nan_lab_pixels_are_dropped_not_crashed():
    # Regression test: lut_delaunay (the chosen model for every reference
    # built so far) returns NaN outside its training patches' convex hull.
    # Real scans routinely have such pixels (dark ink, specular highlights)
    # and this crashed GaussianMixture.fit on a real stamp.
    crop = _two_tone_crop()

    h, w = crop.shape[:2]

    def predict_lab_with_nan_patch(rgb_flat: np.ndarray) -> np.ndarray:
        lab = _fake_predict_lab(rgb_flat)
        # a block inside the paper region (well within the interior mask),
        # not the background margin that's already excluded regardless.
        img = lab.reshape(h, w, 3)
        img[40:50, 40:60] = np.nan
        return img.reshape(-1, 3)

    analysis = analyse_colour(crop, predict_lab_with_nan_patch, PPM, PPM)
    assert any("NaN" in w for w in analysis.warnings)
    assert len(analysis.result.clusters) >= 1


def test_render_swatch_card_handles_zero_clusters():
    # Regression test: plt.subplots(0, 1, ...) crashed outright when the
    # too-few-pixels guard legitimately produces zero clusters; found via
    # the CLI on a real stamp crop.
    tiny_crop = np.full((20, 20, 3), 3000, dtype=np.uint16)
    tiny_crop[5:15, 5:15] = [40000, 40000, 40000]
    analysis = analyse_colour(tiny_crop, _fake_predict_lab, PPM, PPM)
    assert analysis.result.clusters == []
    png = render_swatch_card(analysis)
    assert len(png) > 100


def test_render_colour_overview_multiple_stamps():
    ink1 = ColourCluster(
        label="ink1",
        mean_lab=(30.0, 50.0, 30.0),
        mean_lch=(30.0, 58.3, 31.0),
        srgb_swatch=(150, 20, 20),
    )
    ink2 = ColourCluster(
        label="ink1",
        mean_lab=(20.0, 10.0, -45.0),
        mean_lch=(20.0, 46.1, 282.0),
        srgb_swatch=(20, 30, 120),
    )
    png = render_colour_overview([("stamp_a/ink1", ink1), ("stamp_b/ink1", ink2)])
    assert len(png) > 1000


def test_render_colour_overview_handles_no_entries():
    png = render_colour_overview([])
    assert len(png) > 100


def test_measure_roi_colour_on_ink_region():
    crop = _two_tone_crop()
    mask = np.zeros(crop.shape[:2], dtype=np.uint8)
    mask[100:200, 80:170] = 255  # exactly the ink block
    cluster, warnings = measure_roi_colour(crop, _fake_predict_lab, mask, "ink1")
    assert cluster is not None
    assert cluster.label == "ink1"
    assert cluster.mean_lab is not None
    assert cluster.philatelic_name is not None
    assert warnings == []


def test_measure_roi_colour_empty_mask_warns_not_crashes():
    crop = _two_tone_crop()
    mask = np.zeros(crop.shape[:2], dtype=np.uint8)
    cluster, warnings = measure_roi_colour(crop, _fake_predict_lab, mask, "ink1")
    assert cluster is None
    assert any("empty" in w for w in warnings)


def test_build_colour_result_from_rois_multiple_named_regions():
    crop = _two_tone_crop()
    paper_mask = np.zeros(crop.shape[:2], dtype=np.uint8)
    paper_mask[200:270, 30:220] = 255
    ink_mask = np.zeros(crop.shape[:2], dtype=np.uint8)
    ink_mask[100:200, 80:170] = 255

    result = build_colour_result_from_rois(
        crop, _fake_predict_lab, [("paper", paper_mask), ("ink1", ink_mask)]
    )
    assert {c.label for c in result.clusters} == {"paper", "ink1"}
    assert result.meta.confidence == 1.0


def test_render_roi_scatter_without_crashing():
    crop = _two_tone_crop()
    mask = np.zeros(crop.shape[:2], dtype=np.uint8)
    mask[100:200, 80:170] = 255
    png = render_roi_scatter(crop, _fake_predict_lab, mask, "ink1")
    assert len(png) > 1000
