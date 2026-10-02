"""Phase 1 demo/validation: run calibration (geometry + IT8 + characterisation)
over STAMPAN_TESTDATA if set, else over synthetic data, and write diagnostics
plus a summary to out/phase1/.

Run with: uv run python scripts/phase1_demo.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from stampan.core.calibration import reference as ref_mod  # noqa: E402
from stampan.core.io import ScanMetadata  # noqa: E402

OUT_DIR = ROOT / "out" / "phase1"
CGATS_PATH = ROOT / "reference_data" / "R240103.txt"


def _run_on_real_testdata(testdata_root: Path) -> bool:
    ref_scan_path = testdata_root / "reference" / "reference_01.tif"
    if not ref_scan_path.exists():
        return False
    print(f"NOTE: real reference scan found at {ref_scan_path}, but phase1_demo.py does not")
    print("yet know this stock's ruler region / IT8 corner pixel coordinates (there is no")
    print("GUI for picking them yet, SPEC §4.2/§4.3). Falling back to synthetic data for now.")
    print("Re-run this script with real coordinates once they're known, or build a Reference")
    print("directly with `stampan reference create`.")
    return False


def _run_on_synthetic_data() -> None:
    print("=" * 78)
    print("No usable real reference scan in STAMPAN_TESTDATA -- using SYNTHETIC data.")
    print("All numbers below are from a synthetic scan built by applying a fixed,")
    print("fictitious 3x3 XYZ->RGB 'camera' matrix to the real IT8 patch XYZ values.")
    print("They validate the calibration PIPELINE (geometry fit, IT8 sampling, model")
    print("selection, persistence), not a real scanner's characteristics.")
    print("=" * 78)

    sys.path.insert(0, str(ROOT / "tests"))
    from test_reference import _build_synthetic_reference_scan

    scan, corners, x_axis, y_axis = _build_synthetic_reference_scan()
    result = ref_mod.build_reference(
        name="phase1 synthetic demo",
        scan=scan,
        scan_metadata=ScanMetadata(
            scanner_options={"resolution": 1200, "mode": "Color", "depth": 16}
        ),
        x_axis=x_axis,
        it8_corners=corners,
        cgats_reference_path=CGATS_PATH,
        y_axis=y_axis,
        notes="synthetic validation run (scripts/phase1_demo.py)",
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, data in result.diagnostics.items():
        (OUT_DIR / filename).write_bytes(data)

    ref = result.reference
    summary = {
        "ref_id": ref.ref_id,
        "scale_px_per_mm_x": ref.scale.px_per_mm_x,
        "scale_px_per_mm_y": ref.scale.px_per_mm_y,
        "scale_uncertainty_x": ref.scale.uncertainty_x,
        "scale_uncertainty_y": ref.scale.uncertainty_y,
        "chosen_colour_model": ref.colour_model.kind,
        "train_delta_e00": ref.colour_model.train_delta_e00,
        "cv_delta_e00": ref.colour_model.cv_delta_e00,
        "notes": ref.notes,
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))

    model_comparison = json.loads(result.diagnostics["model_comparison.json"])

    print(f"\nx scale: {ref.scale.px_per_mm_x:.4f} +/- {ref.scale.uncertainty_x:.4f} px/mm")
    print(f"y scale: {ref.scale.px_per_mm_y:.4f} +/- {ref.scale.uncertainty_y:.4f} px/mm")
    print("\ncolour model comparison (leave-one-out cross-validated dE00):")
    for kind, stats in model_comparison.items():
        print(
            f"  {kind:14s} mean={stats['mean']:.4f}  median={stats['median']:.4f}  "
            f"p95={stats['p95']:.4f}  max={stats['max']:.4f}"
        )
    print(f"\nchosen model: {ref.colour_model.kind}")
    print(f"\nwrote diagnostics and summary.json to {OUT_DIR}")


def main() -> None:
    testdata_env = os.environ.get("STAMPAN_TESTDATA")
    handled = False
    if testdata_env:
        handled = _run_on_real_testdata(Path(testdata_env))
    if not handled:
        _run_on_synthetic_data()


if __name__ == "__main__":
    main()
