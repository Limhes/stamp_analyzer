"""IT8 patch location via ArgyllCMS's `scanin` (SPEC §4.3), replacing the
old "click 4-6 corner points, bilinearly interpolate" approach.

Why: that approach needed pixel-precise manual clicks (small errors
compound across a 22x12 grid) and ambiguous point labels led to real
misclicks (row L read as row I in the UI font) -- both failure modes
`scanin` sidesteps entirely, since it locates the chart's own printed
fiducial marks and patch grid directly from the scan via edge/line
detection and feature matching, with no manual input at all. Confirmed
directly against a real scan: every one of the 288 patches lands exactly
on its printed square (`scanin`'s own `-dipon` diagnostic image).

Requires the system `argyll` + `argyll-ref` packages (``sudo apt install
argyll argyll-ref`` on Debian/Ubuntu) -- ArgyllCMS is AGPL-3 licensed and
is not vendored into this repo; this module only shells out to the
installed `scanin` binary and reads its chart file from argyll-ref's
install location, the same way `core.scanner` shells out to `scanimage`
rather than linking SANE.

Scale note: `scanin`'s `.ti3` output reports device RGB as 0-100 (CGATS
convention), normalised to the *scanned TIFF's own bit depth* -- not the
scanner's native bit depth. Every reference TIFF this app writes is
uint16 (see `core.io.write_tiff`, including the x257 upscale for 8-bit-only
backends), so 100 always corresponds to 65535 here. `locate_and_sample_patches`
converts back to that native 0-65535 scale immediately, so a fitted colour
model still operates on exactly the same scale as the rest of the
pipeline (stamp crops read straight from a scan) -- nothing downstream of
this module needs to know `scanin` was involved at all.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from stampan.core.calibration.it8 import PatchSample

#: Known install locations for argyll-ref's shipped chart files, checked
#: in order. Debian/Ubuntu's argyll-ref package installs here; other
#: distributions/manual installs may differ.
_CHT_SEARCH_PATHS = (
    Path("/usr/share/color/argyll/ref"),
    Path("/usr/local/share/color/argyll/ref"),
)

IT8_CHART_NAME = "it8.cht"

_TI3_ROW_RE = re.compile(
    r"^(?P<id>\S+)\s+"
    r"(?P<x>[-\d.eE]+)\s+(?P<y>[-\d.eE]+)\s+(?P<z>[-\d.eE]+)\s+"
    r"(?P<r>[-\d.eE]+)\s+(?P<g>[-\d.eE]+)\s+(?P<b>[-\d.eE]+)\s+"
    r"(?P<sr>[-\d.eE]+)\s+(?P<sg>[-\d.eE]+)\s+(?P<sb>[-\d.eE]+)\s*$"
)


class ScaninError(RuntimeError):
    pass


def find_it8_chart() -> Path:
    """Locate the standard IT8.7/2 chart file shipped by argyll-ref."""
    for base in _CHT_SEARCH_PATHS:
        candidate = base / IT8_CHART_NAME
        if candidate.exists():
            return candidate
    raise ScaninError(
        f"{IT8_CHART_NAME} not found in any of {[str(p) for p in _CHT_SEARCH_PATHS]} -- "
        "install argyll-ref (e.g. `sudo apt install argyll argyll-ref` on Debian/Ubuntu)"
    )


@dataclass
class ScaninResult:
    samples: dict[str, PatchSample]
    diagnostic_png: bytes | None  # scanin's own "-dipon" sample-box overlay


def _parse_ti3(text: str) -> dict[str, tuple[float, float, float, float, float, float]]:
    """Returns {patch_id: (X, Y, Z, R, G, B)} -- std dev parsed separately."""
    rows: dict[str, tuple[float, float, float, float, float, float]] = {}
    in_data = False
    for line in text.splitlines():
        if line.strip() == "BEGIN_DATA":
            in_data = True
            continue
        if line.strip() == "END_DATA":
            break
        if not in_data:
            continue
        m = _TI3_ROW_RE.match(line.strip())
        if not m:
            continue
        rows[m["id"]] = (
            float(m["x"]),
            float(m["y"]),
            float(m["z"]),
            float(m["r"]),
            float(m["g"]),
            float(m["b"]),
        )
    return rows


def _parse_ti3_std(text: str) -> dict[str, tuple[float, float, float]]:
    stds: dict[str, tuple[float, float, float]] = {}
    in_data = False
    for line in text.splitlines():
        if line.strip() == "BEGIN_DATA":
            in_data = True
            continue
        if line.strip() == "END_DATA":
            break
        if not in_data:
            continue
        m = _TI3_ROW_RE.match(line.strip())
        if not m:
            continue
        stds[m["id"]] = (float(m["sr"]), float(m["sg"]), float(m["sb"]))
    return stds


def _autocrop_to_content(image: np.ndarray, margin_px: int = 500) -> np.ndarray:
    """Crop a full-bed scan down to roughly the IT8 target plus a margin.

    `scanin`'s chart recognition is tuned for a scan already cropped close
    to the target (the ArgyllCMS workflow this app's own earlier
    hand-written howto followed did this by hand, via XSane) -- confirmed
    directly: on an uncropped full-bed scan (card occupying roughly a
    third of a much larger white-bed image) its rotation/scale fit
    diverged completely (xscale and yscale differing by 30x, confidence
    0.54); cropped to just the target plus a margin, the same scan fit
    cleanly (xscale == yscale to within 1%, confidence 1.02). Detection
    uses saturation, not brightness -- a raw, uncorrected scan (no
    auto-exposure, see `core.scanner`) is not necessarily bright in its
    background relative to the grey card stock, but the printed colour
    patches are reliably far more saturated than either.
    """
    import cv2

    disp = (image.astype(np.float32) / max(float(image.max()), 1.0) * 255.0).astype(np.uint8)
    hsv = cv2.cvtColor(disp, cv2.COLOR_RGB2HSV)
    mask = (hsv[..., 1] > 30).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((41, 41), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return image  # no saturated content found; let scanin try the whole image
    x, y, w, h = cv2.boundingRect(max(contours, key=cv2.contourArea))
    h_img, w_img = image.shape[:2]
    x0, y0 = max(0, x - margin_px), max(0, y - margin_px)
    x1, y1 = min(w_img, x + w + margin_px), min(h_img, y + h + margin_px)
    return image[y0:y1, x0:x1]


def _normalise_patch_id(raw_id: str) -> str:
    """scanin writes e.g. "A01"/"GS00"; the rest of this app uses "A1"/"GS0"
    (no leading zero on the number) -- normalise so patch IDs match the
    CGATS reference data's own `by_sample_id()` keys exactly."""
    m = re.match(r"^([A-Za-z]+)0*(\d+)$", raw_id)
    if not m:
        return raw_id
    return f"{m.group(1)}{int(m.group(2))}"


def _luma_xyz_correlation(
    samples: dict[str, PatchSample], cgats_reference_path: str | Path
) -> float:
    """How well sampled-RGB brightness tracks the CGATS XYZ_Y brightness --
    see `_run_scanin_once`'s docstring for why this is the check that
    catches a scrambled chart-orientation match."""
    from stampan.core.cgats import parse_cgats_file

    by_id = parse_cgats_file(cgats_reference_path).by_sample_id()
    luma, y = [], []
    for patch_id, sample in samples.items():
        if patch_id not in by_id:
            continue
        luma.append(sum(sample.mean_rgb) / 3.0)
        y.append(by_id[patch_id]["XYZ_Y"])
    if len(luma) < 8:
        return 0.0
    return float(np.corrcoef(luma, y)[0, 1])


def _run_scanin_once(
    image: np.ndarray,
    cgats_reference_path: str | Path,
    cht_path: Path,
    scanin_cmd: str,
    diagnostic: bool,
    native_max_value: float,
) -> ScaninResult:
    import tifffile

    cropped = _autocrop_to_content(image)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        scan_copy = tmp / "scan.tif"
        tifffile.imwrite(str(scan_copy), cropped)
        diag_path = tmp / "diag.tif"

        args = [scanin_cmd, "-v2"]
        if diagnostic:
            args += ["-dipon"]
        args += [str(scan_copy), str(cht_path), str(cgats_reference_path)]
        if diagnostic:
            args += [str(diag_path)]

        proc = subprocess.run(args, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise ScaninError(f"scanin failed: {proc.stderr.strip() or proc.stdout.strip()}")

        ti3_path = scan_copy.with_suffix(".ti3")
        if not ti3_path.exists():
            raise ScaninError(f"scanin did not produce a .ti3 file (stderr: {proc.stderr.strip()})")
        ti3_text = ti3_path.read_text(encoding="utf-8")

        diagnostic_png = None
        if diagnostic and diag_path.exists():
            diagnostic_png = _tiff_to_png(diag_path)

    rows = _parse_ti3(ti3_text)
    stds = _parse_ti3_std(ti3_text)
    scale = native_max_value / 100.0

    samples: dict[str, PatchSample] = {}
    for raw_id, (_x, _y, _z, r, g, b) in rows.items():
        patch_id = _normalise_patch_id(raw_id)
        sr, sg, sb = stds.get(raw_id, (0.0, 0.0, 0.0))
        samples[patch_id] = PatchSample(
            patch_id=patch_id,
            centre_px=(float("nan"), float("nan")),  # scanin doesn't report pixel coords
            mean_rgb=(r * scale, g * scale, b * scale),
            std_rgb=(sr * scale, sg * scale, sb * scale),
            n_pixels=0,  # not reported by scanin
        )
    return ScaninResult(samples=samples, diagnostic_png=diagnostic_png)


def locate_and_sample_patches(
    scan_path: str | Path,
    cgats_reference_path: str | Path,
    native_max_value: float = 65535.0,
    cht_path: str | Path | None = None,
    scanin_cmd: str = "scanin",
    diagnostic: bool = True,
) -> ScaninResult:
    """Run `scanin` to locate and sample every IT8 patch directly from the
    scan -- no manual corner marking. Returns RGB rescaled from scanin's
    0-100 (CGATS) convention back to this app's native 0-``native_max_value``
    scale (see module docstring).

    `scanin`'s geometric fit alone often can't distinguish a chart's true
    orientation from its 180-degree rotation -- an IT8 grid is nearly
    symmetric that way -- and it only cross-checks candidate orientations
    against the known patch colours when its *geometric* fit itself found
    more than one plausible candidate. When it finds only one (common),
    there is no colour-based safety net at all, and it can silently return
    every patch matched to the wrong neighbour (confirmed on a real scan:
    geometrically correct box placement, but each box's RGB compared
    against the wrong CGATS entry, ΔE00 mean ~27 instead of ~2). Since we
    always have the reference CGATS data in hand here, this does that
    cross-check ourselves: if sampled brightness doesn't correlate with
    the reference's own XYZ_Y, retry once with the scan rotated 180 degrees
    (forcing `scanin` into the other geometric candidate) and keep
    whichever attempt actually correlates.
    """
    cht_path = Path(cht_path) if cht_path is not None else find_it8_chart()
    if not cht_path.exists():
        raise ScaninError(f"chart file not found: {cht_path}")

    import tifffile

    image = tifffile.imread(str(scan_path))
    result = _run_scanin_once(
        image, cgats_reference_path, cht_path, scanin_cmd, diagnostic, native_max_value
    )
    corr = _luma_xyz_correlation(result.samples, cgats_reference_path)
    if corr >= 0.7:
        return result

    flipped = np.rot90(image, 2, axes=(0, 1))
    retry = _run_scanin_once(
        flipped, cgats_reference_path, cht_path, scanin_cmd, diagnostic, native_max_value
    )
    retry_corr = _luma_xyz_correlation(retry.samples, cgats_reference_path)
    return retry if retry_corr > corr else result


def _tiff_to_png(tiff_path: Path) -> bytes:
    import io

    import cv2
    import tifffile

    arr = tifffile.imread(str(tiff_path))
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        raise ScaninError("failed to encode scanin diagnostic TIFF as PNG")
    return io.BytesIO(buf.tobytes()).getvalue()
