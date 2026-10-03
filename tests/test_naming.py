from pathlib import Path

import numpy as np
import pytest

from stampan.core.naming import (
    astm_hue,
    lab_d50_to_xyY_illuminant_c,
    load_iscc_nbs_table,
    munsell_to_iscc_nbs,
    name_lab_colour,
    parse_munsell_string,
)

TABLE_PATH = Path(__file__).parent.parent / "reference_data" / "iscc_nbs" / "color_names.txt"


@pytest.fixture(scope="module")
def table():
    return load_iscc_nbs_table(TABLE_PATH)


def test_load_table_counts(table):
    assert len(table.level1_names) == 13
    assert len(table.level2_names) == 29
    assert len(table.level3) == 267


def test_parse_chromatic_munsell():
    spec = parse_munsell_string("5.48R5.04/2.35")
    assert spec.family == "R"
    assert spec.number == pytest.approx(5.48)
    assert spec.value == pytest.approx(5.04)
    assert spec.chroma == pytest.approx(2.35)
    assert not spec.is_neutral


def test_parse_neutral_munsell():
    spec = parse_munsell_string("N4")
    assert spec.is_neutral
    assert spec.value == pytest.approx(4.0)
    assert spec.chroma == 0.0


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_munsell_string("not a colour")


def test_astm_hue_family_boundaries():
    # 10R borders YR; by definition that's the same point as "0YR".
    assert astm_hue("R", 10.0) == pytest.approx(10.0)
    # 10RP borders R, i.e. hue 100 == hue 0 (we report 100 at the boundary).
    assert astm_hue("RP", 10.0) == pytest.approx(100.0)
    assert astm_hue("YR", 5.0) == pytest.approx(15.0)


def test_munsell_to_iscc_nbs_known_black(table):
    result = munsell_to_iscc_nbs("N0", table)
    assert result.level3_name == "Black"
    assert result.level1_name == "Black"


def test_munsell_to_iscc_nbs_matches_csv_example(table):
    # From the regression CSV: 0,20,90 sRGB -> 6.37PB1.04/9.73 -> Deep blue (179)
    result = munsell_to_iscc_nbs("6.37PB1.04/9.73", table)
    assert result.level3_index == 179
    assert result.level3_name == "Deep blue"


def test_lab_d50_to_xyY_illuminant_c_white_point_is_plausible():
    # A near-white Lab should land close to illuminant C's own chromaticity.
    xyY = lab_d50_to_xyY_illuminant_c(np.array([95.0, 0.0, 0.0]))
    assert xyY[0] == pytest.approx(0.310, abs=0.01)
    assert xyY[1] == pytest.approx(0.316, abs=0.01)
    assert xyY[2] == pytest.approx(0.87, abs=0.05)


def test_name_lab_colour_end_to_end(table):
    # A moderately saturated red-ish Lab should round-trip to a sensible name.
    lab = np.array([40.0, 45.0, 25.0])
    result = name_lab_colour(lab, table)
    assert result.warning is None
    assert result.iscc_nbs is not None
    assert result.iscc_nbs.level1_name in ("Red", "Pink", "Brown", "Orange")


def test_name_lab_colour_handles_out_of_gamut_gracefully(table):
    # An implausibly saturated/extreme Lab should fail gracefully, not crash.
    lab = np.array([50.0, 200.0, 200.0])
    result = name_lab_colour(lab, table)
    assert result.iscc_nbs is None
    assert result.warning is not None
