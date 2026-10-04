import numpy as np
import pytest

from stampan.core.naming import lab_d50_to_xyY_illuminant_c, munsell_notation


def test_lab_d50_to_xyY_illuminant_c_white_point_is_plausible():
    # A near-white Lab should land close to illuminant C's own chromaticity.
    xyY = lab_d50_to_xyY_illuminant_c(np.array([95.0, 0.0, 0.0]))
    assert xyY[0] == pytest.approx(0.310, abs=0.01)
    assert xyY[1] == pytest.approx(0.316, abs=0.01)
    assert xyY[2] == pytest.approx(0.87, abs=0.05)


def test_munsell_notation_end_to_end():
    # A moderately saturated red-ish Lab should round-trip to a plausible
    # Munsell notation (hue family R/YR, given the positive a*/b*).
    lab = np.array([40.0, 45.0, 25.0])
    result = munsell_notation(lab)
    assert result.warning is None
    assert result.munsell is not None
    assert "R" in result.munsell  # R, YR, or RP family, all plausible for this hue


def test_munsell_notation_handles_out_of_gamut_gracefully():
    # An implausibly saturated/extreme Lab should fail gracefully, not crash.
    lab = np.array([50.0, 200.0, 200.0])
    result = munsell_notation(lab)
    assert result.munsell is None
    assert result.warning is not None
