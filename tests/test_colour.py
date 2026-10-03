from pathlib import Path

import numpy as np
import pytest

from stampan.core.colour import (
    analyse_colour,
    circular_hue_mean,
    interior_mask,
    lab_to_display_srgb,
    render_cluster_overlay,
    render_scatter_plots,
    render_swatch_card,
    stamp_mask_from_crop,
)
from stampan.core.naming import load_iscc_nbs_table

TABLE_PATH = Path(__file__).parent.parent / "reference_data" / "iscc_nbs" / "color_names.txt"
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


@pytest.fixture(scope="module")
def iscc_table():
    return load_iscc_nbs_table(TABLE_PATH)


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


def test_analyse_colour_separates_paper_and_ink(iscc_table):
    crop = _two_tone_crop()
    analysis = analyse_colour(
        crop, _fake_predict_lab, PPM, PPM, n_inks=1, tau=0.9, iscc_table=iscc_table
    )
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
    assert paper.iscc_nbs_name is not None


def test_analyse_colour_warns_without_cancellation_mask(iscc_table):
    crop = _two_tone_crop()
    analysis = analyse_colour(crop, _fake_predict_lab, PPM, PPM, iscc_table=iscc_table)
    assert any("cancellation" in w for w in analysis.warnings)


def test_analyse_colour_excludes_cancellation_mask(iscc_table):
    crop = _two_tone_crop()
    cancel_mask = np.zeros(crop.shape[:2], dtype=np.uint8)
    cancel_mask[100:200, 80:170] = 255  # mask out the whole ink region

    analysis = analyse_colour(
        crop,
        _fake_predict_lab,
        PPM,
        PPM,
        n_inks=1,
        tau=0.9,
        cancel_mask=cancel_mask,
        iscc_table=iscc_table,
    )
    # with the ink region excluded, clustering sees ~one population (paper)
    labels = {c.label for c in analysis.result.clusters}
    assert (
        "ink1" not in labels
        or next(c for c in analysis.result.clusters if c.label == "ink1").pixel_count < 100
    )


def test_tau_threshold_increases_mixed_fraction(iscc_table):
    crop = _two_tone_crop()
    loose = analyse_colour(crop, _fake_predict_lab, PPM, PPM, tau=0.5, iscc_table=iscc_table)
    strict = analyse_colour(crop, _fake_predict_lab, PPM, PPM, tau=0.999, iscc_table=iscc_table)
    assert strict.result.mixed_pixel_fraction >= loose.result.mixed_pixel_fraction


def test_diagnostics_render_without_crashing(iscc_table):
    crop = _two_tone_crop()
    analysis = analyse_colour(crop, _fake_predict_lab, PPM, PPM, iscc_table=iscc_table)
    assert len(render_cluster_overlay(crop, analysis)) > 1000
    assert len(render_scatter_plots(analysis)) > 1000
    assert len(render_swatch_card(analysis)) > 1000


def test_too_few_pixels_warns_instead_of_crashing(iscc_table):
    # Regression test: an interior-mask margin that consumes a tiny region
    # entirely used to crash inside sklearn (0 samples) rather than fail
    # gracefully.
    tiny_crop = np.full((20, 20, 3), 3000, dtype=np.uint16)
    tiny_crop[5:15, 5:15] = [40000, 40000, 40000]
    analysis = analyse_colour(tiny_crop, _fake_predict_lab, PPM, PPM, iscc_table=iscc_table)
    assert any("pixels" in w for w in analysis.warnings)
    assert analysis.result.clusters == []


def test_nan_lab_pixels_are_dropped_not_crashed(iscc_table):
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

    analysis = analyse_colour(crop, predict_lab_with_nan_patch, PPM, PPM, iscc_table=iscc_table)
    assert any("NaN" in w for w in analysis.warnings)
    assert len(analysis.result.clusters) >= 1


def test_render_swatch_card_handles_zero_clusters(iscc_table):
    # Regression test: plt.subplots(0, 1, ...) crashed outright when the
    # too-few-pixels guard legitimately produces zero clusters; found via
    # the CLI on a real stamp crop.
    tiny_crop = np.full((20, 20, 3), 3000, dtype=np.uint16)
    tiny_crop[5:15, 5:15] = [40000, 40000, 40000]
    analysis = analyse_colour(tiny_crop, _fake_predict_lab, PPM, PPM, iscc_table=iscc_table)
    assert analysis.result.clusters == []
    png = render_swatch_card(analysis)
    assert len(png) > 100
