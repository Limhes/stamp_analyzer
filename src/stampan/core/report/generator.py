"""PDF/CSV/JSON report generation (SPEC §10).

Builds a plain-data context from a project's references/samples/stamps,
renders it through a Jinja2 HTML template, and converts that to an A4 PDF
with WeasyPrint. Also exports the same per-stamp data as CSV and JSON, for
spreadsheet/scripted use outside the PDF.
"""

from __future__ import annotations

import base64
import csv
import io
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import colour as colour_science
import cv2
import jinja2
from weasyprint import HTML

from stampan import __version__
from stampan.core import colour as colour_mod
from stampan.core import project as project_mod
from stampan.core.calibration import reference as ref_mod
from stampan.core.models import ColourCluster, PerforationSide, Project, Reference, Stamp

TEMPLATES_DIR = Path(__file__).parent / "templates"


#: Diagnostic images are produced at full crop resolution (1200dpi stamp
#: crops are often 1500+ px/side) for on-screen inspection; embedded as-is
#: at full res and losslessly, 15 stamps' worth of full-res PNGs plus crop
#: photos made the PDF balloon past 60 MB (found generating a real report).
#: The report only ever displays these at a few cm, and they're
#: photographic (a scanned stamp, not line art), so both a lower working
#: resolution and JPEG's lossy compression are the right trade here.
REPORT_IMAGE_MAX_DIM_PX = 700
REPORT_JPEG_QUALITY = 82


def _resize_for_report(image_bgr_or_gray) -> Any:
    h, w = image_bgr_or_gray.shape[:2]
    scale = min(1.0, REPORT_IMAGE_MAX_DIM_PX / max(h, w))
    if scale >= 1.0:
        return image_bgr_or_gray
    return cv2.resize(image_bgr_or_gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)


def _jpeg_data_uri(jpeg_bytes: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode("ascii")


def _png_data_uri(png_bytes: bytes) -> str:
    # Used only for the matplotlib-rendered colour overview chart (small
    # text labels, line art) -- JPEG's lossy compression visibly smudges
    # that, unlike the photographic crop/diagnostic images above.
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


def _encode_jpeg(image_rgb) -> bytes:
    resized = _resize_for_report(image_rgb)
    ok, buf = cv2.imencode(
        ".jpg",
        cv2.cvtColor(resized, cv2.COLOR_RGB2BGR),
        [cv2.IMWRITE_JPEG_QUALITY, REPORT_JPEG_QUALITY],
    )
    if not ok:
        raise RuntimeError("failed to encode report image")
    return buf.tobytes()


def _read_diagnostic(
    library_root: str | Path, project_id: str, stamp_id: str, filename: str | None
) -> str | None:
    if not filename:
        return None
    # ``filename`` is always the stamp-relative path as stored (e.g.
    # "diagnostics/perforation.png"), matching ``AnalysisMeta.diagnostic_image``'s
    # own convention.
    path = project_mod.project_dir(library_root, project_id) / "stamps" / stamp_id / filename
    if not path.exists():
        return None
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)  # drop any alpha; flattens onto black
    if image is None:
        return _jpeg_data_uri(path.read_bytes())  # unreadable as an image; embed verbatim
    resized = _resize_for_report(image)
    ok, buf = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, REPORT_JPEG_QUALITY])
    if not ok:
        return _jpeg_data_uri(path.read_bytes())
    return _jpeg_data_uri(buf.tobytes())


def _fmt(value: float | None, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"  # em dash: "not available", not "zero"
    return f"{value:.{digits}f}{suffix}"


def _perforation_notation(sides: dict[str, PerforationSide]) -> str:
    """Compound notation, e.g. '11 1/2 x 12' (top/bottom x left/right).

    Mirrors ``core.perforation.perforation_result_notation``, but against
    the persisted :class:`PerforationSide` model (auto merged with any
    manual correction, SPEC §6) rather than the in-memory analysis type.
    """

    def fmt(names: tuple[str, str]) -> str:
        vals = []
        for name in names:
            side = sides.get(name)
            if side is None:
                continue
            if side.kind == "imperf":
                vals.append("imperf")
            elif side.kind == "straight_edge":
                vals.append("straight edge")
            elif side.notation:
                vals.append(side.notation)
        a, b = sides.get(names[0]), sides.get(names[1])
        if a and b and a.kind == b.kind == "perforated" and a.notation == b.notation:
            return a.notation or "?"
        return "/".join(vals) if vals else "?"

    return f"{fmt(('top', 'bottom'))} x {fmt(('left', 'right'))}"


@dataclass
class PerforationRow:
    side: str
    kind: str
    gauge: str
    notation: str
    hole_count: str
    hole_diameter_mm: str
    regularity_mm: str
    source: str  # "auto" | "manual"
    manual_reason: str | None = None


@dataclass
class ColourRow:
    label: str
    lightness: str
    chroma: str
    hue: str
    a_star: str
    b_star: str
    delta_e00_spread: str
    core_delta_e: str | None
    munsell: str
    philatelic_name: str
    philatelic_match_delta_e00: str
    swatch_hex: str
    out_of_gamut: bool


@dataclass
class StampReportContext:
    stamp_id: str
    sample_id: str
    catalogue_numbers: list[str]
    country_issue: str | None
    notes: str
    width_mm: str
    height_mm: str
    crop_image: str | None
    perforation_notation: str
    perforation_rows: list[PerforationRow]
    perforation_diagnostic: str | None
    perforation_confidence: float
    perforation_warnings: list[str]
    colour_rows: list[ColourRow]
    colour_cluster_image: str | None
    colour_scatter_image: str | None
    n_inks: int
    tau: float
    mixed_pixel_fraction: str
    ink_paper_delta_e00: dict[str, str]
    colour_confidence: float
    colour_warnings: list[str]
    flags: list[str] = field(default_factory=list)


@dataclass
class ReferenceSummary:
    ref_id: str
    name: str
    created: str
    colour_model_kind: str
    cv_delta_e00_mean: str
    scale_uncertainty: str


@dataclass
class ReportContext:
    project_name: str
    project_id: str
    generated_at: str
    software_version: str
    references: list[ReferenceSummary]
    stamps: list[StampReportContext]
    notes: list[str]
    colour_overview_image: str | None = None


def _perforation_rows(perforation) -> list[PerforationRow]:
    rows = []
    effective = perforation.effective()
    for side_name in ("top", "right", "bottom", "left"):
        side = effective.get(side_name)
        if side is None:
            continue
        is_manual = side_name in perforation.manual
        rows.append(
            PerforationRow(
                side=side_name,
                kind=side.kind,
                gauge=_fmt(side.gauge, 2) if side.gauge is not None else "—",
                notation=side.notation or ("imperf" if side.kind == "imperf" else "—"),
                hole_count=str(side.hole_count) if side.hole_count is not None else "—",
                hole_diameter_mm=_fmt(side.hole_diameter_mm, 2, " mm"),
                regularity_mm=_fmt(side.spacing_std_mm, 3, " mm"),
                source="manual" if is_manual else "auto",
                manual_reason=perforation.manual_reason.get(side_name) if is_manual else None,
            )
        )
    return rows


def _colour_rows(clusters: list[ColourCluster]) -> list[ColourRow]:
    rows = []
    for c in clusters:
        lightness, chroma, hue = c.mean_lch or (None, None, None)
        _, a_star, b_star = c.mean_lab or (None, None, None)
        core_delta_e = None
        if c.core_mean_lab is not None and c.mean_lab is not None:
            core_delta_e = float(
                colour_science.difference.delta_E_CIE2000(list(c.core_mean_lab), list(c.mean_lab))
            )
        swatch = c.srgb_swatch or (128, 128, 128)
        rows.append(
            ColourRow(
                label=c.label,
                lightness=_fmt(lightness, 1),
                chroma=_fmt(chroma, 1),
                hue=_fmt(hue, 1, "°"),
                a_star=_fmt(a_star, 1),
                b_star=_fmt(b_star, 1),
                delta_e00_spread=_fmt(c.delta_e00_spread, 2),
                core_delta_e=_fmt(core_delta_e, 2) if core_delta_e is not None else None,
                munsell=c.munsell or "—",
                philatelic_name=c.philatelic_name or "—",
                philatelic_match_delta_e00=_fmt(c.philatelic_match_delta_e00, 1),
                swatch_hex="#{:02x}{:02x}{:02x}".format(*swatch),
                out_of_gamut=c.out_of_gamut,
            )
        )
    return rows


def _stamp_context(
    library_root: str | Path,
    project_id: str,
    sample_id: str,
    stamp: Stamp,
    model: ref_mod.ColourModel,
) -> StampReportContext:
    flags: list[str] = []
    if stamp.perforation.manual:
        flags.append("manual perforation override")
    if stamp.perforation.meta.confidence < 0.7:
        flags.append("low perforation confidence")
    if stamp.colour.meta.confidence and stamp.colour.meta.confidence < 0.7:
        flags.append("low colour confidence")

    crop_image = None
    try:
        crop = project_mod.load_stamp_crop(library_root, project_id, stamp.stamp_id)
        lab = colour_mod.rgb_to_lab_d50(crop, model.predict_lab)
        display_rgb = colour_mod.lab_image_to_display_rgb(lab)
        crop_image = _jpeg_data_uri(_encode_jpeg(display_rgb))
    except FileNotFoundError:
        pass

    return StampReportContext(
        stamp_id=stamp.stamp_id,
        sample_id=sample_id,
        catalogue_numbers=stamp.catalogue_numbers,
        country_issue=stamp.country_issue,
        notes=stamp.notes,
        width_mm=_fmt(stamp.geometry.width_mm, 2, " mm"),
        height_mm=_fmt(stamp.geometry.height_mm, 2, " mm"),
        crop_image=crop_image,
        perforation_notation=_perforation_notation(stamp.perforation.effective()),
        perforation_rows=_perforation_rows(stamp.perforation),
        perforation_diagnostic=_read_diagnostic(
            library_root, project_id, stamp.stamp_id, stamp.perforation.meta.diagnostic_image
        ),
        perforation_confidence=stamp.perforation.meta.confidence,
        perforation_warnings=stamp.perforation.meta.warnings,
        colour_rows=_colour_rows(stamp.colour.clusters),
        colour_cluster_image=_read_diagnostic(
            library_root, project_id, stamp.stamp_id, "diagnostics/colour_clusters.png"
        ),
        colour_scatter_image=_read_diagnostic(
            library_root, project_id, stamp.stamp_id, "diagnostics/colour_scatter.png"
        ),
        n_inks=stamp.colour.n_inks,
        tau=stamp.colour.tau,
        mixed_pixel_fraction=_fmt(
            stamp.colour.mixed_pixel_fraction * 100 if stamp.colour.mixed_pixel_fraction else None,
            1,
            "%",
        ),
        ink_paper_delta_e00={k: _fmt(v, 2) for k, v in stamp.colour.ink_paper_delta_e00.items()},
        colour_confidence=stamp.colour.meta.confidence,
        colour_warnings=stamp.colour.meta.warnings,
        flags=flags,
    )


def build_report_context(library_root: str | Path, project: Project) -> ReportContext:
    """Assemble every project/sample/stamp's data into a plain context for
    the Jinja2 template -- no Qt, no file I/O inside the template itself."""
    reference_cache: dict[str, Reference] = {}
    model_cache: dict[str, ref_mod.ColourModel] = {}
    reference_summaries: list[ReferenceSummary] = []
    stamp_contexts: list[StampReportContext] = []
    ink_cluster_entries: list[tuple[str, ColourCluster]] = []
    notes: list[str] = []

    for sample_id in project.sample_ids:
        sample = project_mod.load_sample(library_root, project.project_id, sample_id)
        if sample.ref_id not in reference_cache:
            ref = ref_mod.load_reference(library_root, sample.ref_id)
            reference_cache[sample.ref_id] = ref
            reference_summaries.append(
                ReferenceSummary(
                    ref_id=ref.ref_id,
                    name=ref.name,
                    created=ref.created,
                    colour_model_kind=ref.colour_model.kind if ref.colour_model else "—",
                    cv_delta_e00_mean=_fmt(
                        ref.colour_model.cv_delta_e00.get("mean") if ref.colour_model else None, 2
                    ),
                    scale_uncertainty=_fmt(ref.scale.uncertainty_x if ref.scale else None, 4),
                )
            )
            if ref.colour_model is not None:
                model_cache[sample.ref_id] = ref_mod.load_colour_model(library_root, sample.ref_id)

        ref = reference_cache[sample.ref_id]
        model = model_cache.get(sample.ref_id)
        if model is None or ref.scale is None:
            notes.append(
                f"sample {sample_id}: reference {sample.ref_id} missing scale or colour "
                "model; its stamps are omitted from this report"
            )
            continue

        for stamp_id in sample.stamp_ids:
            stamp = project_mod.load_stamp(library_root, project.project_id, stamp_id)
            stamp_contexts.append(
                _stamp_context(
                    library_root,
                    project.project_id,
                    sample_id,
                    stamp,
                    model,
                )
            )
            for cluster in stamp.colour.clusters:
                if cluster.label != "paper" and cluster.overview_included:
                    label = cluster.overview_label or f"{stamp.stamp_id}/{cluster.label}"
                    ink_cluster_entries.append((label, cluster))

    colour_overview_image = None
    if ink_cluster_entries:
        colour_overview_image = _png_data_uri(
            colour_mod.render_colour_overview(ink_cluster_entries)
        )

    return ReportContext(
        project_name=project.name,
        project_id=project.project_id,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        software_version=__version__,
        references=reference_summaries,
        stamps=stamp_contexts,
        notes=notes,
        colour_overview_image=colour_overview_image,
    )


def render_html(context: ReportContext) -> str:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=jinja2.select_autoescape(["html"]),
    )
    template = env.get_template("report.html")
    return template.render(report=context)


def render_pdf(html: str) -> bytes:
    return HTML(string=html).write_pdf()


def _stamp_csv_row(s: StampReportContext) -> dict[str, Any]:
    paper = next((c for c in s.colour_rows if c.label == "paper"), None)
    ink = next((c for c in s.colour_rows if c.label != "paper"), None)
    return {
        "stamp_id": s.stamp_id,
        "sample_id": s.sample_id,
        "catalogue_numbers": "; ".join(s.catalogue_numbers),
        "country_issue": s.country_issue or "",
        "width_mm": s.width_mm,
        "height_mm": s.height_mm,
        "perforation_notation": s.perforation_notation,
        "perforation_confidence": s.perforation_confidence,
        "paper_munsell": paper.munsell if paper else "",
        "paper_philatelic_name": paper.philatelic_name if paper else "",
        "ink_munsell": ink.munsell if ink else "",
        "ink_philatelic_name": ink.philatelic_name if ink else "",
        "colour_confidence": s.colour_confidence,
        "flags": "; ".join(s.flags),
    }


_CSV_FIELDNAMES = [
    "stamp_id",
    "sample_id",
    "catalogue_numbers",
    "country_issue",
    "width_mm",
    "height_mm",
    "perforation_notation",
    "perforation_confidence",
    "paper_munsell",
    "paper_philatelic_name",
    "ink_munsell",
    "ink_philatelic_name",
    "colour_confidence",
    "flags",
]


def export_csv(context: ReportContext) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDNAMES)
    writer.writeheader()
    writer.writerows(_stamp_csv_row(s) for s in context.stamps)
    return buf.getvalue()


def export_json(context: ReportContext) -> str:
    data = asdict(context)
    # The embedded base64 images make the JSON export enormous for no
    # benefit (it's meant for spreadsheet/scripted use, not for re-deriving
    # images); strip them, keeping every numeric/text field.
    for stamp in data["stamps"]:
        stamp.pop("crop_image", None)
        stamp.pop("colour_cluster_image", None)
        stamp.pop("colour_scatter_image", None)
        stamp.pop("perforation_diagnostic", None)
    return json.dumps(data, indent=2, ensure_ascii=False)
