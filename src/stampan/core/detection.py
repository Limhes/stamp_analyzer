"""Automatic stamp detection and extraction from a sample-sheet scan (SPEC §5).

Pipeline: threshold the dark stock-card background, suppress bright
glassine strip-edge lines, close perforation-tooth gaps, take connected
components, split touching stamps with a distance-transform watershed,
filter by plausible physical size and fill ratio, and fit a `minAreaRect`
per stamp. Produces upright 16-bit crops with a black margin and the
transform back to sample coordinates.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import ndimage as ndi
from skimage.segmentation import watershed

#: Plausible stamp side length, mm (SPEC §5).
MIN_STAMP_SIDE_MM = 8.0
MAX_STAMP_SIDE_MM = 80.0
MIN_FILL_RATIO = 0.5


@dataclass
class DetectedStamp:
    index: int
    centre_px: tuple[float, float]
    size_px: tuple[float, float]  # (width, height) of the minAreaRect
    angle_deg: float
    area_px: float
    fill_ratio: float
    confidence: float
    warnings: list[str] = field(default_factory=list)
    flagged_for_manual_review: bool = False

    def box_points(self) -> np.ndarray:
        return cv2.boxPoints((self.centre_px, self.size_px, self.angle_deg))


@dataclass
class DetectionResult:
    stamps: list[DetectedStamp]
    foreground_mask: np.ndarray  # uint8, cleaned mask used for component analysis
    warnings: list[str] = field(default_factory=list)


def _to_intensity(image: np.ndarray) -> np.ndarray:
    """A single-channel intensity image: max channel (brightest per spec's
    "background is dark in L* or the max channel").

    Stays in the image's native dtype (uint8/uint16) rather than promoting
    to float: on a full 1200dpi flatbed scan (hundreds of MB), a float64
    copy is a multi-GB transient allocation for an operation that is just a
    per-pixel max -- comfortably exceeds an "average PC"'s free RAM. Every
    downstream step (GaussianBlur, normalize, Otsu threshold) handles
    uint8/uint16 input natively, so no float promotion is needed here.
    """
    if image.ndim == 2:
        return image
    return image.max(axis=-1)


def _suppress_strip_edges(
    mask: np.ndarray, min_length_frac: float = 0.4, min_vertical_thickness_px: int = 15
) -> np.ndarray:
    """Remove long, thin, near-horizontal structures (glassine strip edges)
    from a foreground mask.

    A strip edge is long (``min_length_frac`` of the image width) *and*
    thin (shorter than ``min_vertical_thickness_px``, well under a stamp's
    own height). Gating on both, rather than length alone, matters once
    several stamps sit edge to edge: a row through the middle of that whole
    row of stamps can also be "long", but it is part of a vertically thick
    (tall) foreground block, unlike an actual strip-edge line -- so it must
    not be erased.
    """
    long_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(15, int(mask.shape[1] * min_length_frac)), 1)
    )
    long_enough = cv2.morphologyEx(mask, cv2.MORPH_OPEN, long_kernel)

    tall_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, min_vertical_thickness_px))
    vertically_thick = cv2.morphologyEx(mask, cv2.MORPH_OPEN, tall_kernel)

    strip_lines = cv2.bitwise_and(long_enough, cv2.bitwise_not(vertically_thick))
    return cv2.bitwise_and(mask, cv2.bitwise_not(strip_lines))


def _threshold_foreground(intensity: np.ndarray, blur_sigma_px: float = 3.0) -> np.ndarray:
    smoothed = cv2.GaussianBlur(intensity, (0, 0), blur_sigma_px)
    smoothed8 = cv2.normalize(smoothed, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, mask = cv2.threshold(smoothed8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return mask


def _close_perforation_gaps(mask: np.ndarray, kernel_px: int = 7) -> np.ndarray:
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_px, kernel_px))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def _split_touching_components(
    component_mask: np.ndarray,
    min_distance_px: int,
) -> list[np.ndarray]:
    """Try to split a connected-component mask into separate stamps via a
    distance-transform watershed. ``min_distance_px`` (about half a
    minimum-plausible stamp side) keeps a single stamp's own distance-map
    ridge from being read as several close peaks; two touching stamps each
    contribute their own, well-separated peak. Returns a list of
    single-stamp masks (the original mask unchanged if no split was made)."""
    distance = ndi.distance_transform_edt(component_mask)
    coords = _local_maxima(distance, min_distance_px, component_mask)
    if len(coords) < 2:
        return [component_mask]

    markers = np.zeros(component_mask.shape, dtype=np.int32)
    for i, (y, x) in enumerate(coords, start=1):
        markers[y, x] = i
    labels = watershed(-distance, markers, mask=component_mask)

    pieces = []
    for label in range(1, labels.max() + 1):
        piece = (labels == label).astype(np.uint8) * 255
        if np.count_nonzero(piece) > 0:
            pieces.append(piece)
    return pieces if len(pieces) >= 2 else [component_mask]


def _offset_stamp(stamp: DetectedStamp, dx: float, dy: float) -> DetectedStamp:
    """Translate a stamp's centre from a bounding-box-local frame back to
    full-sample coordinates (SPEC §5 perf: per-component work is restricted
    to each component's own bounding box, not the whole sheet)."""
    cx, cy = stamp.centre_px
    stamp.centre_px = (cx + dx, cy + dy)
    return stamp


def _local_maxima(distance: np.ndarray, min_distance_px: int, mask: np.ndarray) -> list:
    from skimage.feature import peak_local_max

    coords = peak_local_max(
        distance, min_distance=min_distance_px, labels=mask.astype(bool), exclude_border=False
    )
    return [tuple(c) for c in coords]


def detect_stamps(
    sample: np.ndarray,
    px_per_mm_x: float,
    px_per_mm_y: float,
    blur_sigma_px: float = 3.0,
    close_kernel_px: int = 7,
    min_side_mm: float = MIN_STAMP_SIDE_MM,
    max_side_mm: float = MAX_STAMP_SIDE_MM,
    min_fill_ratio: float = MIN_FILL_RATIO,
    single_shape_fill_ratio: float = 0.75,
    suppress_strip_edges: bool = True,
) -> DetectionResult:
    """Detect stamp rectangles on a sample-sheet scan (SPEC §5).

    Watershed splitting is only attempted on a component that doesn't
    already look like one clean rectangle (``single_shape_fill_ratio``): a
    single stamp's own distance-transform ridge can otherwise present more
    than one near-equal local maximum (e.g. at both ends of a long, flat
    ridge) and get needlessly cut in half, whereas two touching stamps form
    a concave boundary at the join and so fail the direct single-rect fit's
    fill-ratio check.
    """
    warnings: list[str] = []
    intensity = _to_intensity(sample)

    mask = _threshold_foreground(intensity, blur_sigma_px)
    if suppress_strip_edges:
        mask = _suppress_strip_edges(mask)
    mask = _close_perforation_gaps(mask, close_kernel_px)

    px_per_mm_avg = (px_per_mm_x + px_per_mm_y) / 2.0
    min_side_px = min_side_mm * px_per_mm_avg
    max_side_px = max_side_mm * px_per_mm_avg
    min_distance_px = max(5, int(min_side_px * 0.5))

    n_components, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)

    stamps: list[DetectedStamp] = []
    index = 0
    # Padding around each component's bounding box: generous enough that a
    # watershed split's distance transform isn't clipped at the edge, cheap
    # since it only widens a small local crop, not the whole sheet.
    bbox_pad_px = max(close_kernel_px, min_distance_px // 2) + 2
    for label in range(1, n_components):
        # Restrict all per-component work to this component's own bounding
        # box, not the full sheet. `labels == label` and especially
        # distance_transform_edt (inside _split_touching_components) scale
        # with the array they're given; on a full 1200dpi sheet, doing that
        # once per detected stamp turned a handful of small local
        # computations into dozens of multi-hundred-MB/GB ones.
        bx, by, bw, bh, _area = stats[label]
        x0 = max(0, bx - bbox_pad_px)
        y0 = max(0, by - bbox_pad_px)
        x1 = min(labels.shape[1], bx + bw + bbox_pad_px)
        y1 = min(labels.shape[0], by + bh + bbox_pad_px)
        local_labels = labels[y0:y1, x0:x1]
        component_mask = (local_labels == label).astype(np.uint8) * 255

        direct_stamp, direct_warnings = _rect_from_mask(
            component_mask, index, min_side_px, max_side_px, min_fill_ratio
        )

        looks_like_one_stamp = (
            direct_stamp is not None
            and direct_stamp.fill_ratio >= single_shape_fill_ratio
            and max(direct_stamp.size_px) <= max_side_px
        )
        if looks_like_one_stamp:
            stamps.append(_offset_stamp(direct_stamp, x0, y0))
            index += 1
            continue

        pieces = _split_touching_components(component_mask, min_distance_px)
        if len(pieces) >= 2:
            for piece_mask in pieces:
                stamp, piece_warnings = _rect_from_mask(
                    piece_mask, index, min_side_px, max_side_px, min_fill_ratio
                )
                if stamp is None:
                    continue
                stamp.warnings.extend(piece_warnings)
                stamp.warnings.append("split from a touching-stamp blob by watershed")
                stamp.confidence *= 0.7
                stamps.append(_offset_stamp(stamp, x0, y0))
                index += 1
        elif direct_stamp is not None:
            direct_stamp.warnings.extend(direct_warnings)
            direct_stamp.warnings.append(
                "irregular shape could not be split; kept as one stamp, flagged for review"
            )
            direct_stamp.confidence *= 0.5
            direct_stamp.flagged_for_manual_review = True
            stamps.append(_offset_stamp(direct_stamp, x0, y0))
            index += 1
        else:
            warnings.append(
                f"connected component {label} is not a plausible stamp and could not be "
                "split; flagged for manual review"
            )

    if not stamps:
        warnings.append("no stamps detected")

    return DetectionResult(stamps=stamps, foreground_mask=mask, warnings=warnings)


def _rect_from_mask(
    mask: np.ndarray,
    index: int,
    min_side_px: float,
    max_side_px: float,
    min_fill_ratio: float,
) -> tuple[DetectedStamp | None, list[str]]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, []
    contour = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(contour)
    if area <= 0:
        return None, []

    (cx, cy), (w, h), angle = cv2.minAreaRect(contour)
    rect_area = w * h
    fill_ratio = area / rect_area if rect_area > 0 else 0.0

    warnings: list[str] = []
    confidence = 1.0
    side_min, side_max = min(w, h), max(w, h)

    reject = False
    if side_min < min_side_px or side_max > max_side_px:
        warnings.append(f"size {side_min:.0f}x{side_max:.0f}px outside plausible stamp size range")
        reject = True
    if fill_ratio < min_fill_ratio:
        warnings.append(f"fill ratio {fill_ratio:.2f} below {min_fill_ratio} (not rectangular)")
        reject = True

    if reject:
        return None, warnings

    if fill_ratio < 0.75:
        confidence *= 0.7
        warnings.append("low fill ratio; rectangle fit may be imprecise")

    stamp = DetectedStamp(
        index=index,
        centre_px=(cx, cy),
        size_px=(w, h),
        angle_deg=angle,
        area_px=area,
        fill_ratio=fill_ratio,
        confidence=confidence,
        warnings=warnings,
        flagged_for_manual_review=bool(warnings),
    )
    return stamp, []


# --------------------------------------------------------------------------
# Crop extraction
# --------------------------------------------------------------------------


@dataclass
class CropTransform:
    """The transform needed to map crop-local (px) coordinates back to the
    original sample-sheet (px) coordinates: rotate by -angle_deg about
    crop_origin_in_sample, then translate by crop_origin_in_sample."""

    sample_centre_px: tuple[float, float]  # stamp centre in the original sample
    angle_deg: float  # rotation applied to upright the crop (sample -> crop)
    crop_size_px: tuple[int, int]  # (width, height) of the crop
    mirrored: bool = False

    def to_dict(self) -> dict:
        return {
            "sample_centre_px": self.sample_centre_px,
            "angle_deg": self.angle_deg,
            "crop_size_px": self.crop_size_px,
            "mirrored": self.mirrored,
        }


def extract_crop(
    sample: np.ndarray,
    stamp: DetectedStamp,
    px_per_mm_x: float,
    px_per_mm_y: float,
    margin_mm: float = 3.0,
    background_level: float | None = None,
) -> tuple[np.ndarray, CropTransform]:
    """Extract an upright crop around a detected stamp with a black margin
    of at least ``margin_mm`` around the tooth tips (SPEC §5)."""
    margin_px_x = margin_mm * px_per_mm_x
    margin_px_y = margin_mm * px_per_mm_y
    w, h = stamp.size_px
    angle = stamp.angle_deg

    crop_w = int(np.ceil(w + 2 * margin_px_x))
    crop_h = int(np.ceil(h + 2 * margin_px_y))

    if background_level is None:
        # A full-sheet percentile is a reasonable one-off background
        # estimate, but recomputing it (and the ROI cast below) per stamp
        # doesn't scale: a sheet with dozens of stamps would re-sort the
        # whole multi-hundred-MB sheet that many times. Callers extracting
        # several stamps from the same sample should compute this once and
        # pass it in (see cli.py's `detect` command).
        background_level = float(np.percentile(sample, 1))

    # Rotation only needs a neighbourhood around the stamp, not the whole
    # sheet. Crop a generous axis-aligned ROI first with plain integer
    # slicing (a cheap view, no dtype promotion), then float-cast and
    # warpAffine only that ROI. Previously this cast the *entire* sample to
    # float64 for every single stamp -- on a 1200dpi flatbed scan that's a
    # multi-GB transient allocation per stamp, easily exhausting an
    # "average PC"'s RAM on a sheet with more than a couple of stamps.
    # `roi_radius` is sized so the ROI contains the full rotated crop
    # region regardless of rotation angle (the crop's half-diagonal is the
    # farthest any output pixel's source can be from the stamp centre).
    roi_radius = int(np.ceil(np.hypot(crop_w, crop_h) / 2.0)) + 4
    cx, cy = stamp.centre_px
    x0 = max(0, int(cx) - roi_radius)
    y0 = max(0, int(cy) - roi_radius)
    x1 = min(sample.shape[1], int(cx) + roi_radius)
    y1 = min(sample.shape[0], int(cy) + roi_radius)
    roi = sample[y0:y1, x0:x1]
    roi_centre = (cx - x0, cy - y0)

    rot_mat = cv2.getRotationMatrix2D(roi_centre, angle, 1.0)
    rot_mat[0, 2] += crop_w / 2.0 - roi_centre[0]
    rot_mat[1, 2] += crop_h / 2.0 - roi_centre[1]

    crop = cv2.warpAffine(
        roi.astype(np.float32),
        rot_mat,
        (crop_w, crop_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=background_level,
    )
    crop = np.clip(crop, 0, 65535).astype(np.uint16)

    transform = CropTransform(
        sample_centre_px=stamp.centre_px,
        angle_deg=angle,
        crop_size_px=(crop_w, crop_h),
    )
    return crop, transform


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------


#: This is a visual "did detection look sane" diagnostic, not a measurement
#: input -- a full 1200dpi sheet (~12000x9000px) encodes to 100+ MB of PNG
#: and takes several seconds, for no inspection benefit over a downsampled
#: version. Capping the long side keeps it fast and a few hundred KB.
MAX_OVERVIEW_DIM_PX = 2400


def render_overview(sample: np.ndarray, result: DetectionResult) -> bytes:
    """Sample overview with numbered rotated rectangles; flagged regions in
    a different colour (SPEC §5)."""
    intensity = _to_intensity(sample)
    # cv2.normalize does the same min-max scaling as the old
    # `intensity / intensity.max() * 255` without promoting the full sheet
    # to a second float64 array first.
    display = cv2.normalize(intensity, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
    display = cv2.cvtColor(display, cv2.COLOR_GRAY2BGR)

    scale = min(1.0, MAX_OVERVIEW_DIM_PX / max(display.shape[:2]))
    if scale < 1.0:
        display = cv2.resize(display, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    for stamp in result.stamps:
        colour = (0, 165, 255) if stamp.flagged_for_manual_review else (0, 220, 0)
        box = (stamp.box_points() * scale).astype(np.int32)
        cv2.drawContours(display, [box], 0, colour, 2)
        cv2.putText(
            display,
            str(stamp.index),
            (int(stamp.centre_px[0] * scale), int(stamp.centre_px[1] * scale)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            colour,
            2,
            cv2.LINE_AA,
        )

    ok, buf = cv2.imencode(".png", display)
    if not ok:
        raise RuntimeError("failed to encode overview diagnostic")
    return buf.tobytes()


def render_contact_sheet(crops: list[np.ndarray], n_cols: int = 6) -> bytes:
    """A contact sheet of all extracted crops, for a quick visual scan."""
    if not crops:
        fig, ax = plt.subplots(figsize=(4, 1))
        ax.text(0.5, 0.5, "no stamps detected", ha="center", va="center")
        ax.axis("off")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150)
        plt.close(fig)
        return buf.getvalue()

    n_rows = int(np.ceil(len(crops) / n_cols))
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(2.2 * n_cols, 2.6 * n_rows))
    axes = np.atleast_2d(axes)
    for i in range(n_rows * n_cols):
        ax = axes[i // n_cols, i % n_cols]
        ax.axis("off")
        if i < len(crops):
            crop = crops[i]
            # A thumbnail is all a contact sheet needs; the subplot itself
            # renders at a few hundred px, so downsample before imshow
            # rather than handing matplotlib a full-resolution crop (up to
            # a few thousand px per side) to shrink on every draw.
            thumb_scale = min(1.0, 300 / max(crop.shape[:2]))
            if thumb_scale < 1.0:
                crop = cv2.resize(
                    crop, None, fx=thumb_scale, fy=thumb_scale, interpolation=cv2.INTER_AREA
                )
            display = cv2.normalize(crop, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
            ax.imshow(display, cmap="gray")
            ax.set_title(str(i), fontsize=9)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    return buf.getvalue()
