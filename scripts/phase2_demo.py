"""Phase 2 demo/validation: run core.detection over STAMPAN_TESTDATA if set
(against ground_truth.yaml), else over a synthetic sample sheet, and write
diagnostics plus a summary to out/phase2/.

Run with: uv run python scripts/phase2_demo.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from stampan.core.detection import (  # noqa: E402
    detect_stamps,
    extract_crop,
    render_contact_sheet,
    render_overview,
)

OUT_DIR = ROOT / "out" / "phase2"


def _run_on_real_testdata(testdata_root: Path) -> bool:
    samples_dir = testdata_root / "samples"
    gt_path = testdata_root / "ground_truth.yaml"
    sample_files = list(samples_dir.glob("*.tif")) if samples_dir.exists() else []
    if not sample_files:
        print(f"NOTE: no sample sheet TIFFs found under {samples_dir}.")
        if gt_path.exists():
            print(f"({gt_path} exists but references scans that aren't in testdata/ yet.)")
        print("Falling back to synthetic data.")
        return False

    print("NOTE: real sample sheets found, but phase2_demo.py doesn't yet know this stock's")
    print("reference id / px-per-mm scale (no GUI to build a Reference yet, and no ground")
    print("truth with a usable scale). Falling back to synthetic data for now. Once a real")
    print("Reference exists (via `stampan reference create`), re-run detection with")
    print("`stampan detect <sample.tif> --reference <ref_id> --project <name>`.")
    return False


def _run_on_synthetic_data() -> None:
    print("=" * 78)
    print("No usable real sample sheet in STAMPAN_TESTDATA -- using SYNTHETIC data.")
    print("Numbers below validate the detection PIPELINE (thresholding, strip-edge")
    print("suppression, perforation-gap closing, watershed splitting, size filtering),")
    print("not performance on a real scan.")
    print("=" * 78)

    sys.path.insert(0, str(ROOT / "tests"))
    from synth import SideSpec, SynthSpec
    from synth_sheet import SheetSpec, StampPlacement, generate_synthetic_sheet

    ppm = 10.0
    rng_seed = 0

    # A representative sheet: a regular grid, one rotated stamp, one pair
    # touching in a staircase overlap, a glassine strip-edge line crossing
    # several stamps, and a couple of imperf/straight-edge stamps.
    placements = []
    expected_count = 0

    for i in range(3):
        for j in range(2):
            spec = SynthSpec(
                width_mm=20,
                height_mm=25,
                px_per_mm=ppm,
                rotation_deg=0.0,
                noise_sigma=300.0,
                blur_sigma_px=0.8,
                sides={
                    s: SideSpec(gauge=12.0, edge_roughness_mm=0.02)
                    for s in ("top", "bottom", "left", "right")
                },
                seed=rng_seed + i * 10 + j,
            )
            placements.append(StampPlacement(spec=spec, centre_mm=(25 + i * 40, 25 + j * 40)))
            expected_count += 1

    rotated_spec = SynthSpec(
        width_mm=20,
        height_mm=25,
        px_per_mm=ppm,
        rotation_deg=5.0,
        sides={s: SideSpec(gauge=12.0) for s in ("top", "bottom", "left", "right")},
        seed=99,
    )
    placements.append(StampPlacement(spec=rotated_spec, centre_mm=(170, 25)))
    expected_count += 1

    touching_a = SynthSpec(
        width_mm=20,
        height_mm=25,
        px_per_mm=ppm,
        sides={s: SideSpec(gauge=12.0) for s in ("top", "bottom", "left", "right")},
        seed=201,
    )
    touching_b = SynthSpec(
        width_mm=20,
        height_mm=25,
        px_per_mm=ppm,
        sides={s: SideSpec(gauge=12.0) for s in ("top", "bottom", "left", "right")},
        seed=202,
    )
    placements.append(StampPlacement(spec=touching_a, centre_mm=(170, 90)))
    placements.append(StampPlacement(spec=touching_b, centre_mm=(189, 100)))
    expected_count += 2

    imperf_spec = SynthSpec(
        width_mm=20,
        height_mm=25,
        px_per_mm=ppm,
        sides={
            "top": SideSpec(imperf=True),
            "bottom": SideSpec(gauge=12.0),
            "left": SideSpec(gauge=12.0),
            "right": SideSpec(straight_edge=True),
        },
        seed=301,
    )
    placements.append(StampPlacement(spec=imperf_spec, centre_mm=(25, 140)))
    expected_count += 1

    sheet = SheetSpec(
        width_mm=220,
        height_mm=170,
        px_per_mm=ppm,
        seed=1,
        strip_edge_y_fracs=[0.3, 0.75],
        noise_sigma=300.0,
        blur_sigma_px=0.8,
    )
    img, gt = generate_synthetic_sheet(sheet, placements)

    result = detect_stamps(img, px_per_mm_x=ppm, px_per_mm_y=ppm)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "sample_overview.png").write_bytes(render_overview(img, result))

    crops = [extract_crop(img, s, ppm, ppm, margin_mm=3.0)[0] for s in result.stamps]
    (OUT_DIR / "contact_sheet.png").write_bytes(render_contact_sheet(crops))

    flagged = [s for s in result.stamps if s.flagged_for_manual_review]
    summary = {
        "expected_placed": expected_count,
        "ground_truth_placed": len(gt.stamps),
        "detected": len(result.stamps),
        "flagged_for_manual_review": len(flagged),
        "detection_warnings": result.warnings,
        "stamps": [
            {
                "index": s.index,
                "centre_px": s.centre_px,
                "size_px": s.size_px,
                "size_mm": (s.size_px[0] / ppm, s.size_px[1] / ppm),
                "angle_deg": s.angle_deg,
                "fill_ratio": s.fill_ratio,
                "confidence": s.confidence,
                "warnings": s.warnings,
            }
            for s in result.stamps
        ],
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))

    print(f"\nplaced (ground truth): {len(gt.stamps)}")
    print(f"detected:               {len(result.stamps)}")
    print(f"flagged for review:     {len(flagged)}")
    for s in result.stamps:
        tag = " [FLAGGED]" if s.flagged_for_manual_review else ""
        print(
            f"  #{s.index}: {s.size_px[0] / ppm:.1f}x{s.size_px[1] / ppm:.1f}mm, "
            f"angle={s.angle_deg:.2f}, fill={s.fill_ratio:.2f}, conf={s.confidence:.2f}"
            f"{tag} {s.warnings}"
        )
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
