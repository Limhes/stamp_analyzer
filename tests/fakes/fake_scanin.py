#!/usr/bin/env python3
"""A fake `scanin` for testing core.calibration.scanin without ArgyllCMS
or a real, recognisable scan.

Ignores the input TIFF's actual pixel content entirely (real chart
recognition needs ArgyllCMS itself, not worth faking) and instead writes a
`.ti3` whose RGB values are a known, fixed synthetic "camera" transform of
the real CGATS reference file's own XYZ values -- the same approach (and
the same matrix) `tests/test_reference.py` used before `scanin` existed,
so the reference-building pipeline's own math (linearisation, model
fitting, persistence) is still exercised against a known-recoverable
relationship.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from stampan.core.cgats import parse_cgats_file  # noqa: E402

# Same fixed synthetic "camera" matrix used in test_characterisation.py.
_XYZ_TO_RGB = np.array(
    [
        [2.5, -1.2, -0.3],
        [-0.9, 1.9, 0.0],
        [0.05, -0.2, 1.1],
    ]
)


def main() -> int:
    args = sys.argv[1:]
    positionals = [a for a in args if not a.startswith("-")]
    if len(positionals) < 3:
        print("fake_scanin: expected <scan.tif> <chart.cht> <reference.txt>", file=sys.stderr)
        return 1
    scan_tif, _cht, reference_txt = positionals[0], positionals[1], positionals[2]
    diag_tif = positionals[3] if len(positionals) > 3 else None

    gamma = float(os.environ.get("FAKE_SCANIN_GAMMA", "1.0"))

    table = parse_cgats_file(reference_txt)
    by_id = table.by_sample_id()

    rows = []
    for raw_id, row in by_id.items():
        xyz = np.array([row["XYZ_X"], row["XYZ_Y"], row["XYZ_Z"]])
        rgb_linear = np.clip((xyz @ _XYZ_TO_RGB.T) / 100.0, 0, 1)
        rgb_pct = (rgb_linear**gamma) * 100.0
        rows.append((raw_id, xyz, rgb_pct))

    ti3_path = Path(scan_tif).with_suffix(".ti3")
    lines = [
        "CTI3   ",
        "",
        'DESCRIPTOR "fake scanin output for testing"',
        'ORIGINATOR "fake_scanin.py"',
        'DEVICE_CLASS "INPUT"',
        'COLOR_REP "XYZ_RGB"',
        "",
        f"NUMBER_OF_FIELDS {10}",
        "BEGIN_DATA_FORMAT",
        "SAMPLE_ID XYZ_X XYZ_Y XYZ_Z RGB_R RGB_G RGB_B STDEV_R STDEV_G STDEV_B",
        "END_DATA_FORMAT",
        "",
        f"NUMBER_OF_SETS {len(rows)}",
        "BEGIN_DATA",
    ]
    for raw_id, xyz, rgb_pct in rows:
        lines.append(
            f"{raw_id} {xyz[0]:.6f} {xyz[1]:.6f} {xyz[2]:.6f} "
            f"{rgb_pct[0]:.6f} {rgb_pct[1]:.6f} {rgb_pct[2]:.6f} 0.01 0.01 0.01"
        )
    lines.append("END_DATA")
    ti3_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if diag_tif:
        import tifffile

        tifffile.imwrite(diag_tif, np.zeros((4, 4, 3), dtype=np.uint8))

    return 0


if __name__ == "__main__":
    sys.exit(main())
