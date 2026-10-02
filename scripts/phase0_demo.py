"""Phase 0 demo: render a few synthetic stamps to out/phase0/ for review.

Run with: uv run python scripts/phase0_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from tests.synth import SideSpec, SynthSpec, generate_synthetic_stamp  # noqa: E402

OUT_DIR = ROOT / "out" / "phase0"


def _save_png(path: Path, img16: np.ndarray) -> None:
    img8 = (img16.astype(np.float64) / 65535.0 * 255.0).astype(np.uint8)
    cv2.imwrite(str(path), img8)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    specs = {
        "clean_gauge12": SynthSpec(
            seed=0,
            sides={s: SideSpec(gauge=12.0) for s in ("top", "bottom", "left", "right")},
        ),
        "compound_gauge_rotated": SynthSpec(
            seed=1,
            rotation_deg=2.5,
            sides={
                "top": SideSpec(gauge=11.5),
                "bottom": SideSpec(gauge=11.5),
                "left": SideSpec(gauge=12.25),
                "right": SideSpec(gauge=12.25),
            },
        ),
        "noisy_blurred": SynthSpec(
            seed=2,
            noise_sigma=600.0,
            blur_sigma_px=1.2,
            sides={
                s: SideSpec(gauge=13.0, edge_roughness_mm=0.04)
                for s in ("top", "bottom", "left", "right")
            },
        ),
        "imperf_and_straight_edge": SynthSpec(
            seed=3,
            sides={
                "top": SideSpec(imperf=True),
                "bottom": SideSpec(gauge=12.0),
                "left": SideSpec(gauge=12.0),
                "right": SideSpec(straight_edge=True),
            },
        ),
        "glassine_strip_edge": SynthSpec(
            seed=4,
            strip_edge=True,
            strip_edge_y_frac=0.35,
            rotation_deg=-1.8,
            sides={s: SideSpec(gauge=14.0) for s in ("top", "bottom", "left", "right")},
        ),
    }

    print(f"writing {len(specs)} example images to {OUT_DIR}")
    for name, spec in specs.items():
        img, gt = generate_synthetic_stamp(spec)
        _save_png(OUT_DIR / f"{name}.png", img)

        def _describe(g):
            if g.imperf:
                return "imperf"
            if g.straight_edge:
                return "straight"
            return f"{g.gauge:.2f}g/{len(g.centres_px)}holes"

        hole_summary = ", ".join(f"{side}={_describe(g)}" for side, g in gt.holes.items())
        print(f"  {name}: scene={gt.scene_size_px}, rot={gt.rotation_deg}deg, {hole_summary}")

    print("done.")


if __name__ == "__main__":
    main()
