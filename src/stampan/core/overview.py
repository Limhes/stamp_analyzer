"""The project-wide comparison table (SPEC §9/§10): one row per measured
colour, carrying everything needed to sort, filter and compare stamps --
colour (Lab and LCh), perforation, catalogue numbers and cancellation.

Pure data; the GUI's overview, the exports and the published table build on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from stampan.core import perforation as perforation_mod
from stampan.core import project as project_mod
from stampan.core.models import ColourCluster, Project, Stamp


@dataclass
class OverviewEntry:
    stamp: Stamp  # the loaded stamp ``cluster`` belongs to (for saving label edits)
    stamp_id: str
    sample_id: str
    stamp_label: str  # "Stamp 4"
    sheet: str  # the sheet's name or "Sheet 1"
    cluster: ColourCluster
    is_paper: bool
    lab: tuple[float, float, float]
    lch: tuple[float, float, float]
    perforation: str  # compound notation, "" if not measured
    #: Per-side gauges (quarter-rounded notation) and kinds, for filtering
    #: on e.g. "any side 11" without parsing ``perforation``.
    side_gauges: dict[str, str] = field(default_factory=dict)
    perforation_manual: bool = False
    catalogue: str = ""
    country_issue: str = ""
    cancellation_type: str = ""
    cancellation_date: str = ""
    cancellation_estimated: bool = False

    @property
    def default_label(self) -> str:
        return f"{self.stamp_label} · {self.cluster.label}"

    @property
    def label(self) -> str:
        return self.cluster.overview_label or self.default_label

    @property
    def year(self) -> int | None:
        return int(self.cancellation_date[:4]) if self.cancellation_date else None


def _side_values(stamp: Stamp) -> dict[str, str]:
    values = {}
    for side, result in stamp.perforation.effective().items():
        if result.kind == "perforated" and result.notation:
            values[side] = result.notation
        elif result.kind == "imperf":
            values[side] = "imperf"
        elif result.kind == "straight_edge":
            values[side] = "straight edge"
    return values


def stamp_entries(stamp: Stamp, sample_id: str, sheet: str) -> list[OverviewEntry]:
    """The rows of one stamp: one per measured colour (colours not drawn or
    measured yet, without Lab, are left out)."""
    sides = stamp.perforation.effective()
    notation = perforation_mod.perforation_result_notation(sides) if sides else ""
    entries = []
    for cluster in stamp.colour.clusters:
        if cluster.mean_lab is None or cluster.mean_lch is None:
            continue
        entries.append(
            OverviewEntry(
                stamp=stamp,
                stamp_id=stamp.stamp_id,
                sample_id=sample_id,
                stamp_label=project_mod.stamp_label(stamp.stamp_id),
                sheet=sheet,
                cluster=cluster,
                is_paper=cluster.label.strip().lower() == "paper",
                lab=tuple(cluster.mean_lab),
                lch=tuple(cluster.mean_lch),
                perforation=notation,
                side_gauges=_side_values(stamp),
                perforation_manual=bool(stamp.perforation.manual),
                catalogue=", ".join(stamp.catalogue_numbers),
                country_issue=stamp.country_issue or "",
                cancellation_type=stamp.cancellation.type or "",
                cancellation_date=stamp.cancellation.date or "",
                cancellation_estimated=stamp.cancellation.date_estimated,
            )
        )
    return entries


def project_entries(library_root: str | Path, project: Project) -> list[OverviewEntry]:
    """Every measured colour of every stamp in ``project``, in sheet order."""
    entries = []
    for sample_id in project.sample_ids:
        sample = project_mod.load_sample(library_root, project.project_id, sample_id)
        sheet = project_mod.sheet_title(sample)
        for stamp_id in sample.stamp_ids:
            stamp = project_mod.load_stamp(library_root, project.project_id, stamp_id)
            entries.extend(stamp_entries(stamp, sample_id, sheet))
    return entries


def gauge_choices(entries: list[OverviewEntry]) -> list[str]:
    """Distinct per-side perforation values, in gauge order ("10 1/2" < "11")."""

    def key(value: str) -> tuple[int, float]:
        whole, _, frac = value.partition(" ")
        try:
            num = float(whole) + (_fraction(frac) if frac else 0.0)
        except ValueError:
            return (1, 0.0)  # imperf / straight edge after the gauges
        return (0, num)

    values = {v for e in entries for v in e.side_gauges.values()}
    return sorted(values, key=key)


def _fraction(text: str) -> float:
    num, _, den = text.partition("/")
    return float(num) / float(den)


# -- export ------------------------------------------------------------------------

#: (header, Excel number format or None for text). Numbers stay numbers, so
#: they sort and calculate in a spreadsheet.
EXPORT_COLUMNS: list[tuple[str, str | None]] = [
    ("Stamp", None), ("Sheet", None), ("Catalogue no.", None), ("Country / issue", None),
    ("Colour", None), ("Area", None), ("Philatelic name", None), ("Swatch", None),
    ("L*", "0.0"), ("a*", "0.0"), ("b*", "0.0"), ("C*", "0.0"), ("h°", "0.0"),
    ("ΔE00 spread", "0.00"), ("Perforation", None),
    ("Gauge top", "0.00"), ("Gauge bottom", "0.00"), ("Gauge left", "0.00"),
    ("Gauge right", "0.00"), ("Perforation by hand", None), ("Cancellation", None),
    ("Cancellation date", None), ("Date estimated", None), ("In overview", None),
    ("Stamp ID", None),
]  # fmt: skip


def _side_value(entry: OverviewEntry, side: str) -> float | str | None:
    result = entry.stamp.perforation.effective().get(side)
    if result is None:
        return None
    if result.kind == "perforated":
        return round(result.gauge, 2) if result.gauge else None
    return "imperf" if result.kind == "imperf" else "straight edge"


def export_rows(entries: list[OverviewEntry]) -> list[list]:
    """One row per entry, matching ``EXPORT_COLUMNS``. Numbers are floats
    (None when unknown); yes/no fields are "yes"/"" for readability."""
    rows = []
    for e in entries:
        c = e.cluster
        rows.append(
            [
                e.stamp_label, e.sheet, e.catalogue, e.country_issue, e.label, c.label,
                c.philatelic_name or "",
                "#{:02x}{:02x}{:02x}".format(*c.srgb_swatch) if c.srgb_swatch else "",
                *(round(v, 2) for v in e.lab), round(e.lch[1], 2), round(e.lch[2], 2),
                round(c.delta_e00_spread, 2) if c.delta_e00_spread is not None else None,
                e.perforation.replace(" x ", " × "),
                *(_side_value(e, s) for s in perforation_mod.SIDES),
                "yes" if e.perforation_manual else "", e.cancellation_type,
                e.cancellation_date, "yes" if e.cancellation_estimated else "",
                "yes" if c.overview_included else "", e.stamp_id,
            ]
        )  # fmt: skip
    return rows


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def to_tsv(entries: list[OverviewEntry]) -> str:
    """Tab-separated with a header row: what Excel/LibreOffice expect on the
    clipboard. Decimal point is ".", as in the CSV."""
    lines = ["\t".join(h for h, _ in EXPORT_COLUMNS)]
    for row in export_rows(entries):
        lines.append("\t".join(_text(v).replace("\t", " ").replace("\n", " ") for v in row))
    return "\n".join(lines) + "\n"


def to_html(entries: list[OverviewEntry]) -> str:
    """A plain HTML table for the clipboard. Spreadsheets paste it with
    numbers as numbers (independent of the locale's decimal separator) and
    the swatch column filled with the measured colour."""
    from html import escape

    swatch_col = [h for h, _ in EXPORT_COLUMNS].index("Swatch")
    out = ["<table><tr>"]
    out += [f"<th>{escape(h)}</th>" for h, _ in EXPORT_COLUMNS]
    out.append("</tr>")
    for row in export_rows(entries):
        out.append("<tr>")
        for i, v in enumerate(row):
            if i == swatch_col and v:
                out.append(f'<td style="background:{v}">{v}</td>')
            elif isinstance(v, float):
                out.append(f'<td x:num="{v}">{v:g}</td>')
            else:
                out.append(f"<td>{escape(_text(v))}</td>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def write_csv(path: str | Path, entries: list[OverviewEntry]) -> None:
    import csv

    with Path(path).open("w", newline="", encoding="utf-8-sig") as fh:  # BOM: Excel reads UTF-8
        writer = csv.writer(fh)
        writer.writerow([h for h, _ in EXPORT_COLUMNS])
        writer.writerows([_text(v) for v in row] for row in export_rows(entries))


def write_xlsx(path: str | Path, entries: list[OverviewEntry], title: str = "Stamps") -> None:
    """An Excel workbook: bold frozen header, autofilter, number formats,
    swatch cells filled with the measured colour, sensible column widths."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = title[:31] or "Stamps"
    ws.append([h for h, _ in EXPORT_COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    swatch_col = [h for h, _ in EXPORT_COLUMNS].index("Swatch") + 1
    for row in export_rows(entries):
        ws.append(row)
        swatch = ws.cell(row=ws.max_row, column=swatch_col)
        if swatch.value:
            colour = swatch.value.lstrip("#").upper()
            swatch.fill = PatternFill("solid", start_color=colour, end_color=colour)
    for col, (header, number_format) in enumerate(EXPORT_COLUMNS, start=1):
        letter = get_column_letter(col)
        if number_format:
            for cell in ws[letter][1:]:
                cell.number_format = number_format
        longest = max(
            [len(header)] + [len(_text(c.value)) for c in ws[letter][1:]], default=len(header)
        )
        ws.column_dimensions[letter].width = min(max(8, longest + 2), 40)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(path)
