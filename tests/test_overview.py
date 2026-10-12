import numpy as np
import pytest

from stampan.core import overview, workflow
from stampan.core import project as proj
from stampan.core.io import ScanMetadata
from stampan.core.models import (
    Cancellation,
    ColourCluster,
    PerforationSide,
    Stamp,
    parse_partial_date,
)


@pytest.mark.parametrize(
    "text, expected",
    [("1889", "1889"), ("1889-3", "1889-03"), ("1889.03.14", "1889-03-14"), ("", None)],
)
def test_parse_partial_date(text, expected):
    assert parse_partial_date(text) == expected


@pytest.mark.parametrize("text", ["89", "1889-13", "1889-02-30", "March 1889"])
def test_parse_partial_date_rejects(text):
    with pytest.raises(ValueError):
        parse_partial_date(text)


def _project_with_stamp(tmp_path):
    p = proj.create_project(tmp_path, "P")
    sample = proj.add_sample(
        tmp_path, p, np.zeros((8, 8, 3), np.uint16), ScanMetadata(), ref_id="r"
    )
    stamp = Stamp(stamp_id=proj.next_stamp_id(sample, 0))
    for side in ("top", "bottom", "left", "right"):
        stamp.perforation.auto[side] = PerforationSide(gauge=11.02, notation="11")
    stamp.colour.clusters = [
        ColourCluster(label="ink", mean_lab=(50.0, 40.0, 20.0), mean_lch=(50.0, 44.7, 26.6)),
        ColourCluster(label="paper", mean_lab=(90.0, 1.0, 5.0), mean_lch=(90.0, 5.1, 78.7)),
        ColourCluster(label="frame"),  # not measured: left out
    ]
    stamp.cancellation = Cancellation(type="Numeral", date="1889", date_estimated=True)
    stamp.catalogue_numbers = ["Mi 23"]
    proj.save_stamp(tmp_path, sample, stamp, np.zeros((4, 4, 3), np.uint16))
    proj.save_sample(tmp_path, sample)
    return proj.load_project(tmp_path, p.project_id), stamp.stamp_id


def test_overview_entries_and_override(tmp_path):
    project, stamp_id = _project_with_stamp(tmp_path)
    entries = overview.project_entries(tmp_path, project)
    assert [e.cluster.label for e in entries] == ["ink", "paper"]
    ink = entries[0]
    assert ink.perforation == "11 x 11" and ink.side_gauges["left"] == "11"
    assert ink.catalogue == "Mi 23" and ink.year == 1889 and ink.cancellation_estimated
    assert entries[1].is_paper

    stamp = workflow.set_perforation_override(
        tmp_path, project.project_id, stamp_id,
        {"left": ("perforated", 11.5), "right": ("imperf", None)}, "Instanta",
    )  # fmt: skip
    assert stamp.perforation.auto["left"].notation == "11"  # measured value kept
    assert stamp.perforation.effective()["left"].notation == "11 1/2"
    assert stamp.perforation.manual_reason["left"] == "Instanta"
    entries = overview.project_entries(tmp_path, project)
    assert entries[0].perforation_manual
    assert overview.gauge_choices(entries) == ["11", "11 1/2", "imperf"]

    workflow.set_perforation_override(tmp_path, project.project_id, stamp_id, {})
    assert not proj.load_stamp(tmp_path, project.project_id, stamp_id).perforation.manual
    with pytest.raises(workflow.WorkflowError):
        workflow.set_perforation_override(
            tmp_path, project.project_id, stamp_id, {"top": ("perforated", None)}
        )
