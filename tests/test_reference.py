from pathlib import Path

import numpy as np
import pytest

from stampan.core.calibration import reference as ref_mod
from stampan.core.calibration import scanin as scanin_mod
from stampan.core.calibration.it8 import PatchSample
from stampan.core.calibration.reference import (
    build_reference,
    list_references,
    load_colour_model,
    load_reference,
    save_reference,
)
from stampan.core.cgats import lab_from_xyz_d50, parse_cgats_file
from stampan.core.io import ScanMetadata, write_tiff

REF_PATH = Path(__file__).parent.parent / "reference_data" / "R240103.txt"
FAKE_SCANIN = str(Path(__file__).parent / "fakes" / "fake_scanin.py")

# `scanin` locates patches from the scan's own printed fiducial marks, which
# a synthetic test image has none of -- tests use fake_scanin.py (a stand-in
# `scanin` binary, same pattern as fake_scanimage.py for core.scanner) to
# exercise the rest of build_reference's pipeline (linearisation, model
# fitting, persistence) against a known, recoverable RGB<->XYZ relationship
# instead. The scan image's actual pixel content is irrelevant here -- the
# fake ignores it -- so any small placeholder TIFF will do.


def _dummy_scan(tmp_path: Path) -> tuple[np.ndarray, Path]:
    scan = np.full((8, 8, 3), 30000, dtype=np.uint16)
    path = tmp_path / "dummy_scan.tif"
    write_tiff(path, scan)
    return scan, path


def _build(tmp_path, monkeypatch, gamma: float = 1.0, **kwargs):
    monkeypatch.setenv("FAKE_SCANIN_GAMMA", str(gamma))
    scan, _ = _dummy_scan(tmp_path)
    return build_reference(
        scan=scan,
        scan_metadata=ScanMetadata(scanner_options={"resolution": 1200, "mode": "Color"}),
        cgats_reference_path=REF_PATH,
        dpi_x=1200.0,
        dpi_y=1200.0,
        dpi_source="test fixture",
        scanin_cmd=FAKE_SCANIN,
        cht_path=REF_PATH,  # unused by the fake, just needs to exist
        **kwargs,
    )


def test_build_reference_recovers_low_cv_delta_e00(tmp_path, monkeypatch):
    result = _build(tmp_path, monkeypatch, name="synthetic test reference")
    ref = result.reference
    assert ref.scale.px_per_mm_x == pytest.approx(1200.0 / 25.4)
    assert ref.colour_model.kind in ("matrix3x3", "root_poly2", "root_poly3")
    assert ref.colour_model.cv_delta_e00["mean"] < 1.0
    assert set(result.diagnostics) >= {"it8_std_heatmap.png", "colour_model_error_heatmap.png"}


def test_save_and_load_reference_roundtrip(tmp_path, monkeypatch):
    result = _build(tmp_path, monkeypatch, name="roundtrip test")
    ref_dir = save_reference(result, tmp_path)
    assert (ref_dir / "reference.json").exists()
    assert (ref_dir / "reference_scan.tif").exists()
    assert (ref_dir / "model.npz").exists()

    loaded = load_reference(tmp_path, result.reference.ref_id)
    assert loaded == result.reference

    refs = list_references(tmp_path)
    assert len(refs) == 1
    assert refs[0].ref_id == result.reference.ref_id

    model = load_colour_model(tmp_path, result.reference.ref_id)
    rgb_sample = np.array([[2000.0, 1500.0, 1000.0]])
    lab = model.predict_lab(rgb_sample)
    assert lab.shape == (1, 3)
    assert np.all(np.isfinite(lab))


def test_load_colour_model_applies_grey_ramp_linearisation_to_new_pixels(tmp_path, monkeypatch):
    # Regression test: the grey-ramp linearisation curve fit in build_reference
    # (SPEC §4.3) was discarded after training -- it was applied to the IT8
    # patches used to fit the characterisation model, but never persisted or
    # reapplied to any RGB predicted afterwards. Every real stamp crop (never
    # itself an IT8 patch) was therefore fed to the model still gamma-encoded,
    # landing outside the model's trained (linear) domain entirely. Found via
    # 100% of pixels on a real scan coming back "outside the trained gamut".
    result = _build(tmp_path, monkeypatch, gamma=2.2, name="gamma test reference")
    save_reference(result, tmp_path)
    model = load_colour_model(tmp_path, result.reference.ref_id)

    # Re-derive A1's raw (still gamma-encoded) RGB the same way the fake
    # scanin produced it, and check it still maps close to the known
    # ground-truth Lab once the persisted linearisation curve is applied.
    table = parse_cgats_file(REF_PATH)
    row = table.by_sample_id()["A1"]
    xyz_to_rgb = np.array([[2.5, -1.2, -0.3], [-0.9, 1.9, 0.0], [0.05, -0.2, 1.1]])
    xyz = np.array([row["XYZ_X"], row["XYZ_Y"], row["XYZ_Z"]])
    rgb_linear = np.clip((xyz @ xyz_to_rgb.T) / 100.0, 0, 1)
    raw_rgb = ((rgb_linear**2.2) * 65535.0)[None, :]

    expected_lab = np.array(lab_from_xyz_d50(row["XYZ_X"], row["XYZ_Y"], row["XYZ_Z"]))

    predicted_lab = model.predict_lab(raw_rgb)[0]
    assert np.all(np.isfinite(predicted_lab))
    delta_e = np.linalg.norm(predicted_lab - expected_lab)
    assert delta_e < 2.0, f"predicted {predicted_lab}, expected {expected_lab}"


def test_build_reference_refuses_scrambled_scanin_correspondence(tmp_path, monkeypatch):
    # Regression test: scanin occasionally picks the wrong one of several
    # candidate orientations for a chart (confirmed on a real scan -- the
    # grid still geometrically tiled every patch correctly, so it looked
    # right in the diagnostic at a glance, but each box was matched to the
    # wrong patch ID, e.g. the GS row ending up above row A instead of
    # below row L). That scrambles which sampled RGB gets compared to which
    # CGATS XYZ entry during fitting, producing a plausible-looking but
    # useless reference (ΔE00 mean ~27 on the real failure) rather than an
    # obvious crash. build_reference must refuse rather than silently save
    # a model trained on a scrambled correspondence.
    table = parse_cgats_file(REF_PATH)
    patch_ids = list(table.by_sample_id().keys())
    # Shuffle (fixed seed) so brightness order no longer tracks XYZ_Y order.
    rng = np.random.default_rng(0)
    scrambled_rgb = rng.permutation(len(patch_ids))
    samples = {
        pid: PatchSample(
            patch_id=pid,
            centre_px=(float("nan"), float("nan")),
            mean_rgb=(float(scrambled_rgb[i]), float(scrambled_rgb[i]), float(scrambled_rgb[i])),
            std_rgb=(1.0, 1.0, 1.0),
            n_pixels=0,
        )
        for i, pid in enumerate(patch_ids)
    }
    monkeypatch.setattr(
        ref_mod.scanin_mod,
        "locate_and_sample_patches",
        lambda *a, **kw: scanin_mod.ScaninResult(samples=samples, diagnostic_png=None),
    )

    scan, _ = _dummy_scan(tmp_path)
    with pytest.raises(scanin_mod.ScaninError, match="wrong"):
        build_reference(
            name="scrambled",
            scan=scan,
            scan_metadata=ScanMetadata(scanner_options={"resolution": 1200}),
            cgats_reference_path=REF_PATH,
            dpi_x=1200.0,
            dpi_y=1200.0,
            dpi_source="test fixture",
            scanin_cmd=FAKE_SCANIN,
            cht_path=REF_PATH,
        )
