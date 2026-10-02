"""Synthetic stamp generator (SPEC §6.4).

Generates a stock-card-like scene (dark, hazy background, an optional
glassine strip-edge line) containing one rotated, perforated (or imperf, or
straight-edged) stamp, plus the ground truth used to validate
``core.perforation`` later. Not a core module: it is test/validation
infrastructure only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

Side = str  # "top" | "bottom" | "left" | "right"
SIDES: tuple[Side, ...] = ("top", "bottom", "left", "right")


@dataclass
class SideSpec:
    """Perforation spec for one side of the synthetic stamp."""

    gauge: float | None = 12.0  # teeth per 20 mm; None together with imperf/straight
    imperf: bool = False
    straight_edge: bool = False
    hole_diameter_mm: float = 0.8
    edge_roughness_mm: float = 0.0  # std of per-hole radius/position jitter


@dataclass
class SynthSpec:
    width_mm: float = 20.0
    height_mm: float = 25.0
    px_per_mm: float = 20.0  # synthetic scale, isotropic
    rotation_deg: float = 0.0
    noise_sigma: float = 0.0  # additive gaussian noise, in 16-bit counts
    blur_sigma_px: float = 0.0  # gaussian blur std, approximates scanner MTF
    paper_level: float = 0.78  # fraction of full scale, 16-bit
    background_level: float = 0.03
    haze_level: float = 0.015  # lifted-black haze added near the stamp
    margin_mm: float = 6.0  # black margin around the stamp in the generated scene
    strip_edge: bool = False
    strip_edge_y_frac: float = 0.5  # fractional y position of the glassine line
    strip_edge_level: float = 0.12
    sides: dict[Side, SideSpec] = field(default_factory=lambda: {s: SideSpec() for s in SIDES})
    seed: int | None = None


@dataclass
class HoleGroundTruth:
    side: Side
    gauge: float | None
    imperf: bool
    straight_edge: bool
    centres_px: list[tuple[float, float]]  # in the unrotated stamp-local frame
    pitch_mm: float | None


@dataclass
class SynthGroundTruth:
    spec: SynthSpec
    scene_size_px: tuple[int, int]  # (width, height)
    stamp_centre_px: tuple[float, float]  # centre of the stamp in the scene
    stamp_size_px: tuple[float, float]  # (width, height) before rotation
    rotation_deg: float
    holes: dict[Side, HoleGroundTruth]


def _hole_centres(length_mm: float, gauge: float, rng: np.random.Generator) -> list[float]:
    """1-D hole-centre positions (mm) along a side of given length, centred."""
    pitch = 20.0 / gauge
    n = max(1, round(length_mm / pitch))
    # Centre the row of n holes on the side.
    start = (length_mm - (n - 1) * pitch) / 2.0
    return [start + i * pitch for i in range(n)]


def generate_synthetic_stamp(
    spec: SynthSpec,
) -> tuple[np.ndarray, SynthGroundTruth]:
    """Render a synthetic stock-card scene with one stamp.

    Returns a 16-bit single-channel image (the scanner's green/luminance
    channel is representative enough for perforation work) and the ground
    truth used to validate algorithms against.
    """
    rng = np.random.default_rng(spec.seed)
    ppm = spec.px_per_mm

    stamp_w_px = spec.width_mm * ppm
    stamp_h_px = spec.height_mm * ppm
    margin_px = spec.margin_mm * ppm
    # Scene large enough to contain the rotated stamp plus margin.
    diag = int(np.ceil(np.hypot(stamp_w_px, stamp_h_px) + 2 * margin_px))
    scene_w = scene_h = diag

    full_scale = 65535.0
    scene = np.full((scene_h, scene_w), spec.background_level * full_scale, dtype=np.float64)

    # Haze: radial lift of blacks around where the stamp will sit.
    yy, xx = np.mgrid[0:scene_h, 0:scene_w]
    cx, cy = scene_w / 2.0, scene_h / 2.0
    r = np.hypot(xx - cx, yy - cy)
    r_norm = np.clip(r / (diag / 2.0), 0.0, 1.0)
    scene += spec.haze_level * full_scale * (1.0 - r_norm)

    if spec.strip_edge:
        y0 = int(spec.strip_edge_y_frac * scene_h)
        thickness = max(1, int(0.3 * ppm))
        scene[max(0, y0 - thickness // 2) : y0 + thickness // 2, :] += (
            spec.strip_edge_level * full_scale
        )

    # Render the stamp (paper + holes) in its own local, unrotated canvas.
    pad = int(np.ceil(0.5 * ppm)) + 4
    local_w = int(np.ceil(stamp_w_px)) + 2 * pad
    local_h = int(np.ceil(stamp_h_px)) + 2 * pad
    local = np.zeros((local_h, local_w), dtype=np.uint8)
    rect_tl = (pad, pad)
    rect_br = (pad + int(round(stamp_w_px)), pad + int(round(stamp_h_px)))
    cv2.rectangle(local, rect_tl, rect_br, color=255, thickness=-1)

    holes: dict[Side, HoleGroundTruth] = {}
    hole_mask = np.zeros_like(local)

    for side in SIDES:
        side_spec = spec.sides.get(side, SideSpec())
        centres: list[tuple[float, float]] = []
        pitch_mm = None

        if side_spec.imperf or side_spec.straight_edge or side_spec.gauge is None:
            holes[side] = HoleGroundTruth(
                side=side,
                gauge=None,
                imperf=side_spec.imperf,
                straight_edge=side_spec.straight_edge,
                centres_px=[],
                pitch_mm=None,
            )
            continue

        gauge = side_spec.gauge
        pitch_mm = 20.0 / gauge
        radius_px = side_spec.hole_diameter_mm / 2.0 * ppm

        if side in ("top", "bottom"):
            positions_mm = _hole_centres(spec.width_mm, gauge, rng)
            y_mm = 0.0 if side == "top" else spec.height_mm
        else:
            positions_mm = _hole_centres(spec.height_mm, gauge, rng)
            x_mm = 0.0 if side == "left" else spec.width_mm

        for pos_mm in positions_mm:
            if side in ("top", "bottom"):
                cx_px = pad + pos_mm * ppm
                cy_px = pad + y_mm * ppm
            else:
                cx_px = pad + x_mm * ppm
                cy_px = pad + pos_mm * ppm

            jitter_px = side_spec.edge_roughness_mm * ppm
            dx = rng.normal(0.0, jitter_px) if jitter_px > 0 else 0.0
            dy = rng.normal(0.0, jitter_px) if jitter_px > 0 else 0.0
            r = radius_px + (rng.normal(0.0, jitter_px) if jitter_px > 0 else 0.0)
            r = max(1.0, r)
            cv2.circle(
                hole_mask,
                (int(round(cx_px + dx)), int(round(cy_px + dy))),
                int(round(r)),
                color=255,
                thickness=-1,
            )
            centres.append((cx_px, cy_px))

        holes[side] = HoleGroundTruth(
            side=side,
            gauge=gauge,
            imperf=False,
            straight_edge=False,
            centres_px=centres,
            pitch_mm=pitch_mm,
        )

    stamp_mask = cv2.bitwise_and(local, cv2.bitwise_not(hole_mask))
    stamp_img = np.where(
        stamp_mask > 0, spec.paper_level * full_scale, spec.background_level * full_scale
    )

    # Rotate the local stamp canvas and composite onto the scene.
    rot_centre = (local_w / 2.0, local_h / 2.0)
    rot_mat = cv2.getRotationMatrix2D(rot_centre, spec.rotation_deg, 1.0)
    rotated = cv2.warpAffine(
        stamp_img,
        rot_mat,
        (local_w, local_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=spec.background_level * full_scale,
    )
    rotated_mask = cv2.warpAffine(
        stamp_mask,
        rot_mat,
        (local_w, local_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )

    off_x = int(round((scene_w - local_w) / 2.0))
    off_y = int(round((scene_h - local_h) / 2.0))
    mask_bool = rotated_mask > 32
    scene[off_y : off_y + local_h, off_x : off_x + local_w][mask_bool] = rotated[mask_bool]

    if spec.blur_sigma_px > 0:
        k = max(3, int(2 * round(3 * spec.blur_sigma_px) + 1))
        scene = cv2.GaussianBlur(scene, (k, k), spec.blur_sigma_px)

    if spec.noise_sigma > 0:
        scene += rng.normal(0.0, spec.noise_sigma, scene.shape)

    scene = np.clip(scene, 0, full_scale).astype(np.uint16)

    stamp_centre_scene = (off_x + rot_centre[0], off_y + rot_centre[1])

    ground_truth = SynthGroundTruth(
        spec=spec,
        scene_size_px=(scene_w, scene_h),
        stamp_centre_px=stamp_centre_scene,
        stamp_size_px=(stamp_w_px, stamp_h_px),
        rotation_deg=spec.rotation_deg,
        holes=holes,
    )
    return scene, ground_truth
