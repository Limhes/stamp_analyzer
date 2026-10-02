"""Result dataclasses for every analysis module (SPEC §3) and their JSON
(de)serialisation.

Every analysis result carries an :class:`AnalysisMeta` (confidence, warnings,
the parameters used, and a path to its diagnostic image). Where a human can
correct an automatic result (perforation sides, colour cluster labels,
cancellation masks, ...) the manual value is stored in a separate field next
to the automatic one — the automatic result is never overwritten, so "auto"
vs "manual" stays traceable and auditable.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Shared
# --------------------------------------------------------------------------


@dataclass
class AnalysisMeta:
    """Common bookkeeping attached to every analysis result."""

    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    diagnostic_image: str | None = None

    @staticmethod
    def from_dict(d: dict[str, Any]) -> AnalysisMeta:
        return AnalysisMeta(
            confidence=d.get("confidence", 0.0),
            warnings=list(d.get("warnings", [])),
            params=dict(d.get("params", {})),
            diagnostic_image=d.get("diagnostic_image"),
        )


def _tuple3(v: Any) -> tuple[float, float, float] | None:
    return tuple(v) if v is not None else None  # type: ignore[return-value]


# --------------------------------------------------------------------------
# Perforation (SPEC §6)
# --------------------------------------------------------------------------

PerfSide = str  # "top" | "bottom" | "left" | "right"


@dataclass
class PerforationSide:
    """A single side's perforation measurement, auto or manual."""

    kind: str = "perforated"  # "perforated" | "imperf" | "straight_edge"
    gauge: float | None = None
    uncertainty: float | None = None
    notation: str | None = None  # e.g. "11 1/2"
    hole_count: int | None = None
    length_mm: float | None = None
    spacing_std_mm: float | None = None
    spacing_max_dev_mm: float | None = None
    hole_diameter_mm: float | None = None
    tooth_roughness: float | None = None

    @staticmethod
    def from_dict(d: dict[str, Any]) -> PerforationSide:
        return PerforationSide(**d)


@dataclass
class PerforationResult:
    auto: dict[PerfSide, PerforationSide] = field(default_factory=dict)
    manual: dict[PerfSide, PerforationSide] = field(default_factory=dict)
    manual_reason: dict[PerfSide, str] = field(default_factory=dict)
    meta: AnalysisMeta = field(default_factory=AnalysisMeta)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> PerforationResult:
        return PerforationResult(
            auto={k: PerforationSide.from_dict(v) for k, v in d.get("auto", {}).items()},
            manual={k: PerforationSide.from_dict(v) for k, v in d.get("manual", {}).items()},
            manual_reason=dict(d.get("manual_reason", {})),
            meta=AnalysisMeta.from_dict(d.get("meta", {})),
        )


# --------------------------------------------------------------------------
# Cancellation (SPEC §7)
# --------------------------------------------------------------------------


@dataclass
class CancellationResult:
    present: bool | None = None
    auto_mask_path: str | None = None
    manual_mask_path: str | None = None
    colour_lab: tuple[float, float, float] | None = None
    colour_class: str | None = None  # "black" | "blue" | "violet" | "red" | "other"
    meta: AnalysisMeta = field(default_factory=AnalysisMeta)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> CancellationResult:
        return CancellationResult(
            present=d.get("present"),
            auto_mask_path=d.get("auto_mask_path"),
            manual_mask_path=d.get("manual_mask_path"),
            colour_lab=_tuple3(d.get("colour_lab")),
            colour_class=d.get("colour_class"),
            meta=AnalysisMeta.from_dict(d.get("meta", {})),
        )


# --------------------------------------------------------------------------
# Colour (SPEC §8, §9)
# --------------------------------------------------------------------------


@dataclass
class ColourCluster:
    label: str  # "paper" | "ink1" | ...
    pixel_count: int = 0
    area_fraction: float = 0.0
    mean_lab: tuple[float, float, float] | None = None
    median_lab: tuple[float, float, float] | None = None
    std_lab: tuple[float, float, float] | None = None
    mean_lch: tuple[float, float, float] | None = None
    delta_e00_spread: float | None = None
    munsell: str | None = None
    iscc_nbs_name: str | None = None
    srgb_swatch: tuple[int, int, int] | None = None
    out_of_gamut: bool = False

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ColourCluster:
        return ColourCluster(
            label=d["label"],
            pixel_count=d.get("pixel_count", 0),
            area_fraction=d.get("area_fraction", 0.0),
            mean_lab=_tuple3(d.get("mean_lab")),
            median_lab=_tuple3(d.get("median_lab")),
            std_lab=_tuple3(d.get("std_lab")),
            mean_lch=_tuple3(d.get("mean_lch")),
            delta_e00_spread=d.get("delta_e00_spread"),
            munsell=d.get("munsell"),
            iscc_nbs_name=d.get("iscc_nbs_name"),
            srgb_swatch=tuple(d["srgb_swatch"]) if d.get("srgb_swatch") is not None else None,
            out_of_gamut=d.get("out_of_gamut", False),
        )


@dataclass
class ColourResult:
    n_inks: int = 1
    tau: float = 0.9
    clusters: list[ColourCluster] = field(default_factory=list)
    mixed_pixel_fraction: float | None = None
    ink_paper_delta_e00: dict[str, float] = field(default_factory=dict)
    manual_overrides: dict[str, Any] = field(default_factory=dict)
    meta: AnalysisMeta = field(default_factory=AnalysisMeta)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ColourResult:
        return ColourResult(
            n_inks=d.get("n_inks", 1),
            tau=d.get("tau", 0.9),
            clusters=[ColourCluster.from_dict(c) for c in d.get("clusters", [])],
            mixed_pixel_fraction=d.get("mixed_pixel_fraction"),
            ink_paper_delta_e00=dict(d.get("ink_paper_delta_e00", {})),
            manual_overrides=dict(d.get("manual_overrides", {})),
            meta=AnalysisMeta.from_dict(d.get("meta", {})),
        )


# --------------------------------------------------------------------------
# Geometry, Stamp, Sample
# --------------------------------------------------------------------------


@dataclass
class GeometryResult:
    width_mm: float | None = None
    height_mm: float | None = None
    rotation_deg: float | None = None

    @staticmethod
    def from_dict(d: dict[str, Any]) -> GeometryResult:
        return GeometryResult(**d)


@dataclass
class Stamp:
    stamp_id: str
    crop_path: str | None = None
    catalogue_numbers: list[str] = field(default_factory=list)
    country_issue: str | None = None
    notes: str = ""
    geometry: GeometryResult = field(default_factory=GeometryResult)
    perforation: PerforationResult = field(default_factory=PerforationResult)
    cancellation: CancellationResult = field(default_factory=CancellationResult)
    colour: ColourResult = field(default_factory=ColourResult)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Stamp:
        return Stamp(
            stamp_id=d["stamp_id"],
            crop_path=d.get("crop_path"),
            catalogue_numbers=list(d.get("catalogue_numbers", [])),
            country_issue=d.get("country_issue"),
            notes=d.get("notes", ""),
            geometry=GeometryResult.from_dict(d.get("geometry", {})),
            perforation=PerforationResult.from_dict(d.get("perforation", {})),
            cancellation=CancellationResult.from_dict(d.get("cancellation", {})),
            colour=ColourResult.from_dict(d.get("colour", {})),
        )

    @staticmethod
    def from_json(text: str) -> Stamp:
        return Stamp.from_dict(json.loads(text))


# --------------------------------------------------------------------------
# Reference (SPEC §4.4) and Sample (SPEC §5)
# --------------------------------------------------------------------------


@dataclass
class ScaleCalibration:
    """Separate x/y px<->mm scale, per SPEC §4.2."""

    px_per_mm_x: float
    px_per_mm_y: float
    uncertainty_x: float | None = None
    uncertainty_y: float | None = None
    assumed_isotropic: bool = False

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ScaleCalibration:
        return ScaleCalibration(**d)


@dataclass
class ColourModelInfo:
    kind: str = ""  # "matrix3x3" | "root_poly2" | "root_poly3" | "lut_delaunay" | "icc"
    model_path: str | None = None  # e.g. model.npz
    train_delta_e00: dict[str, float] = field(default_factory=dict)  # mean/median/p95/max
    cv_delta_e00: dict[str, float] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ColourModelInfo:
        return ColourModelInfo(
            kind=d.get("kind", ""),
            model_path=d.get("model_path"),
            train_delta_e00=dict(d.get("train_delta_e00", {})),
            cv_delta_e00=dict(d.get("cv_delta_e00", {})),
        )


@dataclass
class Reference:
    ref_id: str
    name: str
    created: str  # ISO date
    notes: str = ""
    scanner_options: dict[str, Any] = field(default_factory=dict)
    scale: ScaleCalibration | None = None
    colour_model: ColourModelInfo | None = None
    scan_path: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Reference:
        scale = d.get("scale")
        colour_model = d.get("colour_model")
        return Reference(
            ref_id=d["ref_id"],
            name=d["name"],
            created=d["created"],
            notes=d.get("notes", ""),
            scanner_options=dict(d.get("scanner_options", {})),
            scale=ScaleCalibration.from_dict(scale) if scale else None,
            colour_model=ColourModelInfo.from_dict(colour_model) if colour_model else None,
            scan_path=d.get("scan_path"),
        )

    @staticmethod
    def from_json(text: str) -> Reference:
        return Reference.from_dict(json.loads(text))


@dataclass
class Sample:
    sample_id: str
    project_id: str
    scan_path: str
    ref_id: str
    scan_time: str | None = None
    stamp_ids: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Sample:
        return Sample(
            sample_id=d["sample_id"],
            project_id=d["project_id"],
            scan_path=d["scan_path"],
            ref_id=d["ref_id"],
            scan_time=d.get("scan_time"),
            stamp_ids=list(d.get("stamp_ids", [])),
        )

    @staticmethod
    def from_json(text: str) -> Sample:
        return Sample.from_dict(json.loads(text))


def write_json(path: str | Path, obj: Any) -> None:
    Path(path).write_text(obj.to_json(), encoding="utf-8")
