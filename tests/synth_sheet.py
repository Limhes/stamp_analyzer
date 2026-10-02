"""Synthetic stock-card *sheet* generator (SPEC §5), for validating
core.detection. Builds on the single-stamp renderer in tests/synth.py:
places several rendered stamps at given centres/rotations on one shared
dark, hazy background, with optional glassine strip-edge lines crossing the
whole sheet and optional touching pairs (for watershed testing).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from tests.synth import RenderedStamp, SynthSpec, render_rotated_stamp


@dataclass
class StampPlacement:
    spec: SynthSpec
    centre_mm: tuple[float, float]  # centre of the stamp on the sheet, in mm


@dataclass
class PlacedStampGroundTruth:
    placement: StampPlacement
    centre_px: tuple[float, float]
    stamp_size_px: tuple[float, float]
    rotation_deg: float
    rendered: RenderedStamp


@dataclass
class SheetSpec:
    width_mm: float = 200.0
    height_mm: float = 280.0
    px_per_mm: float = 10.0
    background_level: float = 0.03
    haze_level: float = 0.01
    strip_edge_y_fracs: list[float] = field(default_factory=list)
    strip_edge_level: float = 0.12
    noise_sigma: float = 0.0
    blur_sigma_px: float = 0.0
    seed: int | None = None


@dataclass
class SheetGroundTruth:
    sheet_spec: SheetSpec
    scene_size_px: tuple[int, int]
    stamps: list[PlacedStampGroundTruth]


def generate_synthetic_sheet(
    sheet: SheetSpec, placements: list[StampPlacement]
) -> tuple[np.ndarray, SheetGroundTruth]:
    rng = np.random.default_rng(sheet.seed)
    ppm = sheet.px_per_mm
    full_scale = 65535.0

    scene_w = int(round(sheet.width_mm * ppm))
    scene_h = int(round(sheet.height_mm * ppm))
    scene = np.full((scene_h, scene_w), sheet.background_level * full_scale, dtype=np.float64)

    yy, xx = np.mgrid[0:scene_h, 0:scene_w]
    cx, cy = scene_w / 2.0, scene_h / 2.0
    r = np.hypot(xx - cx, yy - cy)
    r_norm = np.clip(r / (np.hypot(scene_w, scene_h) / 2.0), 0.0, 1.0)
    scene += sheet.haze_level * full_scale * (1.0 - r_norm)

    for y_frac in sheet.strip_edge_y_fracs:
        y0 = int(y_frac * scene_h)
        thickness = max(1, int(0.3 * ppm))
        scene[max(0, y0 - thickness // 2) : y0 + thickness // 2, :] += (
            sheet.strip_edge_level * full_scale
        )

    placed: list[PlacedStampGroundTruth] = []
    for placement in placements:
        rendered = render_rotated_stamp(placement.spec)
        local_w, local_h = rendered.local_size
        cx_px = placement.centre_mm[0] * ppm
        cy_px = placement.centre_mm[1] * ppm
        off_x = int(round(cx_px - local_w / 2.0))
        off_y = int(round(cy_px - local_h / 2.0))

        x0, y0 = max(0, off_x), max(0, off_y)
        x1, y1 = min(scene_w, off_x + local_w), min(scene_h, off_y + local_h)
        if x0 >= x1 or y0 >= y1:
            continue  # entirely off-sheet; skip

        src_x0, src_y0 = x0 - off_x, y0 - off_y
        src_x1, src_y1 = src_x0 + (x1 - x0), src_y0 + (y1 - y0)

        mask_crop = rendered.mask[src_y0:src_y1, src_x0:src_x1]
        img_crop = rendered.image[src_y0:src_y1, src_x0:src_x1]
        mask_bool = mask_crop > 32
        dest = scene[y0:y1, x0:x1]
        dest[mask_bool] = img_crop[mask_bool]
        scene[y0:y1, x0:x1] = dest

        placed.append(
            PlacedStampGroundTruth(
                placement=placement,
                centre_px=(off_x + rendered.rot_centre[0], off_y + rendered.rot_centre[1]),
                stamp_size_px=rendered.stamp_size_px,
                rotation_deg=placement.spec.rotation_deg,
                rendered=rendered,
            )
        )

    if sheet.blur_sigma_px > 0:
        import cv2

        k = max(3, int(2 * round(3 * sheet.blur_sigma_px) + 1))
        scene = cv2.GaussianBlur(scene, (k, k), sheet.blur_sigma_px)

    if sheet.noise_sigma > 0:
        scene += rng.normal(0.0, sheet.noise_sigma, scene.shape)

    scene = np.clip(scene, 0, full_scale).astype(np.uint16)

    ground_truth = SheetGroundTruth(
        sheet_spec=sheet, scene_size_px=(scene_w, scene_h), stamps=placed
    )
    return scene, ground_truth
