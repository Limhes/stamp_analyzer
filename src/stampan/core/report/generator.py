"""PDF/CSV/JSON report generation (SPEC §10).

The report puts the comparison first:

1. a summary (counts, the calibration references and their accuracy),
2. every sheet in calibrated colour with its stamps numbered,
3. comparison charts -- ink colour by catalogue number, perforation,
   cancellation date and type, a horizontal-vs-vertical gauge map, shade
   over time -- and a colour summary per catalogue number,
4. a one-line-per-stamp index,
5. stamp cards, four to a page: a large calibrated-colour picture with the
   essentials (perforation, cancellation, colours, size, flags).

Per-side perforation diagnostics stay in the project folder and the GUI;
they are not repeated here. CSV and JSON carry the per-stamp data for use
outside the PDF.
"""

from __future__ import annotations

import base64
import csv
import io
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import colour as colour_science
import cv2
import jinja2
import numpy as np
from weasyprint import HTML

from stampan import __version__
from stampan.core import colour as colour_mod
from stampan.core import overview as overview_mod
from stampan.core import perforation as perforation_mod
from stampan.core import project as project_mod
from stampan.core.calibration import reference as ref_mod
from stampan.core.models import LOW_CONFIDENCE, Cancellation, Project, Reference, Stamp
from stampan.core.report import charts

TEMPLATES_DIR = Path(__file__).parent / "templates"

#: Stamp pictures fill a quarter page (~85 mm); 900 px is ~270 dpi there.
STAMP_IMAGE_MAX_PX = 900
#: Sheets fill the page width (~180 mm).
SHEET_IMAGE_MAX_PX = 1800
JPEG_QUALITY = 85
NO_CATALOGUE = "no catalogue no."


# -- small helpers ---------------------------------------------------------------


def _fmt(value: float | None, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"  # "not available", not "zero"
    return f"{value:.{digits}f}{suffix}"


def _data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def _jpeg_uri(rgb: np.ndarray, max_px: int) -> str:
    h, w = rgb.shape[:2]
    scale = min(1.0, max_px / max(h, w))
    if scale < 1.0:
        rgb = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(
        ".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
    )
    if not ok:
        raise RuntimeError("failed to encode report image")
    return _data_uri(buf.tobytes(), "image/jpeg")


def _calibrated_display(rgb: np.ndarray, model, max_px: int) -> np.ndarray:
    """Scanner RGB -> Lab (the reference's colour model) -> display sRGB,
    downsampled first so a whole sheet stays cheap."""
    h, w = rgb.shape[:2]
    scale = min(1.0, max_px / max(h, w))
    if scale < 1.0:
        rgb = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return colour_mod.lab_image_to_display_rgb(colour_mod.rgb_to_lab_d50(rgb, model.predict_lab))


def _hex(swatch) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(swatch or (128, 128, 128)))


def cancellation_text(c: Cancellation) -> str:
    """'Numeral, 1889-03-14' / 'Pen cancel, c. 1889 (estimated)'."""
    date = ""
    if c.date:
        date = f"c. {c.date} (estimated)" if c.date_estimated else c.date
    return ", ".join(p for p in (c.type or "", date) if p)


def _notation(text: str) -> str:
    return text.replace(" 1/4", "¼").replace(" 1/2", "½").replace(" 3/4", "¾").replace(" x ", " × ")


# -- context dataclasses ------------------------------------------------------


@dataclass
class ReferenceSummary:
    name: str
    created: str
    colour_model_kind: str
    cv_delta_e00_mean: str
    cv_delta_e00_p95: str
    resolution: str
    px_per_mm: str


@dataclass
class SheetContext:
    title: str
    scanned: str
    n_stamps: int
    image: str | None


@dataclass
class ChartContext:
    title: str
    caption: str
    image: str


@dataclass
class ColourLine:
    label: str
    philatelic_name: str
    lch: str
    swatch_hex: str


@dataclass
class StampCard:
    number: str  # "7"
    label: str  # "Stamp 7"
    stamp_id: str
    sheet: str
    catalogue: str
    country_issue: str
    perforation: str
    perforation_detail: str  # per-side gauges
    perforation_by_hand: bool
    perforation_confidence: float | None
    cancellation: str
    colours: list[ColourLine]
    size: str
    notes: str
    flags: list[str]
    image: str | None


@dataclass
class GroupSummary:
    """Colour statistics of one catalogue number's ink measurements."""

    name: str
    n_stamps: int
    perforations: str
    mean_lch: str
    spread_de00: str  # max ΔE00 of a member from the group mean
    swatch_hex: str


@dataclass
class PerforationTable:
    """How many stamps show each perforation, per catalogue number."""

    columns: list[str]  # catalogue numbers
    rows: list[tuple[str, list[int], int]]  # (notation, count per column, total)


def _perforation_table(cards: list[StampCard]) -> PerforationTable | None:
    measured = [c for c in cards if c.perforation]
    if not measured:
        return None
    columns = sorted({c.catalogue or NO_CATALOGUE for c in measured},
                     key=lambda n: (n == NO_CATALOGUE, n))  # fmt: skip
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for c in measured:
        counts[c.perforation][c.catalogue or NO_CATALOGUE] += 1
    rows = [
        (notation, [per_cat[col] for col in columns], sum(per_cat.values()))
        for notation, per_cat in counts.items()
    ]
    rows.sort(key=lambda row: -row[2])
    return PerforationTable(columns=columns if columns != [NO_CATALOGUE] else [], rows=rows)


@dataclass
class ReportContext:
    project_name: str
    project_id: str
    generated_at: str
    software_version: str
    n_sheets: int
    n_stamps: int
    n_perforation: int
    n_colour: int
    n_dated: int
    references: list[ReferenceSummary]
    sheets: list[SheetContext]
    charts: list[ChartContext]
    groups: list[GroupSummary]
    perforations: PerforationTable | None
    stamps: list[StampCard]
    notes: list[str] = field(default_factory=list)
    #: The first colour chart as a standalone PNG (written next to the PDF).
    colour_overview_png: bytes | None = None


# -- building ------------------------------------------------------------------


def _sheet_image(library_root, project_id: str, sample, model, scale) -> str:
    """The sheet in calibrated colour with each stamp's current rectangle
    and number -- the same numbers as the stamp cards."""
    scan, _ = project_mod.load_sample_scan(library_root, project_id, sample)
    out = np.ascontiguousarray(_calibrated_display(scan, model, SHEET_IMAGE_MAX_PX))
    f = out.shape[1] / scan.shape[1]
    thickness = max(2, round(out.shape[1] / 600))
    font_scale = out.shape[1] / 1400
    for stamp_id in sample.stamp_ids:
        g = project_mod.load_stamp(library_root, project_id, stamp_id).geometry
        if g.sample_centre_px is None or not g.width_mm or not g.height_mm:
            continue
        size = (g.width_mm * scale[0] * f, g.height_mm * scale[1] * f)
        centre = (g.sample_centre_px[0] * f, g.sample_centre_px[1] * f)
        box = cv2.boxPoints((centre, size, g.rotation_deg or 0.0)).astype(np.int32)
        cv2.drawContours(out, [box], 0, (0, 114, 178), thickness, cv2.LINE_AA)
        text = project_mod.stamp_label(stamp_id).replace("Stamp ", "")
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
        x, y = int(centre[0] - tw / 2), int(centre[1] + th / 2)
        cv2.rectangle(out, (x - 4, y - th - 4), (x + tw + 4, y + 5), (255, 255, 255), -1)
        cv2.putText(
            out, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 70, 130), thickness,
            cv2.LINE_AA,
        )  # fmt: skip
    return _jpeg_uri(out, SHEET_IMAGE_MAX_PX)


def _stamp_card(library_root, project_id, sheet: str, stamp: Stamp, model) -> StampCard:
    sides = stamp.perforation.effective()
    manual = stamp.perforation.manual
    flags = []
    conf = stamp.perforation.meta.confidence if stamp.perforation.auto else None
    if conf is not None and conf < LOW_CONFIDENCE and not manual:
        flags.append("low perforation confidence")
    if stamp.condition == "on_paper":
        flags.append("on paper")
    detail = []
    for name in perforation_mod.SIDES:
        side = sides.get(name)
        if side is None:
            continue
        if side.kind == "perforated" and side.gauge:
            value = f"{side.gauge:.2f}"
        else:
            value = {"imperf": "imperf", "straight_edge": "straight edge"}.get(side.kind, "?")
        detail.append(f"{name[0].upper()} {value}{'*' if name in manual else ''}")
    colours = [
        ColourLine(
            label=c.label,
            philatelic_name=c.philatelic_name or "",
            lch=" / ".join(f"{v:.1f}" for v in c.mean_lch),
            swatch_hex=_hex(c.srgb_swatch),
        )
        for c in stamp.colour.clusters
        if c.mean_lch is not None
    ]
    image = None
    try:
        crop = project_mod.load_stamp_crop(library_root, project_id, stamp.stamp_id)
        image = _jpeg_uri(_calibrated_display(crop, model, STAMP_IMAGE_MAX_PX), STAMP_IMAGE_MAX_PX)
    except FileNotFoundError:
        flags.append("crop missing")
    g = stamp.geometry
    label = project_mod.stamp_label(stamp.stamp_id)
    return StampCard(
        number=label.replace("Stamp ", ""),
        label=label,
        stamp_id=stamp.stamp_id,
        sheet=sheet,
        catalogue=", ".join(stamp.catalogue_numbers),
        country_issue=stamp.country_issue or "",
        perforation=_notation(perforation_mod.perforation_result_notation(sides)) if sides else "",
        perforation_detail="  ".join(detail),
        perforation_by_hand=bool(manual),
        perforation_confidence=conf,
        cancellation=cancellation_text(stamp.cancellation),
        colours=colours,
        size=f"{g.width_mm:.1f} × {g.height_mm:.1f} mm" if g.width_mm and g.height_mm else "",
        notes=stamp.notes.strip(),
        flags=flags,
        image=image,
    )


def _group_summaries(entries: list[overview_mod.OverviewEntry]) -> list[GroupSummary]:
    by_cat: dict[str, list] = defaultdict(list)
    for e in entries:
        by_cat[e.catalogue or NO_CATALOGUE].append(e)
    if set(by_cat) <= {NO_CATALOGUE}:
        return []
    result = []
    for name in sorted(by_cat, key=lambda n: (n == NO_CATALOGUE, n)):
        members = by_cat[name]
        labs = np.array([e.lab for e in members], dtype=np.float64)
        mean = labs.mean(axis=0)
        spread = float(np.max(colour_science.delta_E(labs, mean, method="CIE 2000")))
        lch = colour_science.Lab_to_LCHab(mean)
        rgb = colour_mod.lab_image_to_display_rgb(mean.reshape(1, 1, 3))[0, 0]
        perfs = sorted({_notation(e.perforation) for e in members if e.perforation})
        result.append(
            GroupSummary(
                name=name,
                n_stamps=len({e.stamp_id for e in members}),
                perforations=", ".join(perfs[:4]) + (" …" if len(perfs) > 4 else ""),
                mean_lch=f"{lch[0]:.1f} / {lch[1]:.1f} / {lch[2]:.1f}",
                spread_de00=f"{spread:.1f}",
                swatch_hex=_hex(tuple(int(v) for v in rgb)),
            )
        )
    return result


def _charts(entries, stamps_for_map) -> list[ChartContext]:
    result: list[ChartContext] = []

    def add(title: str, caption: str, png: bytes | None) -> None:
        if png:
            result.append(ChartContext(title, caption, _data_uri(png, "image/png")))

    def catalogue(e) -> str:
        return e.catalogue or NO_CATALOGUE

    add(
        "Ink colour by catalogue number",
        "Mean ink colour of each measured area, labelled with the stamp number. Shades of one "
        "catalogue number should cluster; outliers are worth a second look.",
        charts.colour_by_group(entries, catalogue, "Ink colour by catalogue no."),
    )
    add(
        "Ink colour by perforation",
        "Grouped by the full perforation notation (top/bottom × left/right).",
        charts.colour_by_group(
            entries,
            lambda e: _notation(e.perforation) if e.perforation else "not measured",
            "Ink colour by perforation",
        ),
    )
    add(
        "Ink colour by cancellation date",
        "Colour scale: cancellation year. Squares are estimated dates; hollow grey circles "
        "have no date.",
        charts.colour_by_year(entries),
    )
    add(
        "Ink colour by cancellation type",
        "",
        charts.colour_by_group(
            entries,
            lambda e: e.cancellation_type or "not recorded",
            "Ink colour by cancellation type",
        ),
    )
    add(
        "Shade over time",
        "Hue angle and chroma of the ink against the cancellation year, by catalogue number. "
        "Estimated dates are drawn slightly to the right of their year.",
        charts.shade_over_time(entries, catalogue, "Catalogue no."),
    )
    add(
        "Perforation map",
        "Mean horizontal against mean vertical gauge, one point per stamp, by catalogue "
        "number. Compound perforations lie off the diagonal; diamonds were set by hand.",
        charts.perforation_map(stamps_for_map, "Perforation by catalogue no."),
    )
    return result


def build_report_context(library_root: str | Path, project: Project) -> ReportContext:
    """Assemble the plain-data context for the template -- no Qt, and no
    file I/O inside the template."""
    references: dict[str, Reference] = {}
    models: dict[str, ref_mod.ColourModel] = {}
    sheets: list[SheetContext] = []
    cards: list[StampCard] = []
    stamps_for_map = []
    notes: list[str] = []
    n_perf = n_colour = n_dated = 0
    reported_samples = set()

    for sample_id in project.sample_ids:
        sample = project_mod.load_sample(library_root, project.project_id, sample_id)
        if sample.ref_id not in references:
            references[sample.ref_id] = ref_mod.load_reference(library_root, sample.ref_id)
            if references[sample.ref_id].colour_model is not None:
                models[sample.ref_id] = ref_mod.load_colour_model(library_root, sample.ref_id)
        ref, model = references[sample.ref_id], models.get(sample.ref_id)
        title = project_mod.sheet_title(sample)
        if model is None or ref.scale is None:
            notes.append(
                f"{title}: its reference “{ref.name}” has no colour model or scale; its "
                "stamps are left out of this report."
            )
            continue
        reported_samples.add(sample_id)
        scale = (ref.scale.px_per_mm_x, ref.scale.px_per_mm_y)
        sheets.append(
            SheetContext(
                title=title,
                scanned=(sample.scan_time or "")[:10],
                n_stamps=len(sample.stamp_ids),
                image=_sheet_image(library_root, project.project_id, sample, model, scale),
            )
        )
        for stamp_id in sample.stamp_ids:
            stamp = project_mod.load_stamp(library_root, project.project_id, stamp_id)
            card = _stamp_card(library_root, project.project_id, title, stamp, model)
            cards.append(card)
            n_perf += bool(stamp.perforation.effective())
            n_colour += any(c.mean_lch is not None for c in stamp.colour.clusters)
            n_dated += bool(stamp.cancellation.date)
            stamps_for_map.append((card.number, stamp, card.catalogue or NO_CATALOGUE))

    # Ink colours only, and only those left ticked in the project overview.
    entries = [
        e
        for e in overview_mod.project_entries(library_root, project)
        if e.sample_id in reported_samples and not e.is_paper and e.cluster.overview_included
    ]
    chart_list = _charts(entries, stamps_for_map)
    first_colour = next((c for c in chart_list if c.title.startswith("Ink colour")), None)

    ref_summaries = []
    for ref in references.values():
        cv = ref.colour_model.cv_delta_e00 if ref.colour_model else {}
        dpi = ref.scanner_options.get("resolution")
        ref_summaries.append(
            ReferenceSummary(
                name=ref.name,
                created=ref.created,
                colour_model_kind=(
                    ref.colour_model.kind.replace("_", " ") if ref.colour_model else "—"
                ),
                cv_delta_e00_mean=_fmt(cv.get("mean")),
                cv_delta_e00_p95=_fmt(cv.get("p95")),
                resolution=f"{float(dpi):.0f} dpi" if dpi else "—",
                px_per_mm=_fmt(ref.scale.px_per_mm_x if ref.scale else None, 3),
            )
        )

    return ReportContext(
        project_name=project.name,
        project_id=project.project_id,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        software_version=__version__,
        n_sheets=len(sheets),
        n_stamps=len(cards),
        n_perforation=n_perf,
        n_colour=n_colour,
        n_dated=n_dated,
        references=ref_summaries,
        sheets=sheets,
        charts=chart_list,
        groups=_group_summaries(entries),
        perforations=_perforation_table(cards),
        stamps=cards,
        notes=notes,
        colour_overview_png=(
            base64.b64decode(first_colour.image.split(",", 1)[1]) if first_colour else None
        ),
    )


# -- rendering and export ----------------------------------------------------------


def render_html(context: ReportContext) -> str:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=jinja2.select_autoescape(["html"]),
    )
    return env.get_template("report.html").render(report=context)


def render_pdf(html: str) -> bytes:
    return HTML(string=html).write_pdf()


_CSV_FIELDS = [
    "stamp_id", "stamp", "sheet", "catalogue", "country_issue", "perforation",
    "perforation_sides", "perforation_by_hand", "perforation_confidence", "cancellation",
    "colours", "size", "flags", "notes",
]  # fmt: skip


def export_csv(context: ReportContext) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS)
    writer.writeheader()
    for s in context.stamps:
        writer.writerow(
            {
                "stamp_id": s.stamp_id,
                "stamp": s.label,
                "sheet": s.sheet,
                "catalogue": s.catalogue,
                "country_issue": s.country_issue,
                "perforation": s.perforation,
                "perforation_sides": s.perforation_detail,
                "perforation_by_hand": s.perforation_by_hand,
                "perforation_confidence": _fmt(s.perforation_confidence),
                "cancellation": s.cancellation,
                "colours": "; ".join(
                    f"{c.label}: {c.philatelic_name} (LCh {c.lch})" for c in s.colours
                ),
                "size": s.size,
                "flags": "; ".join(s.flags),
                "notes": s.notes,
            }
        )
    return buf.getvalue()


def export_json(context: ReportContext) -> str:
    data: dict[str, Any] = asdict(context)
    # Embedded images make the JSON huge and are of no use outside the PDF.
    for key in ("colour_overview_png", "charts"):
        data.pop(key, None)
    for key in ("sheets", "stamps"):
        for item in data[key]:
            item.pop("image", None)
    return json.dumps(data, indent=2, ensure_ascii=False)
