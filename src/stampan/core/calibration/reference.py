"""Build, save and load a :class:`~stampan.core.models.Reference` (SPEC §3.1, §4.4).

Ties together geometry (scanner DPI) and colour (IT8) calibration into one
persisted object: `<library_root>/references/<ref_id>/` holding
`reference.json`, `reference_scan.tif`, `model.npz`, and `diagnostics/*.png`.

Geometric (px/mm) calibration used to come from a ruler scanned alongside
the IT8 target (a fitted tick-spacing regression) -- removed at René's
explicit direction once a real scanner was available to test against:
the scanner's own resolution setting, read back and confirmed by
``core.scanner.apply_and_verify`` before the scan is taken, is a direct
hardware-reported value rather than something inferred from the image, and
is simpler and no less trustworthy than a ruler fit for a flatbed scanner
(unlike, say, a photographed stamp at an unknown, uncalibrated distance).
A DPI value from anywhere *other* than that verified setting -- typed by
hand for an externally-captured scan, or read from a TIFF's own resolution
tag -- is not inherently trustworthy and is surfaced as such (SPEC §4.1/§4.2).
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from stampan.core.calibration import characterisation as char
from stampan.core.calibration import it8
from stampan.core.calibration import scanin as scanin_mod
from stampan.core.cgats import parse_cgats_file
from stampan.core.io import ScanMetadata, read_tiff, write_tiff
from stampan.core.models import ColourModelInfo, Reference, ScaleCalibration

#: mm per inch, for dpi -> px/mm.
MM_PER_INCH = 25.4


def px_per_mm_from_dpi(dpi: float) -> float:
    return dpi / MM_PER_INCH


@dataclass
class ReferenceBuildResult:
    reference: Reference
    diagnostics: dict[str, bytes]  # relative filename -> PNG bytes
    model_arrays: dict[str, Any]  # for np.savez
    scan: np.ndarray
    # Every candidate colour-model kind's own cv ΔE00 stats, keyed by kind
    # -- the wizard's review table (SPEC §4.4/§8: "review the validation
    # (ΔE00 table...)"), so the user can see *why* the best one was chosen,
    # not just its own number in isolation. Same data as the persisted
    # model_comparison.json diagnostic, just available without a round
    # trip through disk for the GUI's review step.
    model_comparison: dict[str, dict[str, float]] = field(default_factory=dict)


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
    cgats_reference_path: str | Path,
    dpi_x: float | None = None,
    dpi_y: float | None = None,
    dpi_source: str = "unspecified",
    notes: str = "",
    scanin_cmd: str = "scanin",
    cht_path: str | Path | None = None,
) -> ReferenceBuildResult:
    """``dpi_x``/``dpi_y`` give the geometric (px/mm) calibration directly
    from the scanner's own resolution setting -- no ruler (SPEC §4.1/§4.2,
    removed at René's direction). ``dpi_y`` omitted assumes an isotropic
    scanner (true of virtually all flatbeds; most don't even expose
    separate x/y resolution). ``dpi_source`` is recorded in the reference's
    notes for traceability -- e.g. "verified scanner setting" (the GUI's
    live-scan wizard path, where ``apply_and_verify`` already confirmed
    this exact value was in effect) versus "typed by hand" (an externally
    captured scan, where it is only as trustworthy as whoever supplied it).

    IT8 patch location is entirely automatic via ArgyllCMS's `scanin`
    (SPEC §4.3) -- no corner marking. The earlier "click 4-6 corner
    points, bilinearly interpolate" approach needed pixel-precise manual
    clicks (small errors compound across the 22x12 grid) and its point
    labels were visually ambiguous enough to cause real misclicks (row L
    read as row I); `scanin` locates the chart's own printed fiducial
    marks and patch grid directly from the scan instead, confirmed exact
    against a real scan via its own diagnostic overlay.
    """
    warnings: list[str] = []
    diagnostics: dict[str, bytes] = {}

    scale_x_val = scale_y_val = None
    assumed_isotropic = False
    if dpi_x is not None:
        scale_x_val = px_per_mm_from_dpi(dpi_x)
        assumed_isotropic = dpi_y is None
        scale_y_val = px_per_mm_from_dpi(dpi_y) if dpi_y is not None else scale_x_val
        if assumed_isotropic:
            warnings.append("no y-resolution given; assuming y scale = x scale (isotropic)")
    else:
        warnings.append(
            "no scan resolution (dpi) given; geometric (px/mm) calibration was not performed. "
            "Perforation gauge and stamp dimensions cannot be measured with this reference "
            "until it is rebuilt with a resolution."
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        scan_tmp_path = Path(tmpdir) / "scan_for_scanin.tif"
        write_tiff(scan_tmp_path, scan.astype(np.uint16))
        scanin_result = scanin_mod.locate_and_sample_patches(
            scan_tmp_path,
            cgats_reference_path,
            native_max_value=65535.0,
            cht_path=cht_path,
            scanin_cmd=scanin_cmd,
        )
    samples = scanin_result.samples
    if scanin_result.diagnostic_png is not None:
        diagnostics["it8_grid.png"] = scanin_result.diagnostic_png
    diagnostics["it8_std_heatmap.png"] = it8.render_std_heatmap(samples)

    patch_ids, rgb, xyz = _patch_rgb_xyz_arrays(samples, cgats_reference_path)

    # Sanity check: scanin occasionally picks the wrong one of several
    # candidate orientations/correspondences for a chart (confirmed on a
    # real scan: the sample grid still geometrically tiled every patch
    # correctly -- so it looked fine in the diagnostic at a glance -- but
    # each box was matched to the wrong patch ID, e.g. the GS row ending
    # up above row A instead of below row L, silently scrambling which
    # sampled RGB got compared to which CGATS XYZ entry during model
    # fitting). A correct correspondence must show brighter patches
    # measuring brighter; that single real failure scored -0.16 here
    # against 0.98 for correct builds. Refuse rather than silently fit (and
    # let the user save) a model trained against scrambled correspondences
    # -- CLAUDE.md: never invent a measurement result.
    luma_xyz_corr = float(np.corrcoef(rgb.mean(axis=1), xyz[:, 1])[0, 1])
    if luma_xyz_corr < 0.7:
        raise scanin_mod.ScaninError(
            "scanin's patch recognition looks wrong (luma-vs-XYZ_Y correlation "
            f"{luma_xyz_corr:.2f}, expected > 0.7 for a correct match) -- it likely "
            "matched the chart at the wrong rotation/orientation. Check the "
            "target's physical placement on the bed (e.g. not upside down) and "
            "rescan; if it recurs, try nudging the target slightly or rescanning "
            "at a different angle."
        )

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
            px_per_mm_x=scale_x_val,
            px_per_mm_y=scale_y_val,
            # Not a statistically-fit uncertainty (there's no regression
            # here to report one from) -- 0.0 reflects that the DPI value
            # itself is taken as exact, which is only as good as
            # dpi_source says it is; the scan head's own mechanical
            # positioning repeatability isn't characterised at all (SPEC
            # §4.1/§4.2 -- flagged here rather than inventing a number).
            uncertainty_x=0.0,
            uncertainty_y=0.0,
            assumed_isotropic=assumed_isotropic,
        )
        if scale_x_val is not None
        else None,
        colour_model=ColourModelInfo(
            kind=best_kind,
            model_path="model.npz",
            train_delta_e00=best_report.train.to_dict(),
            cv_delta_e00=best_report.cv.to_dict(),
        ),
        scan_path="reference_scan.tif",
    )
    if scale_x_val is not None:
        warnings.append(f"scale source: {dpi_source}")

    all_warnings = warnings
    if all_warnings:
        reference.notes = (
            (reference.notes + "\n" if reference.notes else "")
            + "Warnings: "
            + "; ".join(all_warnings)
        )

    model_comparison = {k: v.cv.to_dict() for k, v in reports.items()}
    diagnostics["model_comparison.json"] = json.dumps(model_comparison, indent=2).encode("utf-8")

    return ReferenceBuildResult(
        reference=reference,
        diagnostics=diagnostics,
        model_arrays=model_arrays,
        scan=scan,
        model_comparison=model_comparison,
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


def save_reference_metadata(library_root: str | Path, reference: Reference) -> Path:
    """Re-write just reference.json (rename, archive/unarchive, notes) --
    the scan and colour model are unaffected, so there's no need to touch
    them for a metadata-only edit."""
    ref_dir = Path(library_root) / "references" / reference.ref_id
    path = ref_dir / "reference.json"
    path.write_text(reference.to_json(), encoding="utf-8")
    return path


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
