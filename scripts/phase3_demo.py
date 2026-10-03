"""Phase 3 demo/validation: synthetic gauge sweep (SPEC §6.4) plus real-stamp
diagnostics if STAMPAN_TESTDATA has a usable project. Writes an error table,
error plot, and diagnostic images to out/phase3/.

Run with: uv run python scripts/phase3_demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from stampan.core.perforation import analyse_perforation  # noqa: E402

OUT_DIR = ROOT / "out" / "phase3"


def run_sweep(noise_sigma: float, blur_sigma_px: float, roughness_mm: float, label: str) -> dict:
    sys.path.insert(0, str(ROOT / "tests"))
    from synth import SideSpec, SynthSpec, generate_synthetic_stamp

    gauges = np.arange(8.0, 16.01, 0.25)
    rotations = (-3.0, 0.0, 3.0)
    rows = []

    for gauge in gauges:
        for rot in rotations:
            spec = SynthSpec(
                width_mm=20,
                height_mm=25,
                px_per_mm=20.0,
                rotation_deg=rot,
                noise_sigma=noise_sigma,
                blur_sigma_px=blur_sigma_px,
                sides={
                    s: SideSpec(gauge=float(gauge), edge_roughness_mm=roughness_mm)
                    for s in ("top", "bottom", "left", "right")
                },
                seed=int(gauge * 100) + int(rot),
            )
            img, _gt = generate_synthetic_stamp(spec)
            analysis = analyse_perforation(img, 20.0, 20.0)
            for side_name, side in analysis.sides.items():
                if side.gauge is None:
                    continue
                rows.append(
                    {
                        "gauge_true": float(gauge),
                        "rotation_deg": rot,
                        "side": side_name,
                        "gauge_measured": side.gauge,
                        "error": side.gauge - float(gauge),
                        "hole_count": side.hole_count,
                        "confidence": side.confidence,
                    }
                )

    eligible = [r for r in rows if r["hole_count"] >= 10]
    errors = np.array([r["error"] for r in eligible])
    fails = [r for r in eligible if abs(r["error"]) >= 0.05]

    summary = {
        "label": label,
        "noise_sigma": noise_sigma,
        "blur_sigma_px": blur_sigma_px,
        "roughness_mm": roughness_mm,
        "n_eligible": len(eligible),
        "n_fail": len(fails),
        "fail_rate": len(fails) / len(eligible) if eligible else 0.0,
        "mean_error": float(errors.mean()) if len(errors) else None,
        "std_error": float(errors.std()) if len(errors) else None,
        "max_abs_error": float(np.max(np.abs(errors))) if len(errors) else None,
        "fails": fails,
    }

    fig, ax = plt.subplots(figsize=(8, 4))
    for rot in rotations:
        sub = [r for r in eligible if r["rotation_deg"] == rot]
        ax.scatter(
            [r["gauge_true"] for r in sub],
            [r["error"] for r in sub],
            s=8,
            label=f"rot={rot}",
            alpha=0.6,
        )
    ax.axhline(0.05, color="red", lw=0.5, ls="--")
    ax.axhline(-0.05, color="red", lw=0.5, ls="--")
    ax.set_xlabel("true gauge")
    ax.set_ylabel("measured - true gauge")
    ax.set_title(f"{label}: noise={noise_sigma}, blur={blur_sigma_px}, roughness={roughness_mm}mm")
    ax.legend(fontsize=7)
    fig.tight_layout()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_DIR / f"gauge_sweep_{label}.png", dpi=130)
    plt.close(fig)

    return summary


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    scenarios = [
        ("clean", 0.0, 0.0, 0.0),
        ("realistic", 80.0, 0.5, 0.015),
        ("stress", 200.0, 0.8, 0.03),
    ]
    summaries = []
    for label, noise, blur, rough in scenarios:
        print(f"running sweep: {label} (noise={noise}, blur={blur}, roughness={rough}mm)")
        summary = run_sweep(noise, blur, rough, label)
        summaries.append(summary)
        print(
            f"  n={summary['n_eligible']} fails={summary['n_fail']} "
            f"({summary['fail_rate'] * 100:.1f}%) mean={summary['mean_error']:.4f} "
            f"std={summary['std_error']:.4f} max|err|={summary['max_abs_error']:.4f}"
        )
        for f in summary["fails"][:10]:
            print(
                f"    FAIL gauge={f['gauge_true']} rot={f['rotation_deg']} side={f['side']} "
                f"measured={f['gauge_measured']:.3f} err={f['error']:+.3f} "
                f"n_holes={f['hole_count']}"
            )

    (OUT_DIR / "sweep_summary.json").write_text(json.dumps(summaries, indent=2))
    print(f"\nwrote plots and sweep_summary.json to {OUT_DIR}")


if __name__ == "__main__":
    main()
