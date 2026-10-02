"""One-off import of René's real reference/sample scans into testdata/.

The scans we have are 8-bit sRGB JPEGs at an unknown true resolution (the
EXIF tag is 300/600 dpi, a plausible round scanner preset, but per René the
settings weren't recorded and can't be fully trusted). Per his instruction,
each is upsampled by an integer factor to land on 1200 dpi (the Epson
1200U's working resolution, SPEC §2), so the rest of the pipeline -- tuned
assuming roughly that pixel scale -- sees comparable images. This is a
one-time data-prep step, not something core/io.py should do silently: it
is lossy-source, gamma/compression artifacts and all, and is recorded as
such in the TIFF's embedded metadata (ScanMetadata.extra) rather than
pretended away.

Run with: uv run python scripts/import_real_testdata.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from stampan.core.io import ScanMetadata, write_tiff  # noqa: E402

TESTDATA = ROOT / "testdata"
TARGET_DPI = 1200


def _integer_upscale_factor(reported_dpi: float, target_dpi: int = TARGET_DPI) -> int:
    factor = target_dpi / reported_dpi
    rounded = round(factor)
    if rounded < 1 or abs(factor - rounded) > 0.05:
        raise ValueError(
            f"reported dpi {reported_dpi} does not cleanly scale to {target_dpi} by an "
            f"integer factor (got {factor:.3f}); resample manually and re-check."
        )
    return rounded


def import_jpeg_as_tiff(src_path: Path, dst_path: Path, notes: str) -> None:
    im = Image.open(src_path)
    dpi_x, dpi_y = im.info.get("dpi", (None, None))
    if not dpi_x or not dpi_y:
        raise ValueError(f"{src_path} has no DPI tag; can't compute an upscale factor")
    if abs(dpi_x - dpi_y) > 1e-6:
        raise ValueError(f"{src_path} has non-square DPI ({dpi_x} x {dpi_y}); not handled")

    factor = _integer_upscale_factor(dpi_x)
    print(f"{src_path.name}: reported {dpi_x} dpi -> x{factor} -> {dpi_x * factor} dpi")

    rgb8 = np.array(im.convert("RGB"))
    if factor != 1:
        new_size = (rgb8.shape[1] * factor, rgb8.shape[0] * factor)
        # Nearest-neighbour: this is a scale normalisation, not a resolution
        # enhancement -- interpolating would invent detail the JPEG doesn't
        # have, and would also re-blur/re-smooth the already-lossy source.
        rgb8 = np.array(im.convert("RGB").resize(new_size, Image.NEAREST))

    # 8-bit -> 16-bit: scale, don't pretend we have 16 bits of real dynamic
    # range. Also NOT linearised or de-gammaed -- SPEC's 16-bit linear
    # scanner RGB assumption does not hold for this source; characterisation
    # results from it are approximate at best (see the phase report).
    rgb16 = (rgb8.astype(np.uint32) * 257).astype(np.uint16)

    metadata = ScanMetadata(
        scan_time=None,
        scanner_options={},
        ref_id=None,
        extra={
            "source": str(src_path),
            "source_format": "JPEG (8-bit sRGB, lossy)",
            "source_reported_dpi": dpi_x,
            "upscale_factor": factor,
            "upscale_method": "nearest-neighbour (scale normalisation only)",
            "caveats": (
                "Not a 16-bit linear scanner capture: JPEG compression artefacts, "
                "sRGB gamma not reversed, true capture settings unknown. "
                "See docs: Phase 1/2 real-data report."
            ),
            "notes": notes,
        },
    )
    write_tiff(dst_path, rgb16, metadata)
    print(f"  wrote {dst_path} ({rgb16.shape[1]}x{rgb16.shape[0]}, 16-bit)")


def main() -> None:
    import_jpeg_as_tiff(
        TESTDATA / "reference" / "reference_01.jpg",
        TESTDATA / "reference" / "reference_01.tif",
        notes="Wolf Faust IT8.7/2 R240103 scan; no ruler present in this scan.",
    )
    import_jpeg_as_tiff(
        TESTDATA / "samples" / "sheet_01.jpg",
        TESTDATA / "samples" / "sheet_01.tif",
        notes="Stock-card sample sheet scan.",
    )


if __name__ == "__main__":
    main()
