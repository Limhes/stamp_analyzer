#!/usr/bin/env python3
"""A fake `scanimage` for testing core.scanner without real hardware.

State (current option values) persists in a JSON file given by the
FAKE_SCANNER_STATE env var, so a test can apply options in one subprocess
call and verify them in the next, like the real tool.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

STATE_PATH = Path(os.environ["FAKE_SCANNER_STATE"])

DEFAULTS = {
    "--resolution": "300",
    "--mode": "Color",
    "--depth": "8",
    "--gamma-correction": "Auto",
    "--sharpen": "0",
    "-l": "0",
    "-t": "0",
}


def _load_state() -> dict[str, str]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return dict(DEFAULTS)


def _save_state(state: dict[str, str]) -> None:
    STATE_PATH.write_text(json.dumps(state))


def _write_minimal_tiff(path: str, depth: int) -> None:
    # Smallest possible valid grayscale TIFF via tifffile, matching the
    # requested bit depth so ScanAcquireResult can verify it.
    import numpy as np
    import tifffile

    dtype = np.uint16 if depth == 16 else np.uint8
    img = np.zeros((4, 4), dtype=dtype)
    tifffile.imwrite(path, img)


def main() -> int:
    args = sys.argv[1:]
    state = _load_state()

    if "-L" in args:
        print("device `fake:epson2:libusb:001:005' is a Epson Perfection 1200U flatbed scanner")
        return 0

    depth_inactive = os.environ.get("FAKE_SCANNER_DEPTH_INACTIVE") == "1"

    if "-A" in args:
        depth_line = (
            "    --depth 8bit [inactive]"
            if depth_inactive
            else f"    --depth 8|16 [{state['--depth']}]"
        )
        lines = [
            "",
            "All options specific to device `fake:epson2:libusb:001:005':",
            f"    --resolution 50..1200dpi (in steps of 1) [{state['--resolution']}]",
            "        Sets the resolution of the scanned image.",
            f"    --mode Color|Gray|Lineart [{state['--mode']}]",
            "        Selects the scan mode.",
            depth_line,
            "        Number of bits per sample.",
            f"    --gamma-correction Auto|None|1.0 [{state['--gamma-correction']}]",
            "        Selects the gamma correction mode.",
            f"    --sharpen 0..4 [{state['--sharpen']}]",
            "        Sharpening level.",
            f"    -l 0..216.069mm [{state['-l']}]",
            "        Top-left x position of scan area.",
            f"    -t 0..297mm [{state['-t']}]",
            "        Top-left y position of scan area.",
        ]
        print("\n".join(lines))
        return 0

    # Parse --option=value and -x value style args, applying to state.
    output_path = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-o",):
            output_path = args[i + 1]
            i += 2
            continue
        if a.startswith("--") and "=" in a:
            name, value = a.split("=", 1)
            if name == "--depth" and depth_inactive:
                print(
                    f"scanimage: attempted to set inactive option {name.lstrip('-')}",
                    file=sys.stderr,
                )
                return 1
            state[name] = value
            i += 1
            continue
        if a in ("-d", "-n", "-p", "--format"):
            if a == "-d":
                i += 2
            elif a == "--format":
                i += 1
            else:
                i += 1
            continue
        if a.startswith("--format="):
            i += 1
            continue
        i += 1

    _save_state(state)

    if output_path:
        _write_minimal_tiff(output_path, int(state.get("--depth", "8")))

    return 0


if __name__ == "__main__":
    sys.exit(main())
