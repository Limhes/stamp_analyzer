"""Build, save and load a :class:`~stampan.core.models.Reference` (SPEC §3.1, §4.4).

Ties together geometry (ruler) and colour (IT8) calibration into one
persisted object: `<library_root>/references/<ref_id>/` holding
`reference.json`, `reference_scan.tif`, `model.npz`, and `diagnostics/*.png`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from stampan.core.calibration import characterisation as char
from stampan.core.calibration import geometry as geom
from stampan.core.calibration import it8
from stampan.core.cgats import parse_cgats_file
from stampan.core.io import ScanMetadata, read_tiff, write_tiff
from stampan.core.models import ColourModelInfo, Reference, ScaleCalibration


@dataclass
class RulerAxisSpec:
    centre_xy: tuple[float, float]
    length_px: float
    width_px: float
    angle_deg: float
    tick_spacing_mm: float
    px_per_mm_hint: float


@dataclass
class ReferenceBuildResult:
    reference: Reference
    diagnostics: dict[str, bytes]  # relative filename -> PNG bytes
    model_arrays: dict[str, Any]  # for np.savez
    scan: np.ndarray


def _new_ref_id() -> str:
    return f"ref_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"


def _patch_rgb_xyz_arrays(
    samples: dict[str, it8.PatchSample], cgats_path: str | Path
) -> tuple[list[str], np.ndarray, np.ndarray]:
    table = parse_cgats_file(cgats_path)
    by_id = table.by_sample_id()
    ids, rgb, xyz = [], [], []
    for patch_id, sample in samples.items():
        if patch_id not in by_id:
            continue
        row = by_id[patch_id]
        ids.append(patch_id)
        rgb.append(sample.mean_rgb)
        xyz.append((row["XYZ_X"], row["XYZ_Y"], row["XYZ_Z"]))
    return ids, np.array(rgb, dtype=np.float64), np.array(xyz, dtype=np.float64)


def build_reference(
    name: str,
    scan: np.ndarray,
    scan_metadata: ScanMetadata,
    it8_corners: it8.IT8Corners,
    cgats_reference_path: str | Path,
    x_axis: RulerAxisSpec | None = None,
    y_axis: RulerAxisSpec | None = None,
    notes: str = "",
) -> ReferenceBuildResult:
    warnings: list[str] = []
    diagnostics: dict[str, bytes] = {}
    scale_x = scale_y = None
    assumed_isotropic = False

    if x_axis is not None:
        scale_x, profile_x = geom.calibrate_axis(
            scan,
            x_axis.centre_xy,
            x_axis.length_px,
            x_axis.width_px,
            x_axis.angle_deg,
            x_axis.tick_spacing_mm,
            x_axis.px_per_mm_hint,
        )
        diagnostics["geometry_x.png"] = geom.render_diagnostic(scale_x, profile_x)

        assumed_isotropic = y_axis is None
        if y_axis is not None:
            scale_y, profile_y = geom.calibrate_axis(
                scan,
                y_axis.centre_xy,
                y_axis.length_px,
                y_axis.width_px,
                y_axis.angle_deg,
                y_axis.tick_spacing_mm,
                y_axis.px_per_mm_hint,
            )
            diagnostics["geometry_y.png"] = geom.render_diagnostic(scale_y, profile_y)
        else:
            warnings.append("no y-axis ruler given; assuming y scale = x scale (isotropic)")
            scale_y = scale_x
            diagnostics["geometry_y.png"] = diagnostics["geometry_x.png"]
    else:
        warnings.append(
            "no ruler region given; geometric (px/mm) calibration was not performed. "
            "Perforation gauge and stamp dimensions cannot be measured with this reference "
            "until it is rebuilt with a ruler."
        )

    samples = it8.sample_it8_grid(scan, it8_corners)
    diagnostics["it8_grid.png"] = it8.render_diagnostic(scan, it8_corners, samples)
    diagnostics["it8_std_heatmap.png"] = it8.render_std_heatmap(samples)

    patch_ids, rgb, xyz = _patch_rgb_xyz_arrays(samples, cgats_reference_path)

    # Grey-ramp linearisation (SPEC §4.3): fit from GS patches, apply to all.
    gs_mask = [pid.startswith("GS") for pid in patch_ids]
    lin_curve: char.LinearisationCurve | None = None
    if sum(gs_mask) >= 4:
        gs_rgb = rgb[gs_mask]
        gs_y = xyz[gs_mask, 1]
        lin_curve = char.fit_linearisation(gs_rgb, gs_y)
        rgb_lin = lin_curve.apply(rgb)
    else:
        warnings.append("fewer than 4 GS patches sampled; skipping grey-ramp linearisation")
        rgb_lin = rgb

    reports: dict[char.ModelKind, char.ModelReport] = {}
    for kind, degree in (("matrix3x3", 1), ("root_poly2", 2), ("root_poly3", 3)):
        reports[kind] = char.evaluate_model(kind, rgb_lin, xyz, patch_ids, degree=degree)
    reports["lut_delaunay"] = char.evaluate_model("lut_delaunay", rgb_lin, xyz, patch_ids)

    best_kind = char.choose_best_model(reports)
    best_report = reports[best_kind]
    diagnostics["colour_model_error_heatmap.png"] = char.render_error_heatmap(
        best_report.per_patch_cv_delta_e00, it8.ROWS, it8.N_COLS, it8.N_GS
    )

    model_arrays: dict[str, Any] = {"kind": best_kind, "patch_ids": np.array(patch_ids)}
    model_arrays["has_linearisation"] = lin_curve is not None
    if lin_curve is not None:
        model_arrays.update(char.linearisation_to_arrays(lin_curve))
    if best_kind in ("matrix3x3", "root_poly2", "root_poly3"):
        degree = {"matrix3x3": 1, "root_poly2": 2, "root_poly3": 3}[best_kind]
        model = (
            char.fit_matrix3x3(rgb_lin, xyz)
            if best_kind == "matrix3x3"
            else char.fit_root_polynomial(rgb_lin, xyz, degree=degree)
        )
        model_arrays["ccm"] = model.ccm
        model_arrays["degree"] = degree
    else:
        model_arrays["rgb_train"] = rgb_lin
        model_arrays["lab_train"] = char.xyz_to_lab_array(xyz)

    ref_id = _new_ref_id()
    reference = Reference(
        ref_id=ref_id,
        name=name,
        created=datetime.now(UTC).date().isoformat(),
        notes=notes,
        scanner_options=dict(scan_metadata.scanner_options),
        scale=ScaleCalibration(
            px_per_mm_x=scale_x.px_per_mm,
            px_per_mm_y=scale_y.px_per_mm,
            uncertainty_x=scale_x.se_px_per_mm,
            uncertainty_y=scale_y.se_px_per_mm,
            assumed_isotropic=assumed_isotropic,
        )
        if scale_x is not None
        else None,
        colour_model=ColourModelInfo(
            kind=best_kind,
            model_path="model.npz",
            train_delta_e00=best_report.train.to_dict(),
            cv_delta_e00=best_report.cv.to_dict(),
        ),
        scan_path="reference_scan.tif",
    )

    scale_warnings = (scale_x.warnings if scale_x is not None else []) + (
        scale_y.warnings if (scale_y is not None and y_axis is not None) else []
    )
    all_warnings = warnings + scale_warnings
    if all_warnings:
        reference.notes = (
            (reference.notes + "\n" if reference.notes else "")
            + "Warnings: "
            + "; ".join(all_warnings)
        )

    model_comparison = {k: v.cv.to_dict() for k, v in reports.items()}
    diagnostics["model_comparison.json"] = json.dumps(model_comparison, indent=2).encode("utf-8")

    return ReferenceBuildResult(
        reference=reference, diagnostics=diagnostics, model_arrays=model_arrays, scan=scan
    )


def save_reference(result: ReferenceBuildResult, library_root: str | Path) -> Path:
    ref_dir = Path(library_root) / "references" / result.reference.ref_id
    (ref_dir / "diagnostics").mkdir(parents=True, exist_ok=True)

    (ref_dir / "reference.json").write_text(result.reference.to_json(), encoding="utf-8")
    write_tiff(ref_dir / "reference_scan.tif", result.scan.astype(np.uint16))
    np.savez(ref_dir / "model.npz", **result.model_arrays)
    for filename, data in result.diagnostics.items():
        (ref_dir / "diagnostics" / filename).write_bytes(data)
    return ref_dir


def load_reference(library_root: str | Path, ref_id: str) -> Reference:
    ref_dir = Path(library_root) / "references" / ref_id
    text = (ref_dir / "reference.json").read_text(encoding="utf-8")
    return Reference.from_json(text)


def load_reference_scan(library_root: str | Path, ref_id: str) -> tuple[np.ndarray, ScanMetadata]:
    ref_dir = Path(library_root) / "references" / ref_id
    return read_tiff(ref_dir / "reference_scan.tif")


def list_references(library_root: str | Path) -> list[Reference]:
    refs_dir = Path(library_root) / "references"
    if not refs_dir.exists():
        return []
    refs = []
    for ref_dir in sorted(refs_dir.iterdir()):
        ref_json = ref_dir / "reference.json"
        if ref_json.exists():
            refs.append(Reference.from_json(ref_json.read_text(encoding="utf-8")))
    return refs


class ColourModel:
    """A loaded, ready-to-use RGB -> Lab predictor, reconstructed from model.npz."""

    def __init__(self, kind: str, predict_fn):
        self.kind = kind
        self._predict_fn = predict_fn

    def predict_lab(self, rgb: np.ndarray) -> np.ndarray:
        return self._predict_fn(rgb)


def load_colour_model(library_root: str | Path, ref_id: str) -> ColourModel:
    ref_dir = Path(library_root) / "references" / ref_id
    data = np.load(ref_dir / "model.npz", allow_pickle=True)
    kind = str(data["kind"])

    # SPEC §4.3: the characterisation models are fit on grey-ramp-linearised
    # RGB, so any RGB predicted against them (new stamp crops included) must
    # go through the same per-channel curve first. References built before
    # this curve was persisted have no "has_linearisation" key at all; those
    # fall back to the identity (the old, buggy behaviour) rather than
    # crashing, since rebuilding them requires a rescan.
    has_lin = bool(data["has_linearisation"]) if "has_linearisation" in data else False
    lin_curve = char.linearisation_from_arrays(data) if has_lin else None

    def _linearise(rgb: np.ndarray) -> np.ndarray:
        return lin_curve.apply(rgb) if lin_curve is not None else np.asarray(rgb, dtype=np.float64)

    if kind in ("matrix3x3", "root_poly2", "root_poly3"):
        ccm = data["ccm"]
        degree = int(data["degree"])
        from colour.characterisation import apply_matrix_colour_correction_Finlayson2015

        from stampan.core.calibration.characterisation import xyz_to_lab_array

        def predict(rgb: np.ndarray) -> np.ndarray:
            xyz = apply_matrix_colour_correction_Finlayson2015(_linearise(rgb), ccm, degree=degree)
            return xyz_to_lab_array(xyz)

        return ColourModel(kind=kind, predict_fn=predict)

    if kind == "lut_delaunay":
        rgb_train = data["rgb_train"]
        lab_train = data["lab_train"]
        model = char.fit_linear_nd_interpolator(rgb_train, lab_train)

        def predict(rgb: np.ndarray) -> np.ndarray:
            return model.predict_lab(_linearise(rgb))

        return ColourModel(kind=kind, predict_fn=predict)

    raise ValueError(f"unknown stored model kind: {kind}")


def reference_summary(reference: Reference) -> dict[str, Any]:
    """A flat dict for `stampan reference show`."""
    summary: dict[str, Any] = {
        "ref_id": reference.ref_id,
        "name": reference.name,
        "created": reference.created,
    }
    if reference.scale:
        summary["px_per_mm_x"] = reference.scale.px_per_mm_x
        summary["px_per_mm_y"] = reference.scale.px_per_mm_y
        summary["scale_assumed_isotropic"] = reference.scale.assumed_isotropic
    if reference.colour_model:
        summary["colour_model_kind"] = reference.colour_model.kind
        summary["cv_delta_e00_mean"] = reference.colour_model.cv_delta_e00.get("mean")
    return summary
