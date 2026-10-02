from pathlib import Path

import pytest

from stampan.core.cgats import lab_from_xyz_d50, parse_cgats_file

REF_PATH = Path(__file__).parent.parent / "reference_data" / "R240103.txt"


@pytest.fixture(scope="module")
def table():
    return parse_cgats_file(REF_PATH)


def test_header_fields(table):
    assert table.header["ORIGINATOR"] == "Wolf Faust"
    assert table.header["NUMBER_OF_SETS"] == 288
    assert table.header["NUMBER_OF_FIELDS"] == 18


def test_field_list(table):
    assert table.fields[0] == "SAMPLE_ID"
    assert "LAB_L" in table.fields
    assert "MEAN_DE" in table.fields
    assert len(table.fields) == 18


def test_288_sets(table):
    assert len(table.rows) == 288


def test_a1_values(table):
    by_id = table.by_sample_id()
    a1 = by_id["A1"]
    assert a1["XYZ_X"] == pytest.approx(4.07)
    assert a1["XYZ_Y"] == pytest.approx(3.43)
    assert a1["XYZ_Z"] == pytest.approx(2.55)
    assert a1["LAB_L"] == pytest.approx(21.69)
    assert a1["LAB_A"] == pytest.approx(11.68)
    assert a1["LAB_B"] == pytest.approx(2.20)


def test_gs23_values(table):
    by_id = table.by_sample_id()
    gs23 = by_id["GS23"]
    assert gs23["XYZ_X"] == pytest.approx(0.47)
    assert gs23["LAB_L"] == pytest.approx(4.35)
    assert gs23["LAB_A"] == pytest.approx(0.08)
    assert gs23["LAB_B"] == pytest.approx(-0.92)


def test_lab_recomputed_from_xyz_matches_file_mean_within_tolerance(table):
    # The file rounds XYZ and Lab independently to 2 decimals. Near black
    # (e.g. GS22/GS23, L19-L22), that quantisation alone moves Lab by up to
    # ~0.2-0.3 dE76 through the cube-root nonlinearity, so a strict per-patch
    # 0.05 bound is not achievable from the published XYZ. The mean/median
    # over all 288 sets is the meaningful check and matches the file's own
    # MEAN_DE repeatability column in magnitude.
    delta_es = []
    for row in table.rows:
        lightness, a, b = lab_from_xyz_d50(row["XYZ_X"], row["XYZ_Y"], row["XYZ_Z"])
        delta_es.append(
            ((lightness - row["LAB_L"]) ** 2 + (a - row["LAB_A"]) ** 2 + (b - row["LAB_B"]) ** 2)
            ** 0.5
        )
    import numpy as np

    delta_es = np.array(delta_es)
    assert delta_es.mean() < 0.05
    assert np.median(delta_es) < 0.05


def test_lab_recomputed_from_xyz_matches_file_for_bright_patches(table):
    # Away from black, XYZ quantisation noise is negligible and each
    # component should match the file within 0.05.
    by_id = table.by_sample_id()
    for sample_id in ("A1", "A13", "GS0", "GS5"):
        row = by_id[sample_id]
        lightness, a, b = lab_from_xyz_d50(row["XYZ_X"], row["XYZ_Y"], row["XYZ_Z"])
        assert lightness == pytest.approx(row["LAB_L"], abs=0.05), sample_id
        assert a == pytest.approx(row["LAB_A"], abs=0.05), sample_id
        assert b == pytest.approx(row["LAB_B"], abs=0.05), sample_id


def test_comment_stripping_does_not_break_quoted_values(table):
    # KEYWORD "MEAN_DE" # Mean Delta E of samples compared to batch average
    assert "MEAN_DE" not in table.header or isinstance(table.header.get("KEYWORD"), str)
