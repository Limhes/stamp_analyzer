"""Application workflows shared by the CLI and the GUI (SPEC §3).

The analysis modules (``detection``, ``perforation``, ``colour``,
``calibration.*``) each perform one measurement. The functions here are one
level up: "take a scan with these settings", "add a sample sheet to a
project", "measure this stamp's perforation and persist it" -- each a short
sequence of core calls plus the bookkeeping that ties their results to the
project on disk. Both front ends call these, so each exists exactly once
(previously the CLI and up to two GUI panels carried separate copies, which
had started to drift: the CLI's ``scan`` never passed the identity gamma
tables to the acquiring ``scanimage`` call, for instance).

No Qt, no typer -- return values and exceptions only. Long-running
functions accept an optional ``progress(message)`` callback.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from stampan.core import colour as colour_mod
from stampan.core import detection as detection_mod
from stampan.core import perforation as perforation_mod
from stampan.core import project as project_mod
from stampan.core import scanner as scanner_mod
from stampan.core.calibration import reference as ref_mod
from stampan.core.io import ScanMetadata, read_tiff
from stampan.core.models import (
    ColourCluster,
    GeometryResult,
    PerforationSide,
    Project,
    Reference,
    Sample,
    Stamp,
)

Progress = Callable[[str], None] | None

DEFAULT_LIBRARY_ROOT = Path.home() / "StampAnalyzer"
REFERENCE_DATA_DIR = Path(__file__).resolve().parents[3] / "reference_data"
DEFAULT_CGATS_PATH = REFERENCE_DATA_DIR / "R240103.txt"
#: Black margin kept around the tooth tips of every extracted crop (SPEC §5).
DEFAULT_STAMP_MARGIN_MM = 3.0

#: Keys of a scanner option dict that ``capture_scan`` passes as dedicated
#: ``acquire_scan`` arguments rather than as extra ``--option=value`` flags.
_DEDICATED_SCAN_KEYS = ("resolution", "mode", "depth")


class WorkflowError(RuntimeError):
    """A workflow precondition failed (e.g. a reference without a scale).
    The message is written for the end user."""


def _report(progress: Progress, message: str) -> None:
    if progress is not None:
        progress(message)


# -- scanning -----------------------------------------------------------------


def raw_capture_options() -> dict[str, str]:
    """Options that switch the scanner's own image processing off (SPEC §2).

    ``--gamma-correction Default`` is *not* linear on the epson2 backend --
    it is the backend's contrast curve (cross-validated ΔE00 ~6-8 against
    ~2 with an identity table on otherwise identical scans). An identity
    user-defined table is the real linear/off setting. ``auto-area-
    segmentation`` is deliberately absent: it is a lineart/halftone option,
    inactive in Color mode, and setting it makes ``scanimage`` abort.
    """
    identity = ",".join(str(i) for i in range(256))
    return {
        "color-correction": "None",
        "gamma-correction": "User defined",
        "red-gamma-table": identity,
        "green-gamma-table": identity,
        "blue-gamma-table": identity,
        "sharpness": "0",
    }


def reference_scan_options(
    resolution_dpi: float, overrides: dict[str, str] | None = None
) -> dict[str, str]:
    """The full option set for capturing an IT8 reference target.

    ``overrides`` (the user's preferences) replace or add options; an empty
    value removes that option. Whatever is used is verified before scanning
    and recorded in the reference, and sample sheets reuse the reference's
    own recorded set, so a change here only affects references made after it.
    """
    options = {"resolution": str(int(resolution_dpi)), "mode": "Color", **raw_capture_options()}
    for key, value in (overrides or {}).items():
        key = key.strip().lstrip("-")
        if not key or key == "resolution":
            continue
        if str(value).strip():
            options[key] = str(value).strip()
        else:
            options.pop(key, None)
    return options


def sample_scan_options(reference: Reference) -> dict[str, str]:
    """The reference's own recorded option set, for scanning a sample sheet
    against it (SPEC §4.1: everything except the scan area must match)."""
    options = {str(k): str(v) for k, v in reference.scanner_options.items()}
    resolution = options.get("resolution")
    if resolution is None:
        raise WorkflowError(
            f"Reference '{reference.name}' has no recorded scanner resolution. It was built "
            "from an externally captured scan; rebuild it from a live scan before scanning "
            "sample sheets against it."
        )
    options["resolution"] = str(int(float(resolution)))
    options.setdefault("mode", "Color")
    options.pop("depth", None)  # informational on older references; never a settable value
    return options


@dataclass
class CaptureResult:
    image: np.ndarray  # uint16, (H, W, 3)
    metadata: ScanMetadata
    source_bit_depth: int  # what the scanner actually delivered
    warnings: list[str] = field(default_factory=list)


def capture_scan(
    scanner_options: dict[str, str],
    device: str | None = None,
    ref_id: str | None = None,
    scanimage_cmd: str = "scanimage",
    progress: Progress = None,
) -> CaptureResult:
    """Apply ``scanner_options``, verify them, then acquire a scan (SPEC §4.1).

    Raises ``scanner.ScannerError`` -- refusing to scan -- if any option
    does not read back as requested. The acquiring ``scanimage`` call gets
    the full option set again: the epson2 backend keeps no option state
    between invocations, so options verified in one process are gone in the
    next. An 8-bit capture is upscaled ×257 to the pipeline's uint16
    convention (a plain ``astype`` would leave values in 0-255, outside the
    domain every colour model is trained on).
    """
    options = {str(k): str(v) for k, v in scanner_options.items()}
    _report(progress, "Checking scanner settings…")
    mismatches = scanner_mod.apply_and_verify(options, device=device, scanimage_cmd=scanimage_cmd)
    if mismatches:
        detail = "; ".join(
            f"{name}: requested {req!r}, scanner reports {act!r}"
            for name, (req, act) in mismatches.items()
        )
        raise scanner_mod.ScannerError(
            f"The scanner did not accept the required settings (SPEC §4.1): {detail}"
        )

    resolution = options.get("resolution")
    extra = {k: v for k, v in options.items() if k not in _DEDICATED_SCAN_KEYS}
    _report(progress, "Scanning…")
    with tempfile.TemporaryDirectory() as tmp:
        raw_path = Path(tmp) / "raw_scan.tif"
        acquired = scanner_mod.acquire_scan(
            raw_path,
            device=device,
            resolution_dpi=int(float(resolution)) if resolution is not None else None,
            mode=options.get("mode", "Color"),
            depth=16,
            extra_options=extra,
            scanimage_cmd=scanimage_cmd,
        )
        image, _ = read_tiff(raw_path)

    warnings = list(acquired.warnings)
    meta_extra: dict = {}
    if image.dtype == np.uint8:
        warnings.append("scanner delivered 8-bit data; upscaling to 16-bit (×257)")
        image = (image.astype(np.uint16) * 257).astype(np.uint16)
        meta_extra["upscaled_from_8bit"] = True
    metadata = ScanMetadata(
        scan_time=datetime.now(UTC).isoformat(),
        scanner_options=options,
        ref_id=ref_id,
        extra=meta_extra,
    )
    return CaptureResult(
        image=image, metadata=metadata, source_bit_depth=acquired.bit_depth, warnings=warnings
    )


def list_scanners() -> list[scanner_mod.ScannerDevice]:
    """Flatbed scanners only. SANE's v4l backend presents webcams as
    scanner-shaped devices; they accept none of the options this app
    relies on, so they are excluded rather than offered."""
    return [d for d in scanner_mod.list_devices() if not d.device_name.startswith("v4l:")]


# -- references ---------------------------------------------------------------


def scale_of(reference: Reference) -> tuple[float, float]:
    if reference.scale is None:
        raise WorkflowError(
            f"Reference '{reference.name}' has no geometric calibration (px/mm), so it "
            "cannot be used to measure stamps."
        )
    return reference.scale.px_per_mm_x, reference.scale.px_per_mm_y


def reference_for_stamp(
    library_root: str | Path, project_id: str, stamp_id: str
) -> tuple[Sample, Reference]:
    sample_id = project_mod.find_sample_for_stamp(library_root, project_id, stamp_id)
    sample = project_mod.load_sample(library_root, project_id, sample_id)
    return sample, ref_mod.load_reference(library_root, sample.ref_id)


def colour_model_for_stamp(
    library_root: str | Path, project_id: str, stamp_id: str
) -> ref_mod.ColourModel | None:
    sample, reference = reference_for_stamp(library_root, project_id, stamp_id)
    if reference.colour_model is None:
        return None
    return ref_mod.load_colour_model(library_root, sample.ref_id)


# -- sample sheets ------------------------------------------------------------


@dataclass
class IngestResult:
    sample: Sample
    n_stamps: int
    n_flagged: int
    warnings: list[str]


def _crop_and_save(
    library_root: str | Path,
    sample: Sample,
    scan_image: np.ndarray,
    detected: detection_mod.DetectedStamp,
    scale: tuple[float, float],
    stamp: Stamp,
    background_level: float,
    margin_mm: float,
) -> np.ndarray:
    px_per_mm_x, px_per_mm_y = scale
    crop, transform = detection_mod.extract_crop(
        scan_image,
        detected,
        px_per_mm_x,
        px_per_mm_y,
        margin_mm,
        background_level=background_level,
    )
    stamp.geometry.width_mm = detected.size_px[0] / px_per_mm_x
    stamp.geometry.height_mm = detected.size_px[1] / px_per_mm_y
    stamp.geometry.rotation_deg = transform.angle_deg
    stamp.geometry.sample_centre_px = transform.sample_centre_px
    stamp.geometry.crop_size_px = transform.crop_size_px
    project_mod.save_stamp(library_root, sample, stamp, crop)
    return crop


def _background_level(scan_image: np.ndarray) -> float:
    # The 1st percentile of a background-dominated sheet is stable under
    # heavy subsampling; computing it once per sheet avoids extract_crop's
    # own fallback re-sorting the whole (multi-hundred-MB) scan per stamp.
    return float(np.percentile(scan_image[::8, ::8], 1))


def ingest_sample(
    library_root: str | Path,
    project: Project,
    ref_id: str,
    scan_image: np.ndarray,
    scan_metadata: ScanMetadata,
    margin_mm: float = DEFAULT_STAMP_MARGIN_MM,
    progress: Progress = None,
) -> IngestResult:
    """Add a sample sheet to ``project``: store the scan, detect the stamps,
    extract an upright crop per stamp, write the overview and contact-sheet
    diagnostics (SPEC §5)."""
    reference = ref_mod.load_reference(library_root, ref_id)
    scale = scale_of(reference)

    _report(progress, "Storing sample sheet…")
    scan_metadata.ref_id = ref_id
    sample = project_mod.add_sample(library_root, project, scan_image, scan_metadata, ref_id)

    _report(progress, "Detecting stamps…")
    result = detection_mod.detect_stamps(scan_image, *scale)
    background_level = _background_level(scan_image)

    crops = []
    for i, detected in enumerate(result.stamps):
        _report(progress, f"Extracting stamp {i + 1} of {len(result.stamps)}…")
        stamp = Stamp(stamp_id=project_mod.next_stamp_id(sample, i), geometry=GeometryResult())
        crops.append(
            _crop_and_save(
                library_root,
                sample,
                scan_image,
                detected,
                scale,
                stamp,
                background_level,
                margin_mm,
            )
        )

    _report(progress, "Writing diagnostics…")
    diagnostics_dir = project_mod.project_dir(library_root, project.project_id) / "diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    (diagnostics_dir / f"{sample.sample_id}_overview.png").write_bytes(
        detection_mod.render_overview(scan_image, result)
    )
    (diagnostics_dir / f"{sample.sample_id}_contact_sheet.png").write_bytes(
        detection_mod.render_contact_sheet(crops)
    )
    project_mod.save_sample(library_root, sample)

    return IngestResult(
        sample=sample,
        n_stamps=len(result.stamps),
        n_flagged=sum(1 for s in result.stamps if s.flagged_for_manual_review),
        warnings=list(result.warnings),
    )


def place_stamp(
    library_root: str | Path,
    project_id: str,
    sample: Sample,
    centre_px: tuple[float, float],
    size_px: tuple[float, float],
    angle_deg: float,
    stamp_id: str | None = None,
    margin_mm: float = DEFAULT_STAMP_MARGIN_MM,
) -> Stamp:
    """Re-extract a stamp's crop from a hand-placed rectangle on its sheet --
    correcting a detected stamp's box (``stamp_id`` given) or adding one the
    detection pass missed (``stamp_id`` None). Existing perforation/colour
    results of a corrected stamp refer to the old crop afterwards."""
    reference = ref_mod.load_reference(library_root, sample.ref_id)
    scale = scale_of(reference)
    scan_image, _meta = project_mod.load_sample_scan(library_root, project_id, sample)
    detected = detection_mod.DetectedStamp(
        index=0,
        centre_px=centre_px,
        size_px=size_px,
        angle_deg=angle_deg,
        area_px=size_px[0] * size_px[1],
        fill_ratio=1.0,
        confidence=1.0,
    )
    if stamp_id is None:
        stamp = Stamp(stamp_id=project_mod.next_stamp_id(sample))
    else:
        stamp = project_mod.load_stamp(library_root, project_id, stamp_id)
    _crop_and_save(
        library_root,
        sample,
        scan_image,
        detected,
        scale,
        stamp,
        _background_level(scan_image),
        margin_mm,
    )
    if stamp_id is None:
        project_mod.save_sample(library_root, sample)
    return stamp


# -- perforation --------------------------------------------------------------

#: Sentinel for ``measure_perforation``'s ``box_px``: use the stamp's saved
#: box override (or the auto-detected box if there is none).
USE_SAVED_BOX = object()


@dataclass
class PerforationMeasurement:
    stamp: Stamp
    analysis: perforation_mod.PerforationAnalysis
    notation: str


def measure_perforation(
    library_root: str | Path,
    project_id: str,
    stamp_id: str,
    box_px=USE_SAVED_BOX,
    gauge_range: tuple[float, float] | None = None,
) -> PerforationMeasurement:
    """Measure one stamp's perforation and persist the result and its
    diagnostics (SPEC §6).

    ``box_px`` (4 corners) replaces and is saved as the stamp's box
    override; the default keeps whatever override is already saved.
    ``gauge_range`` None keeps the saved range (default range for a stamp
    never measured). Manual per-side corrections are never touched.
    """
    _sample, reference = reference_for_stamp(library_root, project_id, stamp_id)
    px_per_mm_x, px_per_mm_y = scale_of(reference)
    crop = project_mod.load_stamp_crop(library_root, project_id, stamp_id)
    stamp = project_mod.load_stamp(library_root, project_id, stamp_id)

    if box_px is USE_SAVED_BOX:
        box = stamp.perforation.box_override_px
    else:
        box = box_px
        stamp.perforation.box_override_px = (
            [(float(x), float(y)) for x, y in np.asarray(box)] if box is not None else None
        )
    gauge_range = (
        gauge_range or stamp.perforation.gauge_range or perforation_mod.DEFAULT_GAUGE_RANGE
    )

    analysis = perforation_mod.analyse_perforation(
        crop,
        px_per_mm_x,
        px_per_mm_y,
        manual_roi=stamp.perforation.roi_override_mm or None,
        manual_box_px=box,
        condition=stamp.condition,
        multiplicity=stamp.multiplicity,
        multiplicity_count=stamp.multiplicity_count,
        gauge_range=gauge_range,
    )
    diagnostics = {"perforation.png": perforation_mod.render_diagnostic(crop, analysis)}
    for name, side in analysis.sides.items():
        diagnostics[f"perforation_{name}.png"] = perforation_mod.render_side_profile(side)

    stamp.perforation.auto = {name: side.to_persisted() for name, side in analysis.sides.items()}
    stamp.perforation.gauge_range = gauge_range
    stamp.perforation.meta.confidence = analysis.confidence
    stamp.perforation.meta.warnings = analysis.warnings
    stamp.perforation.meta.diagnostic_image = "diagnostics/perforation.png"
    project_mod.save_stamp_results(library_root, project_id, stamp, diagnostics=diagnostics)
    return PerforationMeasurement(
        stamp=stamp,
        analysis=analysis,
        notation=perforation_mod.perforation_result_notation(analysis.sides),
    )


@dataclass
class BatchItem:
    stamp_id: str
    measurement: PerforationMeasurement | None = None
    skipped: str | None = None  # reason, when measurement is None


def set_perforation_override(
    library_root: str | Path,
    project_id: str,
    stamp_id: str,
    sides: dict[str, tuple[str, float | None]],
    reason: str = "",
) -> Stamp:
    """Replace the manual perforation values of a stamp (SPEC §6.2).

    ``sides`` maps a side to ``(kind, gauge)`` -- kind "perforated" (gauge
    required), "imperf" or "straight_edge". Sides left out have no override
    and show the measured value. The measured result itself is untouched, so
    "measured" and "set by hand" stay distinguishable (and the report flags
    the override).
    """
    stamp = project_mod.load_stamp(library_root, project_id, stamp_id)
    manual, reasons = {}, {}
    for side, (kind, gauge) in sides.items():
        if side not in perforation_mod.SIDES:
            raise WorkflowError(f"unknown side '{side}'")
        if kind == "perforated":
            if gauge is None or not perforation_mod.GAUGE_MIN <= gauge <= 30:
                raise WorkflowError(f"{side}: a perforated side needs a plausible gauge")
            manual[side] = PerforationSide(
                kind=kind, gauge=float(gauge), notation=perforation_mod.notation_for_gauge(gauge)
            )
        elif kind in ("imperf", "straight_edge"):
            manual[side] = PerforationSide(kind=kind)
        else:
            raise WorkflowError(f"{side}: unknown kind '{kind}'")
        if reason.strip():
            reasons[side] = reason.strip()
    stamp.perforation.manual = manual
    stamp.perforation.manual_reason = reasons
    project_mod.save_stamp_results(library_root, project_id, stamp)
    return stamp


def measure_project_perforation(
    library_root: str | Path,
    project: Project,
    stamp_ids: list[str] | None = None,
    progress: Progress = None,
) -> list[BatchItem]:
    """Re-measure perforation on every (or the listed) stamp(s), keeping
    each stamp's own saved box and gauge range."""
    index = project_mod.stamp_index(library_root, project)
    targets = stamp_ids if stamp_ids is not None else list(index)
    items: list[BatchItem] = []
    for i, stamp_id in enumerate(targets, start=1):
        _report(progress, f"Measuring perforation {i} of {len(targets)}…")
        if stamp_id not in index:
            items.append(BatchItem(stamp_id, skipped="not found in this project"))
            continue
        try:
            items.append(
                BatchItem(stamp_id, measure_perforation(library_root, project.project_id, stamp_id))
            )
        except WorkflowError as exc:
            items.append(BatchItem(stamp_id, skipped=str(exc)))
    return items


# -- colour -------------------------------------------------------------------


def measure_roi_colour(
    library_root: str | Path,
    project_id: str,
    stamp_id: str,
    name: str,
    mask: np.ndarray,
    philatelic_table=None,
) -> tuple[ColourCluster | None, list[str]]:
    """Measure one user-drawn colour area (SPEC §8) and save its mask."""
    model = colour_model_for_stamp(library_root, project_id, stamp_id)
    if model is None:
        return None, ["this stamp's reference has no colour model"]
    crop = project_mod.load_stamp_crop(library_root, project_id, stamp_id)
    cluster, warnings = colour_mod.measure_roi_colour(
        crop, model.predict_lab, mask, name, philatelic_table=philatelic_table
    )
    mask_path = project_mod.save_stamp_roi_mask(library_root, project_id, stamp_id, name, mask)
    if cluster is not None:
        cluster.mask_path = mask_path
    return cluster, warnings


def save_stamp_colours(
    library_root: str | Path, project_id: str, stamp_id: str, clusters: list[ColourCluster]
) -> Stamp:
    stamp = project_mod.load_stamp(library_root, project_id, stamp_id)
    stamp.colour.n_inks = len(clusters)
    stamp.colour.clusters = clusters
    stamp.colour.meta.confidence = 1.0 if clusters else 0.0
    project_mod.save_stamp_results(library_root, project_id, stamp)
    return stamp


# -- report -------------------------------------------------------------------


@dataclass
class ReportFiles:
    pdf: Path
    csv: Path
    json: Path
    colour_overview: Path | None
    n_stamps: int
    notes: list[str]


def export_report(
    library_root: str | Path,
    project: Project,
    pdf_path: Path | None = None,
    progress: Progress = None,
) -> ReportFiles:
    """Write the PDF report plus CSV/JSON export (and the standalone colour
    overview image) for ``project`` (SPEC §10)."""
    from stampan.core.report import generator as report_mod

    _report(progress, "Collecting results…")
    context = report_mod.build_report_context(library_root, project)
    if not context.stamps:
        raise WorkflowError("This project has no stamps with a usable reference to report on.")

    _report(progress, "Rendering PDF…")
    pdf_bytes = report_mod.render_pdf(report_mod.render_html(context))
    pdf_path = pdf_path or (
        project_mod.project_dir(library_root, project.project_id) / "report.pdf"
    )
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(pdf_bytes)
    csv_path = pdf_path.with_suffix(".csv")
    csv_path.write_text(report_mod.export_csv(context), encoding="utf-8")
    json_path = pdf_path.with_suffix(".json")
    json_path.write_text(report_mod.export_json(context), encoding="utf-8")

    overview_path = None
    if context.colour_overview_png:
        overview_path = pdf_path.parent / "colour_overview.png"
        overview_path.write_bytes(context.colour_overview_png)

    return ReportFiles(
        pdf=pdf_path,
        csv=csv_path,
        json=json_path,
        colour_overview=overview_path,
        n_stamps=len(context.stamps),
        notes=list(context.notes),
    )
