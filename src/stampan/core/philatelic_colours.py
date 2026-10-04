"""CIELAB D50 -> a philatelic colour name (SPEC §9, replacing ISCC-NBS).

René flagged Centore's ISCC-NBS block system (the previous naming source,
``reference_data/iscc_nbs/``) as flawed for this use and asked for a
dedicated reference built from stamp-catalogue/printing-ink colour
vocabulary instead (carmine, ultramarine, cobalt blue, ...), combined with
an ISCC-NBS-*like* structure: a lightness/chroma modifier composed with a
base hue name, rather than one fixed list of discrete swatches.

**Provenance, read before trusting a result for catalogue work.** The
reference table (``reference_data/philatelic_colours.csv``) is a
compilation I (Claude) put together from general colour-science/printing-
ink knowledge of what these classic names typically look like on paper --
it is **not** digitised from a specific philatelic catalogue (Yvert,
Stanley Gibbons, Michel) or from spectrophotometer measurements of actual
ink swatches. Treat the Lab values as reasonable approximate anchors, good
for giving measurements a human-readable, domain-appropriate name and for
comparing stamps to *each other*, but not yet as calibrated catalogue
ground truth. If exact catalogue-colour matching matters, this table should
be replaced or extended with values measured from a real colour reference
(a printed colour guide, or spectros of known-colour stamps) -- the module
is structured so that only the CSV needs to change to do that.

Naming method: nearest reference colour by ΔE00 (not Centore's block
geometry -- there is no equivalent published block system for this
vocabulary), then a modifier derived from how the input's L*/C* compare to
the matched anchor's own L*/C*: "pale"/"light", "dark"/"deep", "grayish",
"vivid". This mirrors ISCC-NBS's own compositional structure (a modifier
plus a hue name) without pretending to its decades of psychophysical block
calibration.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import colour
import numpy as np

DEFAULT_TABLE_PATH = (
    Path(__file__).resolve().parents[3] / "reference_data" / "philatelic_colours.csv"
)

#: Beyond this ΔE00 to the nearest reference colour, the name is not a
#: confident match -- still returned (never silently withheld), but flagged.
NO_CONFIDENT_MATCH_DELTA_E00 = 20.0

# Thresholds for the lightness/chroma modifiers, in the same spirit as
# ISCC-NBS's own L*/C* boundaries but tuned to this table's spacing rather
# than derived from one: entries are typically >= 12 L* apart and chroma
# ratios of 1.3x+ are a clearly visible difference in practice.
_LIGHTNESS_DELTA = 12.0
_CHROMA_RATIO_GRAYISH = 0.6
_CHROMA_RATIO_VIVID = 1.3


@dataclass
class PhilatelicColourEntry:
    name: str
    family: str
    lab: tuple[float, float, float]


@dataclass
class PhilatelicNameResult:
    full_name: str  # e.g. "deep carmine", or just "carmine" if no modifier applies
    base_name: str  # the matched reference entry's own name
    family: str
    delta_e00: float  # distance to the matched reference entry
    confident: bool
    warning: str | None = None


def load_philatelic_colours(path: str | Path = DEFAULT_TABLE_PATH) -> list[PhilatelicColourEntry]:
    entries = []
    with Path(path).open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            entries.append(
                PhilatelicColourEntry(
                    name=row["name"],
                    family=row["family"],
                    lab=(float(row["L"]), float(row["a"]), float(row["b"])),
                )
            )
    return entries


def _lch(lab: tuple[float, float, float]) -> tuple[float, float, float]:
    lightness, a, b = lab
    chroma = float(np.hypot(a, b))
    hue = float(np.degrees(np.arctan2(b, a)) % 360.0)
    return lightness, chroma, hue


def name_philatelic_colour(
    lab: np.ndarray, table: list[PhilatelicColourEntry]
) -> PhilatelicNameResult:
    """Nearest-reference-colour naming with an ISCC-NBS-like lightness/
    chroma modifier (see module docstring for the method and its caveats)."""
    lab_t = tuple(float(v) for v in lab)
    distances = [
        float(colour.difference.delta_E_CIE2000(np.array(lab_t), np.array(e.lab))) for e in table
    ]
    best_idx = int(np.argmin(distances))
    best = table[best_idx]
    delta_e00 = distances[best_idx]

    lightness, chroma, _hue = _lch(lab_t)
    anchor_lightness, anchor_chroma, _anchor_hue = _lch(best.lab)

    modifiers: list[str] = []
    light_delta = lightness - anchor_lightness
    chroma_ratio = chroma / max(anchor_chroma, 1.0)

    if best.family != "neutral":
        if light_delta > _LIGHTNESS_DELTA:
            modifiers.append("pale" if chroma_ratio < _CHROMA_RATIO_GRAYISH else "light")
        elif light_delta < -_LIGHTNESS_DELTA:
            modifiers.append("dark" if chroma_ratio < _CHROMA_RATIO_GRAYISH else "deep")
        elif chroma_ratio < _CHROMA_RATIO_GRAYISH:
            modifiers.append("grayish")
        elif chroma_ratio >= _CHROMA_RATIO_VIVID:
            modifiers.append("vivid")

    full_name = " ".join([*modifiers, best.name])
    confident = delta_e00 <= NO_CONFIDENT_MATCH_DELTA_E00
    warning = (
        None
        if confident
        else (
            f"no close match in the philatelic reference table "
            f"(ΔE00={delta_e00:.1f} to nearest: {best.name!r}); "
            "name is only an approximate hue-family label"
        )
    )
    return PhilatelicNameResult(
        full_name=full_name,
        base_name=best.name,
        family=best.family,
        delta_e00=delta_e00,
        confident=confident,
        warning=warning,
    )
