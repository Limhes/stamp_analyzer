"""CIELAB D50 -> Munsell -> ISCC-NBS colour naming (SPEC §9).

Pipeline: Lab D50 -> XYZ D50 -> chromatic-adapt (Bradford/Von Kries CAT) to
illuminant C -> xyY -> Munsell (``colour.notation.munsell``, which assumes
illuminant C, matching the Munsell renotation data) -> ISCC-NBS Level 1/2/3
via Centore's block-boundary system.

The ISCC-NBS block logic is ported from Centore's MATLAB original
(``reference_data/iscc_nbs/MunsellToISCCNBS.m``): the per-side classifier
is taken directly from René's existing line-for-line Python translation
(``MunsellToISCCNBS_adapt.py``, ``find_color()``) rather than re-typing
~1600 lines of near-identical if/elif ranges by hand, since SPEC explicitly
allows "verify one existing port line by line ... or port fresh" and a
135k-row regression test (Centore's own sRGB->Munsell->ISCC-NBS table) is a
far stronger, more scalable correctness check than manual diffing would be
here. Only the ASTM-hue conversion and the Level-index-to-name lookup
(``color_names.txt``) were written fresh, since the adapted port has
neither.
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path

import colour
import numpy as np

# Munsell hue families, in increasing-hue order (the order the hue circle
# actually advances in: R -> YR -> Y -> GY -> G -> BG -> B -> PB -> P -> RP
# -> back to R). ASTM hue = family_index*10 + number, mod 100 (100 == 0,
# the R/RP boundary); within one family, hue INCREASES with increasing
# Munsell number, reaching the next family's start at number=10.
FAMILIES = ("R", "YR", "Y", "GY", "G", "BG", "B", "PB", "P", "RP")

_MUNSELL_CHROMATIC_RE = re.compile(
    r"^\s*(?P<number>\d+(?:\.\d+)?)\s*(?P<family>[A-Z]+)\s*(?P<value>\d+(?:\.\d+)?)\s*/\s*"
    r"(?P<chroma>\d+(?:\.\d+)?)\s*$"
)
_MUNSELL_NEUTRAL_RE = re.compile(r"^\s*N\s*(?P<value>\d+(?:\.\d+)?)\s*$")


def _load_find_color():
    """Load find_color() from the adapted Centore port without needing it
    on sys.path as a package (it lives in reference_data/, not src/)."""
    path = (
        Path(__file__).resolve().parents[3]
        / "reference_data"
        / "iscc_nbs"
        / "MunsellToISCCNBS_adapt.py"
    )
    spec = importlib.util.spec_from_file_location("_munsell_to_iscc_nbs_adapt", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.find_color


_find_color = _load_find_color()


@dataclass
class MunsellSpec:
    family: str | None  # None for neutral (grey)
    number: float | None
    value: float
    chroma: float

    @property
    def is_neutral(self) -> bool:
        return self.family is None


def parse_munsell_string(s: str) -> MunsellSpec:
    """Parse e.g. "5.48R5.04/2.35" or "N4" (with or without the space some
    formatters put between hue and value)."""
    m = _MUNSELL_NEUTRAL_RE.match(s)
    if m:
        return MunsellSpec(family=None, number=None, value=float(m.group("value")), chroma=0.0)
    m = _MUNSELL_CHROMATIC_RE.match(s)
    if not m:
        raise ValueError(f"unparseable Munsell specification: {s!r}")
    family = m.group("family")
    if family not in FAMILIES:
        raise ValueError(f"unknown Munsell hue family {family!r} in {s!r}")
    return MunsellSpec(
        family=family,
        number=float(m.group("number")),
        value=float(m.group("value")),
        chroma=float(m.group("chroma")),
    )


def astm_hue(family: str, number: float) -> float:
    """Munsell hue family + number -> the 1-100 ASTM hue scale used by
    Centore's block logic. Returns 100.0 rather than 0.0 at the R/RP
    boundary, matching find_color()'s `hue >= 1` style range checks."""
    idx = FAMILIES.index(family)
    hue = (idx * 10 + number) % 100
    return 100.0 if hue == 0 else hue


# --------------------------------------------------------------------------
# Level index -> name lookup (color_names.txt)
# --------------------------------------------------------------------------


@dataclass
class ISCCNBSLevel3Entry:
    level3: int
    level2: int
    level1: int
    designator: str
    abbreviation: str


@dataclass
class ISCCNBSTable:
    level1_names: dict[int, str]
    level2_names: dict[int, str]
    level3: dict[int, ISCCNBSLevel3Entry]


def load_iscc_nbs_table(path: str | Path) -> ISCCNBSTable:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    level1_names: dict[int, str] = {}
    level2_names: dict[int, str] = {}
    level3: dict[int, ISCCNBSLevel3Entry] = {}

    section = None
    for line in lines:
        line = line.rstrip()
        if (
            not line
            or line.startswith("Level1\t")
            or line.startswith("Level2\t")
            or line.startswith("Level3\t")
        ):
            section = {"Level1\t": "L1", "Level2\t": "L2", "Level3\t": "L3"}.get(line[:7])
            continue
        if "DESCRIPTION ENDS HERE" in line:
            section = None
            continue
        parts = line.split("\t")
        if section == "L1" and len(parts) >= 2:
            level1_names[int(parts[0])] = parts[1]
        elif section == "L2" and len(parts) >= 3:
            level2_names[int(parts[0])] = parts[2]
        elif section == "L3" and len(parts) >= 5:
            level3[int(parts[0])] = ISCCNBSLevel3Entry(
                level3=int(parts[0]),
                level2=int(parts[1]),
                level1=int(parts[2]),
                designator=parts[3],
                abbreviation=parts[4],
            )
    return ISCCNBSTable(level1_names=level1_names, level2_names=level2_names, level3=level3)


# --------------------------------------------------------------------------
# Munsell -> ISCC-NBS
# --------------------------------------------------------------------------


@dataclass
class ISCCNBSResult:
    munsell: str
    level3_index: int
    level2_index: int
    level1_index: int
    level3_name: str
    level2_name: str
    level1_name: str
    abbreviation: str


def munsell_to_iscc_nbs(munsell: str, table: ISCCNBSTable) -> ISCCNBSResult:
    spec = parse_munsell_string(munsell)
    hue = astm_hue(spec.family, spec.number) if not spec.is_neutral else 0.0
    level3_index = _find_color(hue, spec.chroma, spec.value)
    entry = table.level3.get(level3_index)
    if entry is None:
        raise ValueError(f"no ISCC-NBS block matched Munsell {munsell!r} (hue={hue})")
    return ISCCNBSResult(
        munsell=munsell,
        level3_index=entry.level3,
        level2_index=entry.level2,
        level1_index=entry.level1,
        level3_name=entry.designator,
        level2_name=table.level2_names.get(entry.level2, "?"),
        level1_name=table.level1_names.get(entry.level1, "?"),
        abbreviation=entry.abbreviation,
    )


# --------------------------------------------------------------------------
# Lab D50 -> xyY (illuminant C) -> Munsell
# --------------------------------------------------------------------------

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
class NamingResult:
    iscc_nbs: ISCCNBSResult | None
    munsell: str | None
    xyY: tuple[float, float, float] | None
    warning: str | None


def name_lab_colour(lab: np.ndarray, table: ISCCNBSTable) -> NamingResult:
    """Full pipeline: Lab D50 -> ... -> ISCC-NBS name, with graceful
    failure outside the Munsell renotation gamut (SPEC §9 step 2)."""
    xyY = lab_d50_to_xyY_illuminant_c(np.asarray(lab, dtype=np.float64))
    try:
        munsell = colour.notation.munsell.xyY_to_munsell_colour(xyY)
    except Exception as exc:  # colour-science raises a bare Exception/ValueError
        return NamingResult(
            iscc_nbs=None,
            munsell=None,
            xyY=tuple(xyY.tolist()),
            warning=f"outside Munsell renotation range: {exc}",
        )
    try:
        result = munsell_to_iscc_nbs(munsell, table)
    except ValueError as exc:
        return NamingResult(
            iscc_nbs=None, munsell=munsell, xyY=tuple(xyY.tolist()), warning=str(exc)
        )
    return NamingResult(iscc_nbs=result, munsell=munsell, xyY=tuple(xyY.tolist()), warning=None)
