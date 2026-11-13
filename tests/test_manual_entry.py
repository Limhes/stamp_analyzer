"""Values typed by hand -- perforation, dates, catalogue numbers -- and the
workflow functions that both the stamp page and the overview table use."""

import pytest

from stampan.core import models, workflow
from stampan.core import perforation as perf
from stampan.core import project as proj
from tests.test_overview import _project_with_stamp


@pytest.mark.parametrize(
    "text, expected",
    [
        ("11", {s: ("perforated", 11.0) for s in perf.SIDES}),
        (
            "11½ × 12",
            {"top": ("perforated", 11.5), "bottom": ("perforated", 11.5),
             "left": ("perforated", 12.0), "right": ("perforated", 12.0)},
        ),
        (
            "11 1/2/12 x imperf",
            {"top": ("perforated", 11.5), "bottom": ("perforated", 12.0),
             "left": ("imperf", None), "right": ("imperf", None)},
        ),
        (
            "12,75 X se",
            {"top": ("perforated", 12.75), "bottom": ("perforated", 12.75),
             "left": ("straight_edge", None), "right": ("straight_edge", None)},
        ),
    ],
)  # fmt: skip
def test_sides_from_notation(text, expected):
    assert perf.sides_from_notation(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "11 x 12 x 13", "40", "11/12/13", "11 x"])
def test_sides_from_notation_rejects(text):
    with pytest.raises(ValueError):
        perf.sides_from_notation(text)


def test_pretty_notation_round_trips():
    notation = "11 1/2/12 x 11 3/4"
    assert perf.pretty_notation(notation) == "11½/12 × 11¾"
    sides = perf.sides_from_notation(perf.pretty_notation(notation))
    assert sides["top"] == ("perforated", 11.5) and sides["left"] == ("perforated", 11.75)


@pytest.mark.parametrize(
    "text, expected",
    [("1889-3", ("1889-03", False)), ("c. 1889", ("1889", True)),
     ("~1889-03-14", ("1889-03-14", True)), ("ca.1890", ("1890", True)), ("", (None, False))],
)  # fmt: skip
def test_parse_cancellation_date(text, expected):
    assert models.parse_cancellation_date(text) == expected


def test_catalogue_numbers_and_colour_labels():
    assert models.parse_catalogue_numbers(" Mi  23 , ,SG 4") == ["Mi 23", "SG 4"]
    assert models.next_colour_label([]) == "ink"
    assert models.next_colour_label(["ink", "paper"]) == "ink2"
    assert models.next_colour_label(["ink", "ink2", "ink4"]) == "ink3"


def test_update_stamp_details_is_all_or_nothing(tmp_path):
    project, stamp_id = _project_with_stamp(tmp_path)
    pid = project.project_id
    with pytest.raises(workflow.WorkflowError):
        workflow.update_stamp_details(
            tmp_path, pid, stamp_id, catalogue="SG 1", cancellation_date="1889-13"
        )
    assert proj.load_stamp(tmp_path, pid, stamp_id).catalogue_numbers == ["Mi 23"]

    stamp = workflow.update_stamp_details(
        tmp_path, pid, stamp_id, cancellation_type="  Ring  cancel ", cancellation_date="1890"
    )
    assert stamp.cancellation.type == "Ring cancel"
    assert stamp.cancellation.date == "1890" and not stamp.cancellation.date_estimated
    # The stamp page's checkbox and a "c." prefix both mark an estimate.
    stamp = workflow.update_stamp_details(
        tmp_path, pid, stamp_id, cancellation_date="1890", date_estimated=True
    )
    assert stamp.cancellation.date_estimated


def test_set_perforation_text(tmp_path):
    project, stamp_id = _project_with_stamp(tmp_path)
    pid = project.project_id
    stamp = workflow.set_perforation_text(tmp_path, pid, stamp_id, "11 x 12½", reason="gauge")
    assert stamp.perforation.manual["left"].notation == "12 1/2"
    stamp = workflow.set_perforation_text(tmp_path, pid, stamp_id, "11")
    assert stamp.perforation.manual_reason["top"] == "gauge"  # kept
    with pytest.raises(workflow.WorkflowError):
        workflow.set_perforation_text(tmp_path, pid, stamp_id, "eleven")
    stamp = workflow.set_perforation_text(tmp_path, pid, stamp_id, "")
    assert stamp.perforation.manual == {}


def test_set_colour_overview(tmp_path):
    project, stamp_id = _project_with_stamp(tmp_path)
    stamp = workflow.set_colour_overview(
        tmp_path, project.project_id, stamp_id, "ink", included=False, display_label=" A "
    )
    ink = stamp.colour.clusters[0]
    assert not ink.overview_included and ink.overview_label == "A"
    with pytest.raises(workflow.WorkflowError):
        workflow.set_colour_overview(tmp_path, project.project_id, stamp_id, "nope", included=True)


def test_cancellation_place_kept_as_typed(tmp_path):
    project, stamp_id = _project_with_stamp(tmp_path)
    stamp = workflow.update_stamp_details(
        tmp_path, project.project_id, stamp_id, cancellation_place="  Гельсингфорсъ "
    )
    assert stamp.cancellation.place == "Гельсингфорсъ"
    reloaded = proj.load_stamp(tmp_path, project.project_id, stamp_id)
    assert reloaded.cancellation.place == "Гельсингфорсъ"
