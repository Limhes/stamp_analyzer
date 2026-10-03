import json

import numpy as np
import pytest
from typer.testing import CliRunner

from stampan.cli import app
from stampan.core.calibration import reference as ref_mod
from stampan.core.io import ScanMetadata, write_tiff
from tests.synth import SideSpec, SynthSpec
from tests.synth_sheet import SheetSpec, StampPlacement, generate_synthetic_sheet
from tests.test_reference import _build_synthetic_reference_scan

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_reference_list_empty(tmp_path):
    result = runner.invoke(app, ["reference", "list", "--library-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "no references found" in result.stdout


def test_reference_create_list_show_roundtrip(tmp_path):
    scan, corners, x_axis, y_axis = _build_synthetic_reference_scan()
    scan_path = tmp_path / "ref_scan.tif"
    write_tiff(scan_path, scan.astype(np.uint16))

    library_root = tmp_path / "library"

    def fmt(axis):
        return ",".join(
            str(v)
            for v in (
                axis.centre_xy[0],
                axis.centre_xy[1],
                axis.length_px,
                axis.width_px,
                axis.angle_deg,
                axis.tick_spacing_mm,
                axis.px_per_mm_hint,
            )
        )

    corners_str = ",".join(str(v) for v in (*corners.a1, *corners.a22, *corners.l1, *corners.l22))

    result = runner.invoke(
        app,
        [
            "reference",
            "create",
            "--scan",
            str(scan_path),
            "--name",
            "cli test reference",
            "--ruler-x",
            fmt(x_axis),
            "--ruler-y",
            fmt(y_axis),
            "--it8-corners",
            corners_str,
            "--cgats",
            "reference_data/R240103.txt",
            "--library-root",
            str(library_root),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "saved reference" in result.stdout

    list_result = runner.invoke(app, ["reference", "list", "--library-root", str(library_root)])
    assert list_result.exit_code == 0
    assert "cli test reference" in list_result.stdout

    ref_id = list_result.stdout.split()[0]
    show_result = runner.invoke(
        app, ["reference", "show", ref_id, "--library-root", str(library_root)]
    )
    assert show_result.exit_code == 0
    summary = json.loads(show_result.stdout)
    assert summary["ref_id"] == ref_id
    assert summary["colour_model_kind"] in ("matrix3x3", "root_poly2", "root_poly3")


def test_detect_command_writes_project_and_stamps(tmp_path):
    scan, corners, x_axis, y_axis = _build_synthetic_reference_scan()
    library_root = tmp_path / "library"
    ref_result = ref_mod.build_reference(
        name="detect-test reference",
        scan=scan,
        scan_metadata=ScanMetadata(scanner_options={"resolution": 1200}),
        x_axis=x_axis,
        it8_corners=corners,
        cgats_reference_path="reference_data/R240103.txt",
        y_axis=y_axis,
    )
    ref_mod.save_reference(ref_result, library_root)
    ref_id = ref_result.reference.ref_id

    placements = [
        StampPlacement(
            spec=SynthSpec(
                width_mm=20,
                height_mm=25,
                px_per_mm=10,
                sides={s: SideSpec(gauge=12.0) for s in ("top", "bottom", "left", "right")},
                seed=i,
            ),
            centre_mm=(25 + i * 35, 30),
        )
        for i in range(3)
    ]
    sheet = SheetSpec(width_mm=130, height_mm=60, px_per_mm=10, seed=1)
    sample_img, _ = generate_synthetic_sheet(sheet, placements)
    sample_path = tmp_path / "sample_sheet.tif"
    write_tiff(sample_path, sample_img.astype(np.uint16))

    result = runner.invoke(
        app,
        [
            "detect",
            str(sample_path),
            "--reference",
            ref_id,
            "--project",
            "detect cli test",
            "--library-root",
            str(library_root),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "detected 3 stamp(s)" in result.stdout

    from stampan.core import project as project_mod

    projects = project_mod.list_projects(library_root)
    assert len(projects) == 1
    project = projects[0]
    assert len(project.sample_ids) == 1

    sample = project_mod.load_sample(library_root, project.project_id, project.sample_ids[0])
    assert len(sample.stamp_ids) == 3

    for stamp_id in sample.stamp_ids:
        stamp = project_mod.load_stamp(library_root, project.project_id, stamp_id)
        sides_mm = sorted([stamp.geometry.width_mm, stamp.geometry.height_mm])
        assert sides_mm[0] == pytest.approx(20.0, abs=1.0)
        assert sides_mm[1] == pytest.approx(25.0, abs=1.0)
        crop = project_mod.load_stamp_crop(library_root, project.project_id, stamp_id)
        assert crop.dtype == np.uint16

    overview_path = (
        project_mod.project_dir(library_root, project.project_id)
        / "diagnostics"
        / f"{sample.sample_id}_overview.png"
    )
    assert overview_path.exists()

    perf_result = runner.invoke(
        app,
        [
            "perf",
            project.project_id,
            "--library-root",
            str(library_root),
        ],
    )
    assert perf_result.exit_code == 0, perf_result.stdout
    assert "measured perforation on 3/3" in perf_result.stdout

    for stamp_id in sample.stamp_ids:
        stamp = project_mod.load_stamp(library_root, project.project_id, stamp_id)
        assert stamp.perforation.auto  # all 4 sides populated
        assert stamp.perforation.meta.diagnostic_image == "diagnostics/perforation.png"
        diag_path = (
            project_mod.project_dir(library_root, project.project_id)
            / "stamps"
            / stamp_id
            / "diagnostics"
            / "perforation.png"
        )
        assert diag_path.exists()
