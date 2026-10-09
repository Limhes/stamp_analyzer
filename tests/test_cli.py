import json
import sys
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from stampan.cli import app
from stampan.core.calibration import reference as ref_mod
from stampan.core.calibration import scanin as scanin_mod
from stampan.core.calibration.it8 import PatchSample
from stampan.core.cgats import parse_cgats_file
from stampan.core.io import ScanMetadata, read_tiff, write_tiff
from tests.synth import SideSpec, SynthSpec
from tests.synth_sheet import SheetSpec, StampPlacement, generate_synthetic_sheet

runner = CliRunner()

FAKE_SCANIMAGE = [sys.executable, str(Path(__file__).parent / "fakes" / "fake_scanimage.py")]
FAKE_SCANIN = str(Path(__file__).parent / "fakes" / "fake_scanin.py")
REF_CGATS_PATH = "reference_data/R240103.txt"

# Same fixed synthetic "camera" matrix fake_scanin.py uses, so direct
# build_reference() calls here (not going through the CLI) and the one CLI
# invocation test (which monkeypatches scanin_mod directly, since `reference
# create` has no --scanin-cmd flag -- there's no reason production code
# needs one) agree on the same known-recoverable RGB<->XYZ relationship.
_XYZ_TO_RGB = np.array(
    [
        [2.5, -1.2, -0.3],
        [-0.9, 1.9, 0.0],
        [0.05, -0.2, 1.1],
    ]
)


def _fake_scanin_result() -> scanin_mod.ScaninResult:
    table = parse_cgats_file(REF_CGATS_PATH)
    samples = {}
    for patch_id, row in table.by_sample_id().items():
        xyz = np.array([row["XYZ_X"], row["XYZ_Y"], row["XYZ_Z"]])
        rgb_linear = np.clip((xyz @ _XYZ_TO_RGB.T) / 100.0, 0, 1)
        rgb = tuple((rgb_linear * 65535.0).tolist())
        samples[patch_id] = PatchSample(
            patch_id=patch_id,
            centre_px=(float("nan"), float("nan")),
            mean_rgb=rgb,
            std_rgb=(1.0, 1.0, 1.0),
            n_pixels=0,
        )
    return scanin_mod.ScaninResult(samples=samples, diagnostic_png=b"")


def _dummy_scan() -> np.ndarray:
    return np.full((8, 8, 3), 30000, dtype=np.uint16)


def _build_test_reference(name: str) -> ref_mod.ReferenceBuildResult:
    # dpi chosen so px_per_mm (dpi / 25.4) matches the synthetic sample
    # sheets' own px_per_mm=10 below -- detect/extract_crop interpret the
    # sample sheet's pixel sizes using the *reference's* scale, so a
    # mismatch here silently detects 0 stamps rather than erroring.
    dpi = 10.0 * 25.4
    return ref_mod.build_reference(
        name=name,
        scan=_dummy_scan(),
        scan_metadata=ScanMetadata(scanner_options={"resolution": dpi}),
        cgats_reference_path=REF_CGATS_PATH,
        dpi_x=dpi,
        dpi_y=dpi,
        dpi_source="test fixture",
        scanin_cmd=FAKE_SCANIN,
        cht_path=REF_CGATS_PATH,  # unused by the fake, just needs to exist
    )


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_reference_list_empty(tmp_path):
    result = runner.invoke(app, ["reference", "list", "--library-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "no references found" in result.stdout


def test_reference_create_list_show_roundtrip(tmp_path, monkeypatch):
    # `reference create` has no --scanin-cmd flag (no reason production
    # code needs one), so this one test monkeypatches scanin_mod directly
    # rather than going through a fake binary on PATH.
    monkeypatch.setattr(
        ref_mod.scanin_mod, "locate_and_sample_patches", lambda *a, **kw: _fake_scanin_result()
    )
    scan_path = tmp_path / "ref_scan.tif"
    write_tiff(scan_path, _dummy_scan())

    library_root = tmp_path / "library"

    result = runner.invoke(
        app,
        [
            "reference",
            "create",
            "--scan",
            str(scan_path),
            "--name",
            "cli test reference",
            "--dpi",
            "1200",
            "--cgats",
            REF_CGATS_PATH,
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
    library_root = tmp_path / "library"
    ref_result = _build_test_reference("detect-test reference")
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


def test_colour_and_report_commands_produce_outputs(tmp_path):
    from stampan.core import project as project_mod

    library_root = tmp_path / "library"
    ref_result = _build_test_reference("report-test reference")
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
        for i in range(2)
    ]
    sheet = SheetSpec(width_mm=95, height_mm=60, px_per_mm=10, seed=1)
    sample_img, _ = generate_synthetic_sheet(sheet, placements)
    # generate_synthetic_sheet renders a single grayscale channel (fine for
    # detect/perf, which handle 2D); colour.py, like a real scanner, needs
    # 3-channel RGB, so replicate it the way a neutral/grey synthetic
    # subject actually would appear on an RGB scan.
    sample_img_rgb = np.stack([sample_img] * 3, axis=-1)
    sample_path = tmp_path / "sample_sheet.tif"
    write_tiff(sample_path, sample_img_rgb.astype(np.uint16))

    result = runner.invoke(
        app,
        [
            "detect",
            str(sample_path),
            "--reference",
            ref_id,
            "--project",
            "report cli test",
            "--library-root",
            str(library_root),
        ],
    )
    assert result.exit_code == 0, result.stdout

    project = project_mod.list_projects(library_root)[0]

    perf_result = runner.invoke(
        app, ["perf", project.project_id, "--library-root", str(library_root)]
    )
    assert perf_result.exit_code == 0, perf_result.stdout

    colour_result = runner.invoke(
        app, ["colour", project.project_id, "--library-root", str(library_root)]
    )
    assert colour_result.exit_code == 0, colour_result.stdout
    assert "no inclusion area selected" in colour_result.output

    report_result = runner.invoke(
        app, ["report", project.project_id, "--library-root", str(library_root)]
    )
    assert report_result.exit_code == 0, report_result.stdout

    project_dir = project_mod.project_dir(library_root, project.project_id)
    pdf_path = project_dir / "report.pdf"
    csv_path = project_dir / "report.csv"
    json_path = project_dir / "report.json"
    assert pdf_path.exists() and pdf_path.stat().st_size > 1000
    assert pdf_path.read_bytes().startswith(b"%PDF")
    assert csv_path.exists()

    csv_text = csv_path.read_text(encoding="utf-8")
    report_data = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(report_data["stamps"]) == 2
    for stamp in report_data["stamps"]:
        assert stamp["stamp_id"] in csv_text
        # the big embedded images are stripped from the JSON export
        assert "crop_image" not in stamp


def test_run_command_chains_detect_perf_colour_report(tmp_path):
    from stampan.core import project as project_mod

    library_root = tmp_path / "library"
    ref_result = _build_test_reference("run-test reference")
    ref_mod.save_reference(ref_result, library_root)
    ref_id = ref_result.reference.ref_id

    placements = [
        StampPlacement(
            spec=SynthSpec(
                width_mm=20,
                height_mm=25,
                px_per_mm=10,
                sides={s: SideSpec(gauge=12.0) for s in ("top", "bottom", "left", "right")},
                seed=0,
            ),
            centre_mm=(25, 30),
        )
    ]
    sheet = SheetSpec(width_mm=60, height_mm=60, px_per_mm=10, seed=1)
    sample_img, _ = generate_synthetic_sheet(sheet, placements)
    sample_img_rgb = np.stack([sample_img] * 3, axis=-1)
    sample_path = tmp_path / "sample_sheet.tif"
    write_tiff(sample_path, sample_img_rgb.astype(np.uint16))

    result = runner.invoke(
        app,
        [
            "run",
            str(sample_path),
            "--reference",
            ref_id,
            "--project",
            "run cli test",
            "--library-root",
            str(library_root),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "detected 1 stamp(s)" in result.stdout
    assert "measured perforation on 1/1" in result.stdout
    assert "measured colour on 1/1" in result.stdout

    project = project_mod.list_projects(library_root)[0]
    report_path = project_mod.project_dir(library_root, project.project_id) / "report.pdf"
    assert report_path.exists()
    assert report_path.read_bytes().startswith(b"%PDF")


def test_scan_command_upscales_8bit_scanner_output_to_16bit(tmp_path, monkeypatch):
    # Regression test: the Epson 1200U (real hardware, Phase 1) only offers
    # 8-bit depth. Before this fix, `stampan scan` would crash in
    # write_tiff (uint8 != uint16) or -- worse, if that check were ever
    # loosened -- silently save values still numerically in 0-255 typed as
    # uint16, outside the domain any reference's colour model was trained
    # on. Found exactly that way on a real sample sheet.
    import subprocess

    import stampan.core.scanner as scanner_mod

    monkeypatch.setenv("FAKE_SCANNER_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("FAKE_SCANNER_DEPTH_INACTIVE", "1")  # backend stuck at 8-bit

    def fake_run(args):
        return subprocess.run(
            [*FAKE_SCANIMAGE, *args[1:]], capture_output=True, text=True, check=False
        )

    monkeypatch.setattr(scanner_mod, "_run", fake_run)

    options_path = tmp_path / "options.json"
    # "depth" deliberately omitted: apply_and_verify would pass it straight
    # through to scanimage, and this backend reports --depth inactive (would
    # abort with "attempted to set inactive option"). acquire_scan's own
    # depth-handling (the thing under test) checks for that and skips it.
    options_path.write_text(json.dumps({"resolution": 1200, "mode": "Color"}))
    output_path = tmp_path / "scan.tif"

    result = runner.invoke(
        app,
        ["scan", "-o", str(output_path), "--options-from", str(options_path)],
    )
    assert result.exit_code == 0, result.stdout
    assert "upscaling to 16-bit" in result.stdout or "upscaling to 16-bit" in (result.stderr or "")

    image, meta = read_tiff(output_path)
    assert image.dtype == np.uint16
    # the fake backend writes all-zero pixels; the real assertion that
    # matters is that write_tiff didn't crash and the upscale flag is set.
    assert meta.extra.get("upscaled_from_8bit") is True
