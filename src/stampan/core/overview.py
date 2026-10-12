"""The project-wide comparison table (SPEC §9/§10): one row per measured
colour, carrying everything needed to sort, filter and compare stamps --
colour (Lab and LCh), perforation, catalogue numbers and cancellation.

Pure data; the GUI's overview and the report both build on it.
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


def project_entries(library_root: str | Path, project: Project) -> list[OverviewEntry]:
    """Every measured colour of every stamp in ``project``, in sheet order.
    Colours not drawn/measured yet (no Lab) are left out."""
    entries = []
    for sample_id in project.sample_ids:
        sample = project_mod.load_sample(library_root, project.project_id, sample_id)
        sheet = project_mod.sheet_title(sample)
        for stamp_id in sample.stamp_ids:
            stamp = project_mod.load_stamp(library_root, project.project_id, stamp_id)
            sides = stamp.perforation.effective()
            notation = perforation_mod.perforation_result_notation(sides) if sides else ""
            for cluster in stamp.colour.clusters:
                if cluster.mean_lab is None or cluster.mean_lch is None:
                    continue
                entries.append(
                    OverviewEntry(
                        stamp=stamp,
                        stamp_id=stamp_id,
                        sample_id=sample_id,
                        stamp_label=project_mod.stamp_label(stamp_id),
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
