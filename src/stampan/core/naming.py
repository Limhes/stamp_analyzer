"""CIELAB D50 -> Munsell notation (SPEC §9).

Pipeline: Lab D50 -> XYZ D50 -> chromatic-adapt (Bradford/Von Kries CAT) to
illuminant C -> xyY -> Munsell (``colour.notation.munsell``, which assumes
illuminant C, matching the Munsell renotation data).

This module used to continue on to an ISCC-NBS name via Centore's
block-boundary system (``reference_data/iscc_nbs/``). René flagged that
table as flawed for this use; it has been removed. Colour naming now comes
from ``core.philatelic_colours`` (a stamp-catalogue/printing-ink name
reference, matched directly from Lab -- it does not go through Munsell at
all). Munsell notation is kept here as a standard, independently useful
scientific colour specification in its own right.
"""

from __future__ import annotations

from dataclasses import dataclass

import colour
import numpy as np

_OBSERVER = "CIE 1931 2 Degree Standard Observer"
_D50_WHITE_xy = colour.CCS_ILLUMINANTS[_OBSERVER]["D50"]
_C_WHITE_xy = colour.CCS_ILLUMINANTS[_OBSERVER]["C"]
_D50_WHITE_XYZ = colour.xyY_to_XYZ((*_D50_WHITE_xy, 100.0))
_C_WHITE_XYZ = colour.xyY_to_XYZ((*_C_WHITE_xy, 100.0))

CAT_METHOD = "Bradford"  # the chromatic-adaptation transform used D50 -> C


def lab_d50_to_xyY_illuminant_c(lab: np.ndarray) -> np.ndarray:
    """CIELAB D50 -> XYZ D50 -> Bradford-adapt to illuminant C -> xyY.

    Documented CAT choice (SPEC §9): Bradford, via colour-science's
    Von Kries-class adaptation with the Bradford transform matrix -- the
    de facto standard CAT, and consistent with what Centore himself used to
    build the Munsell renotation data' illuminant-C framing.
    """
    xyz_d50 = colour.Lab_to_XYZ(lab, illuminant=_D50_WHITE_xy) * 100.0
    xyz_c = colour.adaptation.chromatic_adaptation(
        xyz_d50, _D50_WHITE_XYZ, _C_WHITE_XYZ, method="Von Kries", transform=CAT_METHOD
    )
    return colour.XYZ_to_xyY(xyz_c / 100.0)


@dataclass
class MunsellResult:
    munsell: str | None
    xyY: tuple[float, float, float]
    warning: str | None = None


def munsell_notation(lab: np.ndarray) -> MunsellResult:
    """Lab D50 -> Munsell notation, with graceful failure outside the
    Munsell renotation gamut (SPEC §9)."""
    xyY = lab_d50_to_xyY_illuminant_c(np.asarray(lab, dtype=np.float64))
    try:
        munsell = colour.notation.munsell.xyY_to_munsell_colour(xyY)
    except Exception as exc:  # colour-science raises a bare Exception/ValueError
        return MunsellResult(
            munsell=None,
            xyY=tuple(xyY.tolist()),
            warning=f"outside Munsell renotation range: {exc}",
        )
    return MunsellResult(munsell=munsell, xyY=tuple(xyY.tolist()), warning=None)
