"""Paper/ink colour analysis in CIELAB D50 (SPEC §8).

Pipeline: interior mask (stamp mask eroded past the perforation zone) minus
exclusions (user-drawn zones, specular pixels) -> convert to Lab D50 via the
reference's characterisation model -> Gaussian-mixture cluster into paper +
N inks -> per-cluster statistics (mean/median/std/covariance Lab,
circular-mean LCh, core-ink estimate, ΔE00 spread, ink-paper ΔE00,
Munsell notation, a philatelic colour name (core.philatelic_colours),
display sRGB swatch).

There is no automatic cancellation detection feeding into this (an earlier
classical pipeline was tried and dropped as unreliable). Cancelled/soiled
areas are kept out of the statistics by having the user select the area to
analyse directly (``user_inclusion_mask``), not by auto-detecting and
excluding the cancellation.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import colour
import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
from scipy import ndimage as ndi
from sklearn.mixture import GaussianMixture

from stampan.core.models import ColourCluster, ColourResult
from stampan.core.naming import munsell_notation
from stampan.core.philatelic_colours import (
    PhilatelicColourEntry,
    load_philatelic_colours,
    name_philatelic_colour,
)

_DEFAULT_PHILATELIC_TABLE: list[PhilatelicColourEntry] | None = None


def _default_philatelic_table() -> list[PhilatelicColourEntry]:
    global _DEFAULT_PHILATELIC_TABLE
    if _DEFAULT_PHILATELIC_TABLE is None:
        _DEFAULT_PHILATELIC_TABLE = load_philatelic_colours()
    return _DEFAULT_PHILATELIC_TABLE


_OBSERVER = "CIE 1931 2 Degree Standard Observer"
_D50_XY = colour.CCS_ILLUMINANTS[_OBSERVER]["D50"]
DEFAULT_INTERIOR_MARGIN_MM = 0.3  # SPEC §8.1, beyond the hole-bottom line
DEFAULT_SPECULAR_L_THRESHOLD = 98.0  # L*, near-paper-white glare


# --------------------------------------------------------------------------
# Region of analysis (SPEC §8.1)
# --------------------------------------------------------------------------


def stamp_mask_from_crop(crop: np.ndarray) -> np.ndarray:
    """Foreground (stamp) mask against the black margin. Shares
    ``core.perforation.segment_stamp`` rather than a second, independently
    drifting copy -- but then fills internal holes, which
    ``segment_stamp`` deliberately does *not* do (it needs perforation
    holes to stay holes). colour.py cares about a different thing: *area*,
    not perimeter. A single global Otsu threshold can split a stamp with a
    large solid-ink region *between* the ink and paper populations rather
    than between background and (ink+paper) -- found via a synthetic test
    with a large solid ink block, where the ink read as "background" and
    silently vanished from every colour statistic. Filling holes not
    connected to the frame recovers any such region while still correctly
    excluding the true (border-connected) background margin.
    """
    from stampan.core.perforation import segment_stamp

    mask = segment_stamp(crop)
    filled = ndi.binary_fill_holes(mask > 0)
    return (filled.astype(np.uint8)) * 255


def interior_mask(
    stamp_mask: np.ndarray,
    margin_mm: float,
    px_per_mm_x: float,
    px_per_mm_y: float,
) -> np.ndarray:
    """Erode the stamp mask inward by ``margin_mm`` (SPEC §8.1: past the
    hole-bottom line plus >= 0.3mm). Uses a distance transform rather than a
    fixed morphological kernel so the margin is exact in mm even when x/y
    scale differs; the per-pixel distance is scaled to an isotropic "mm
    units" grid first.

    This applies one uniform margin all around rather than a per-side,
    direction-aware one derived from each side's own measured perforation
    depth (which would need the actual hole-bottom line geometry, not
    exposed by core.perforation's current SideAnalysis). Documented
    simplification: callers should pass the largest margin required by any
    side (deepest perforation + the 0.3mm safety margin).
    """
    scale_x = px_per_mm_x
    scale_y = px_per_mm_y
    if abs(scale_x - scale_y) / max(scale_x, scale_y) > 0.01:
        # Resample to isotropic "mm-unit" pixels so the Euclidean distance
        # transform gives a true mm margin on both axes.
        h, w = stamp_mask.shape
        iso_w, iso_h = int(w * scale_y / scale_x), h  # normalise x to y's scale
        resized = cv2.resize(stamp_mask, (iso_w, iso_h), interpolation=cv2.INTER_NEAREST)
        dist = ndi.distance_transform_edt(resized > 0) / scale_y
        eroded = dist > margin_mm
        return cv2.resize(eroded.astype(np.uint8) * 255, (w, h), interpolation=cv2.INTER_NEAREST)
    dist_px = ndi.distance_transform_edt(stamp_mask > 0)
    return np.where(dist_px > margin_mm * scale_x, 255, 0).astype(np.uint8)


def exclude_artifacts(
    crop: np.ndarray,
    lab: np.ndarray,
    mask: np.ndarray,
    user_exclusion_mask: np.ndarray | None = None,
    specular_l_threshold: float = DEFAULT_SPECULAR_L_THRESHOLD,
) -> tuple[np.ndarray, list[str]]:
    """Remove user-drawn exclusions and near-specular pixels from ``mask``.

    There is no automatic cancellation-mask exclusion: an earlier classical
    cancellation-segmentation pipeline (SPEC §7) was tried and dropped --
    its output wasn't reliable enough to exclude automatically. Instead, the
    user selects the area to analyse directly (``analyse_colour``'s
    ``user_inclusion_mask``, e.g. "just this clean patch of paper" or "just
    this part of the design ink"), which sidesteps needing to detect
    cancellation at all. Strip-edge-line exclusion is a documented no-op
    hook (SPEC mentions it; stamp crops in practice don't carry strip-edge
    lines once extracted, since those live in the background margin that's
    already outside the stamp mask)."""
    warnings: list[str] = []
    result = mask.copy()

    if user_exclusion_mask is not None:
        result = cv2.bitwise_and(result, cv2.bitwise_not(user_exclusion_mask))

    specular = lab[..., 0] > specular_l_threshold
    result = cv2.bitwise_and(result, np.where(specular, 0, 255).astype(np.uint8))

    return result, warnings


# --------------------------------------------------------------------------
# Colour space conversion (SPEC §8.2)
# --------------------------------------------------------------------------


def rgb_to_lab_d50(rgb: np.ndarray, predict_lab_fn) -> np.ndarray:
    """Apply the reference's characterisation model (scanner RGB -> Lab
    D50) to every pixel of an (H, W, 3) image. ``predict_lab_fn`` is a
    ``ColourModel.predict_lab``-shaped callable (SPEC §4.3's chosen model)."""
    h, w = rgb.shape[:2]
    flat = rgb.reshape(-1, 3).astype(np.float64)
    lab_flat = predict_lab_fn(flat)
    return lab_flat.reshape(h, w, 3)


def lab_to_lch(lab: np.ndarray) -> np.ndarray:
    lightness, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    chroma = np.hypot(a, b)
    hue = np.degrees(np.arctan2(b, a)) % 360.0
    return np.stack([lightness, chroma, hue], axis=-1)


# --------------------------------------------------------------------------
# Clustering (SPEC §8.3)
# --------------------------------------------------------------------------


@dataclass
class ClusteringResult:
    gmm: GaussianMixture
    labels: np.ndarray  # (N,) per included pixel: cluster index
    posteriors: np.ndarray  # (N, k)
    mixed: np.ndarray  # (N,) bool, posterior < tau
    paper_index: int
    cluster_names: dict[int, str]  # index -> "paper" | "ink1" | ...


def cluster_colours(
    lab_pixels: np.ndarray,
    n_inks: int,
    tau: float = 0.9,
    subsample: int = 200_000,
    random_state: int = 0,
) -> ClusteringResult:
    """GMM clustering of Lab pixels into 1 paper + n_inks ink clusters
    (SPEC §8.3). Fits on a subsample, predicts on all pixels given."""
    rng = np.random.default_rng(random_state)
    n = lab_pixels.shape[0]
    fit_idx = rng.choice(n, size=min(subsample, n), replace=False)

    gmm = GaussianMixture(
        n_components=1 + n_inks,
        covariance_type="full",
        init_params="kmeans",
        random_state=random_state,
    )
    gmm.fit(lab_pixels[fit_idx])

    posteriors = gmm.predict_proba(lab_pixels)
    labels = np.argmax(posteriors, axis=1)
    mixed = posteriors.max(axis=1) < tau

    means = gmm.means_
    lightness, chroma = means[:, 0], np.hypot(means[:, 1], means[:, 2])
    paper_index = int(np.lexsort((chroma, -lightness))[0])  # highest L*, then lowest C*

    cluster_names = {paper_index: "paper"}
    ink_order = [i for i in range(1 + n_inks) if i != paper_index]
    for rank, idx in enumerate(ink_order, start=1):
        cluster_names[idx] = f"ink{rank}"

    return ClusteringResult(
        gmm=gmm,
        labels=labels,
        posteriors=posteriors,
        mixed=mixed,
        paper_index=paper_index,
        cluster_names=cluster_names,
    )


def reassign_fixed_means(
    lab_pixels: np.ndarray, means: np.ndarray, covariances: np.ndarray, weights: np.ndarray
) -> np.ndarray:
    """Re-run the posterior assignment with fixed (e.g. user-dragged)
    cluster centroids -- SPEC §8.4's "dragging a centroid re-runs the
    assignment with fixed means." Returns posteriors (N, k)."""
    k = len(weights)
    log_probs = np.empty((lab_pixels.shape[0], k))
    for i in range(k):
        rv_logpdf = _multivariate_normal_logpdf(lab_pixels, means[i], covariances[i])
        log_probs[:, i] = np.log(weights[i] + 1e-300) + rv_logpdf
    log_probs -= log_probs.max(axis=1, keepdims=True)
    probs = np.exp(log_probs)
    return probs / probs.sum(axis=1, keepdims=True)


def _multivariate_normal_logpdf(x: np.ndarray, mean: np.ndarray, cov: np.ndarray) -> np.ndarray:
    d = mean.shape[0]
    diff = x - mean
    cov_inv = np.linalg.inv(cov)
    sign, logdet = np.linalg.slogdet(cov)
    maha = np.einsum("ni,ij,nj->n", diff, cov_inv, diff)
    return -0.5 * (d * np.log(2 * np.pi) + logdet + maha)


# --------------------------------------------------------------------------
# Per-cluster statistics (SPEC §8.5)
# --------------------------------------------------------------------------


def circular_hue_mean(hue_deg: np.ndarray, weights: np.ndarray) -> float:
    """C*-weighted circular mean hue, in degrees [0, 360)."""
    rad = np.radians(hue_deg)
    s = np.sum(weights * np.sin(rad))
    c = np.sum(weights * np.cos(rad))
    return float(np.degrees(np.arctan2(s, c)) % 360.0)


def lab_to_display_srgb(lab: np.ndarray) -> tuple[tuple[int, int, int], bool]:
    """Lab D50 -> an approximate display sRGB swatch (assumes an sRGB
    monitor; display only, never feeds back into measurements)."""
    xyz_d50 = colour.Lab_to_XYZ(lab, illuminant=_D50_XY)
    rgb = colour.XYZ_to_RGB(
        xyz_d50,
        "sRGB",
        illuminant=_D50_XY,
        chromatic_adaptation_transform="Bradford",
        apply_cctf_encoding=True,
    )
    out_of_gamut = bool(np.any(rgb < -1e-6) or np.any(rgb > 1 + 1e-6))
    clipped = np.clip(rgb, 0.0, 1.0)
    srgb_255 = tuple(int(round(v * 255)) for v in clipped)
    return srgb_255, out_of_gamut


def lab_image_to_display_rgb(lab: np.ndarray) -> np.ndarray:
    """Vectorised Lab D50 (H, W, 3) -> display sRGB uint8 (display only,
    e.g. the report's calibrated stamp photo; never feeds back into
    measurements). NaN Lab (pixels outside the colour model's trained
    gamut, SPEC §4.3) is replaced with a neutral mid-grey rather than left
    to propagate into an invalid-cast warning."""
    xyz = colour.Lab_to_XYZ(lab, illuminant=_D50_XY)
    rgb = colour.XYZ_to_RGB(
        xyz,
        "sRGB",
        illuminant=_D50_XY,
        chromatic_adaptation_transform="Bradford",
        apply_cctf_encoding=True,
    )
    rgb = np.nan_to_num(rgb, nan=0.5, posinf=1.0, neginf=0.0)
    return (np.clip(rgb, 0.0, 1.0) * 255).astype(np.uint8)


def compute_cluster_stats(
    label: str,
    lab_pixels: np.ndarray,  # this cluster's non-mixed pixels
    paper_mean_lab: np.ndarray | None,
    total_pixel_count: int,
    philatelic_table: list[PhilatelicColourEntry] | None = None,
) -> ColourCluster:
    lch = lab_to_lch(lab_pixels[:, None, :])[:, 0, :]
    mean_lab = lab_pixels.mean(axis=0)
    median_lab = np.median(lab_pixels, axis=0)
    std_lab = lab_pixels.std(axis=0)

    hue_mean = circular_hue_mean(lch[:, 2], weights=lch[:, 1])
    mean_chroma = float(np.mean(lch[:, 1]))
    mean_lch = (float(mean_lab[0]), mean_chroma, hue_mean)

    de00_spread = float(np.mean(colour.difference.delta_E_CIE2000(lab_pixels, mean_lab)))

    core_mean_lab = None
    if paper_mean_lab is not None and label != "paper":
        dist_to_paper = np.linalg.norm(lab_pixels - paper_mean_lab, axis=1)
        if len(dist_to_paper) >= 4:
            threshold = np.percentile(dist_to_paper, 75)
            core_pixels = lab_pixels[dist_to_paper >= threshold]
            core_mean_lab = tuple(core_pixels.mean(axis=0).tolist())

    srgb_swatch, out_of_gamut = lab_to_display_srgb(mean_lab)

    munsell_name = munsell_notation(mean_lab).munsell
    table = philatelic_table if philatelic_table is not None else _default_philatelic_table()
    naming_result = name_philatelic_colour(mean_lab, table)

    return ColourCluster(
        label=label,
        pixel_count=len(lab_pixels),
        area_fraction=len(lab_pixels) / total_pixel_count if total_pixel_count else 0.0,
        mean_lab=tuple(mean_lab.tolist()),
        median_lab=tuple(median_lab.tolist()),
        std_lab=tuple(std_lab.tolist()),
        mean_lch=mean_lch,
        delta_e00_spread=de00_spread,
        core_mean_lab=core_mean_lab,
        munsell=munsell_name,
        philatelic_name=naming_result.full_name,
        philatelic_match_delta_e00=naming_result.delta_e00,
        srgb_swatch=srgb_swatch,
        out_of_gamut=out_of_gamut,
    )


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


@dataclass
class ColourAnalysis:
    result: ColourResult
    interior_mask: np.ndarray
    excluded_mask: np.ndarray  # the final included/analysed region
    lab_image: np.ndarray
    pixel_coords: np.ndarray  # (N, 2) row,col of analysed pixels, for diagnostics
    labels: np.ndarray  # (N,) cluster index per analysed pixel
    mixed: np.ndarray  # (N,) bool
    cluster_names: dict[int, str]
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Direct ROI measurement (the GUI's primary colour workflow): the user draws
# one ROI per colour they want measured, and that colour is read directly
# from it -- no GMM clustering, since the user has already done the
# separation by hand. GMM-based `analyse_colour` below remains available
# (the CLI's default, unattended path), but the GUI builds its results this
# way instead.
# --------------------------------------------------------------------------


def measure_roi_colour(
    crop: np.ndarray,
    predict_lab_fn,
    mask: np.ndarray,
    label: str,
    total_pixel_count: int | None = None,
    philatelic_table: list[PhilatelicColourEntry] | None = None,
) -> tuple[ColourCluster | None, list[str]]:
    """Measure one user-drawn ROI directly (SPEC §8, ROI-first colour
    workflow). ``median_lab`` (already computed by ``compute_cluster_stats``)
    is the more bleed-through-robust of the two reported means: a minority
    of mixed-colour pixels at a ROI's edge pulls the mean but not the
    median. No outlier trimming beyond that -- a cleanly drawn ROI is the
    primary defence against contamination, not post-hoc statistics.
    """
    warnings: list[str] = []
    lab = rgb_to_lab_d50(crop, predict_lab_fn)
    rows, cols = np.nonzero(mask)
    if len(rows) == 0:
        return None, ["empty ROI; draw a region on the image first"]

    lab_pixels = lab[rows, cols, :]
    finite = np.all(np.isfinite(lab_pixels), axis=1)
    if not np.all(finite):
        n_dropped = int((~finite).sum())
        warnings.append(
            f"{n_dropped} pixel(s) outside the colour model's trained gamut (NaN Lab); excluded"
        )
        lab_pixels = lab_pixels[finite]

    if len(lab_pixels) < 4:
        warnings.append(f"only {len(lab_pixels)} pixel(s) in ROI; too few to measure reliably")
        return None, warnings

    cluster = compute_cluster_stats(
        label, lab_pixels, None, total_pixel_count or len(rows), philatelic_table
    )
    return cluster, warnings


def build_colour_result_from_rois(
    crop: np.ndarray,
    predict_lab_fn,
    rois: list[tuple[str, np.ndarray]],
    philatelic_table: list[PhilatelicColourEntry] | None = None,
) -> ColourResult:
    """Assemble a ``ColourResult`` from a list of ``(name, mask)`` ROIs --
    the GUI's colour workflow. Each ROI is measured independently
    (``measure_roi_colour``); a ROI that fails (empty/too few pixels) is
    skipped with its warning kept, not silently dropped."""
    warnings: list[str] = []
    clusters: list[ColourCluster] = []
    stamp_mask = stamp_mask_from_crop(crop)
    total_pixel_count = int(np.count_nonzero(stamp_mask))

    for name, mask in rois:
        cluster, roi_warnings = measure_roi_colour(
            crop, predict_lab_fn, mask, name, total_pixel_count, philatelic_table
        )
        warnings.extend(f"{name}: {w}" for w in roi_warnings)
        if cluster is not None:
            clusters.append(cluster)

    result = ColourResult(n_inks=len(clusters), clusters=clusters)
    result.meta.warnings = warnings
    result.meta.confidence = 1.0 if clusters else 0.0
    return result


def analyse_colour(
    crop: np.ndarray,
    predict_lab_fn,
    px_per_mm_x: float,
    px_per_mm_y: float,
    perforation_margin_mm: float = DEFAULT_INTERIOR_MARGIN_MM,
    n_inks: int = 1,
    tau: float = 0.9,
    user_exclusion_mask: np.ndarray | None = None,
    user_inclusion_mask: np.ndarray | None = None,
    philatelic_table: list[PhilatelicColourEntry] | None = None,
    ref_id: str | None = None,
    colour_model_cv_delta_e00: float | None = None,
) -> ColourAnalysis:
    """Full colour pipeline for one stamp crop (SPEC §8).

    There is no automatic cancellation exclusion (see the module docstring).
    When a stamp is cancelled, pass ``user_inclusion_mask`` to restrict the
    analysis to a cancellation-free area chosen by the user -- this is the
    intended way to get a clean paper/ink reading on a cancelled stamp, not
    an optional extra."""
    warnings: list[str] = []
    mask = stamp_mask_from_crop(crop)
    interior = interior_mask(mask, perforation_margin_mm, px_per_mm_x, px_per_mm_y)

    lab = rgb_to_lab_d50(crop, predict_lab_fn)
    analysed, excl_warnings = exclude_artifacts(
        crop,
        lab,
        interior,
        user_exclusion_mask=user_exclusion_mask,
    )
    warnings.extend(excl_warnings)

    if user_inclusion_mask is not None:
        analysed = cv2.bitwise_and(analysed, user_inclusion_mask)
    else:
        warnings.append(
            "no inclusion area selected; analysing the full stamp interior, which may "
            "include cancellation ink or other artifacts as 'ink'"
        )

    rows, cols = np.nonzero(analysed)
    pixel_coords = np.stack([rows, cols], axis=1)
    lab_pixels = lab[rows, cols, :]

    # Some characterisation models (e.g. the lut_delaunay option from SPEC
    # §4.3, chosen for every reference built so far) are undefined outside
    # the convex hull of the training patches and return NaN there -- by
    # design, documented in core.calibration.characterisation. Real scans
    # routinely have pixels outside that hull (dark ink, specular
    # highlights); found via a crash on a real stamp. Drop them rather than
    # feed NaN into the GMM.
    finite = np.all(np.isfinite(lab_pixels), axis=1)
    if not np.all(finite):
        n_dropped = int((~finite).sum())
        warnings.append(
            f"{n_dropped} pixel(s) outside the colour model's trained gamut (NaN Lab); excluded"
        )
        pixel_coords = pixel_coords[finite]
        lab_pixels = lab_pixels[finite]
        rows, cols = pixel_coords[:, 0], pixel_coords[:, 1]

    min_pixels = max(2 * (1 + n_inks), 50)
    if len(rows) < min_pixels:
        warnings.append(
            f"only {len(rows)} pixels in the analysed region (< {min_pixels} needed to fit "
            f"{1 + n_inks} clusters); skipping colour analysis entirely"
        )
        result = ColourResult(n_inks=n_inks, tau=tau)
        result.meta.warnings = warnings
        result.meta.confidence = 0.0
        return ColourAnalysis(
            result=result,
            interior_mask=interior,
            excluded_mask=analysed,
            lab_image=lab,
            pixel_coords=pixel_coords,
            labels=np.zeros(len(rows), dtype=int),
            mixed=np.ones(len(rows), dtype=bool),
            cluster_names={},
            warnings=warnings,
        )
    if len(rows) < 100:
        warnings.append(f"only {len(rows)} pixels in the analysed region; results unreliable")

    clustering = cluster_colours(lab_pixels, n_inks=n_inks, tau=tau)

    paper_lab_pixels = lab_pixels[(clustering.labels == clustering.paper_index) & ~clustering.mixed]
    paper_mean = paper_lab_pixels.mean(axis=0) if len(paper_lab_pixels) else None

    clusters: list[ColourCluster] = []
    cluster_lab_means: dict[str, np.ndarray] = {}
    for idx, name in sorted(clustering.cluster_names.items(), key=lambda kv: kv[0]):
        sel = (clustering.labels == idx) & ~clustering.mixed
        cluster_pixels = lab_pixels[sel]
        if len(cluster_pixels) == 0:
            warnings.append(f"cluster {name} has no non-mixed pixels")
            continue
        stats = compute_cluster_stats(name, cluster_pixels, paper_mean, len(rows), philatelic_table)
        clusters.append(stats)
        cluster_lab_means[name] = np.array(stats.mean_lab)

    ink_paper_delta_e00: dict[str, float] = {}
    if "paper" in cluster_lab_means:
        for name, lab_mean in cluster_lab_means.items():
            if name == "paper":
                continue
            ink_paper_delta_e00[name] = float(
                colour.difference.delta_E_CIE2000(lab_mean, cluster_lab_means["paper"])
            )

    mixed_fraction = float(clustering.mixed.mean()) if len(clustering.mixed) else 0.0

    result = ColourResult(
        n_inks=n_inks,
        tau=tau,
        clusters=clusters,
        mixed_pixel_fraction=mixed_fraction,
        ink_paper_delta_e00=ink_paper_delta_e00,
    )
    result.meta.params = {
        "ref_id": ref_id,
        "colour_model_cv_delta_e00": colour_model_cv_delta_e00,
        "perforation_margin_mm": perforation_margin_mm,
    }
    result.meta.warnings = warnings
    result.meta.confidence = 1.0 - mixed_fraction

    return ColourAnalysis(
        result=result,
        interior_mask=interior,
        excluded_mask=analysed,
        lab_image=lab,
        pixel_coords=pixel_coords,
        labels=clustering.labels,
        mixed=clustering.mixed,
        cluster_names=clustering.cluster_names,
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Diagnostics (SPEC §8.4's 2D view, §10's a*b*/C*-L* plots and swatch card)
# --------------------------------------------------------------------------

_CLUSTER_COLOURS = {
    "paper": (0.6, 0.6, 0.6),
    "ink1": (0.9, 0.2, 0.2),
    "ink2": (0.2, 0.5, 0.9),
    "ink3": (0.2, 0.8, 0.3),
    "ink4": (0.9, 0.7, 0.1),
}


def _cluster_colour(name: str) -> tuple[float, float, float]:
    return _CLUSTER_COLOURS.get(name, (0.5, 0.2, 0.8))


def _render_placeholder(message: str) -> bytes:
    fig, ax = plt.subplots(figsize=(5, 1.2))
    ax.text(0.5, 0.5, message, ha="center", va="center")
    ax.axis("off")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    return buf.getvalue()


def render_cluster_overlay(crop: np.ndarray, analysis: ColourAnalysis) -> bytes:
    """The stamp with the per-cluster mask overlay and the mixed-pixel
    overlay (SPEC §8.4's linked 2D view)."""
    display = crop.astype(np.float64)
    display = (display / max(display.max(), 1) * 255).astype(np.uint8)
    if display.ndim == 2:
        display = np.stack([display] * 3, axis=-1)
    overlay = display.copy()

    rows, cols = analysis.pixel_coords[:, 0], analysis.pixel_coords[:, 1]
    for idx, name in analysis.cluster_names.items():
        colour_rgb = np.array(_cluster_colour(name)) * 255
        sel = (analysis.labels == idx) & ~analysis.mixed
        overlay[rows[sel], cols[sel]] = (
            0.5 * overlay[rows[sel], cols[sel]] + 0.5 * colour_rgb
        ).astype(np.uint8)
    mixed_sel = analysis.mixed
    overlay[rows[mixed_sel], cols[mixed_sel]] = [255, 255, 0]

    fig, axes = plt.subplots(1, 2, figsize=(9, 4.5))
    axes[0].imshow(display)
    axes[0].set_title("crop")
    axes[0].axis("off")
    axes[1].imshow(overlay)
    axes[1].set_title("clusters (yellow = mixed, excluded)")
    axes[1].axis("off")
    handles = [
        mpatches.Patch(color=_cluster_colour(name), label=name)
        for name in analysis.cluster_names.values()
    ]
    handles.append(mpatches.Patch(color=(1, 1, 0), label="mixed"))
    axes[1].legend(handles=handles, loc="upper right", fontsize=7)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    return buf.getvalue()


def render_scatter_plots(analysis: ColourAnalysis) -> bytes:
    """a*b* and C*-L* scatter plots with 1-sigma/2-sigma cluster ellipses
    (SPEC §10)."""
    fig, (ax_ab, ax_cl) = plt.subplots(1, 2, figsize=(9, 4.2))

    for idx, name in analysis.cluster_names.items():
        sel = (analysis.labels == idx) & ~analysis.mixed
        lab = analysis.lab_image[analysis.pixel_coords[sel, 0], analysis.pixel_coords[sel, 1]]
        if len(lab) == 0:
            continue
        subsample = lab[
            np.random.default_rng(0).choice(len(lab), min(3000, len(lab)), replace=False)
        ]
        colour_rgb = _cluster_colour(name)
        ax_ab.scatter(
            subsample[:, 1], subsample[:, 2], s=2, color=colour_rgb, label=name, alpha=0.4
        )
        chroma = np.hypot(lab[:, 1], lab[:, 2])
        ax_cl.scatter(chroma, lab[:, 0], s=2, color=colour_rgb, alpha=0.4)

        _draw_cov_ellipse(ax_ab, lab[:, 1:3], colour_rgb)

    ax_ab.set_xlabel("a*")
    ax_ab.set_ylabel("b*")
    ax_ab.set_title("a*b* scatter")
    ax_ab.legend(fontsize=7)
    ax_ab.axhline(0, color="grey", lw=0.3)
    ax_ab.axvline(0, color="grey", lw=0.3)

    ax_cl.set_xlabel("C*")
    ax_cl.set_ylabel("L*")
    ax_cl.set_title("C*-L* scatter")

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    return buf.getvalue()


def render_roi_scatter(crop: np.ndarray, predict_lab_fn, mask: np.ndarray, label: str) -> bytes:
    """a*b* and C*-L* scatter of one ROI's own pixels (not aggregated
    cluster means) -- the per-colour plot shown under the GUI's colour
    panel when a ROI is selected."""
    rows, cols = np.nonzero(mask)
    if len(rows) == 0:
        return _render_placeholder("empty ROI")
    lab = rgb_to_lab_d50(crop, predict_lab_fn)
    lab_pixels = lab[rows, cols, :]
    finite = np.all(np.isfinite(lab_pixels), axis=1)
    lab_pixels = lab_pixels[finite]
    if len(lab_pixels) == 0:
        return _render_placeholder("no in-gamut pixels in ROI")

    subsample = lab_pixels[
        np.random.default_rng(0).choice(len(lab_pixels), min(3000, len(lab_pixels)), replace=False)
    ]
    mean_lab = lab_pixels.mean(axis=0)
    swatch_rgb, _ = lab_to_display_srgb(mean_lab)
    swatch = tuple(v / 255.0 for v in swatch_rgb)

    fig, (ax_ab, ax_cl) = plt.subplots(1, 2, figsize=(7.5, 3.6))
    ax_ab.scatter(subsample[:, 1], subsample[:, 2], s=3, color=swatch, alpha=0.4)
    _draw_cov_ellipse(ax_ab, lab_pixels[:, 1:3], swatch)
    ax_ab.set_xlabel("a*")
    ax_ab.set_ylabel("b*")
    ax_ab.set_title(f"{label}: a*b*")
    ax_ab.axhline(0, color="grey", lw=0.3)
    ax_ab.axvline(0, color="grey", lw=0.3)

    chroma = np.hypot(subsample[:, 1], subsample[:, 2])
    ax_cl.scatter(chroma, subsample[:, 0], s=3, color=swatch, alpha=0.4)
    ax_cl.set_xlabel("C*")
    ax_cl.set_ylabel("L*")
    ax_cl.set_title("C*-L*")

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    return buf.getvalue()


def render_colour_overview(entries: list[tuple[str, ColourCluster]]) -> bytes:
    """All given clusters' colours plotted together in CIELAB a*b* and
    CIELCh (C*-h, polar) -- a cross-stamp comparison, not a per-stamp
    diagnostic. Typically called with every stamp's ink cluster(s) in a
    project (SPEC §9/§10), so colours can be compared to each other
    directly rather than one stamp at a time.

    ``entries`` is ``(label, cluster)``, where ``label`` is whatever the
    caller wants shown next to the point -- e.g. ``f"{stamp_id}/{cluster.label}"``.
    """
    if not entries:
        return _render_placeholder("no clusters to plot")

    fig = plt.figure(figsize=(11, 5.2))
    ax_ab = fig.add_subplot(1, 2, 1)
    ax_lch = fig.add_subplot(1, 2, 2, projection="polar")

    plotted = 0
    for label, cluster in entries:
        if cluster.mean_lab is None or cluster.mean_lch is None:
            continue
        plotted += 1
        _lightness, a_star, b_star = cluster.mean_lab
        swatch = tuple(v / 255.0 for v in (cluster.srgb_swatch or (128, 128, 128)))

        ax_ab.scatter(a_star, b_star, s=36, color=swatch, edgecolor="black", linewidth=0.4)
        ax_ab.annotate(
            label, (a_star, b_star), fontsize=6, xytext=(3, 3), textcoords="offset points"
        )

        _mean_l, chroma, hue = cluster.mean_lch
        ax_lch.scatter(
            np.radians(hue), chroma, s=36, color=swatch, edgecolor="black", linewidth=0.4
        )

    if plotted == 0:
        plt.close(fig)
        return _render_placeholder("no clusters with Lab stats to plot")

    ax_ab.set_xlabel("a*")
    ax_ab.set_ylabel("b*")
    ax_ab.set_title("CIELAB a*b*")
    ax_ab.axhline(0, color="grey", lw=0.3)
    ax_ab.axvline(0, color="grey", lw=0.3)
    ax_ab.set_aspect("equal", adjustable="datalim")

    ax_lch.set_theta_zero_location("E")
    ax_lch.set_title("CIELCh (C*, h)", pad=20)

    fig.suptitle(f"Colour overview -- {plotted} cluster(s)")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    return buf.getvalue()


def _draw_cov_ellipse(ax, xy: np.ndarray, colour_rgb: tuple[float, float, float]) -> None:
    if len(xy) < 3:
        return
    mean = xy.mean(axis=0)
    cov = np.cov(xy, rowvar=False)
    vals, vecs = np.linalg.eigh(cov)
    order = np.argsort(vals)[::-1]
    vals, vecs = vals[order], vecs[:, order]
    angle = np.degrees(np.arctan2(vecs[1, 0], vecs[0, 0]))
    for n_std, alpha in ((1, 0.8), (2, 0.4)):
        width, height = 2 * n_std * np.sqrt(np.maximum(vals, 0))
        ellipse = mpatches.Ellipse(
            mean, width, height, angle=angle, fill=False, color=colour_rgb, alpha=alpha, lw=1.2
        )
        ax.add_patch(ellipse)


def render_swatch_card(analysis: ColourAnalysis) -> bytes:
    """A card with each cluster's swatch and numeric stats (SPEC §10)."""
    clusters = analysis.result.clusters
    if not clusters:
        return _render_placeholder("no clusters (see warnings)")
    fig, axes = plt.subplots(len(clusters), 1, figsize=(11, 1.3 * len(clusters)))
    axes = np.atleast_1d(axes)
    for ax, cluster in zip(axes, clusters, strict=True):
        swatch = np.array(cluster.srgb_swatch, dtype=np.uint8).reshape(1, 1, 3)
        ax.imshow(np.tile(swatch, (10, 10, 1)), extent=(0, 1, 0, 1))
        lch = cluster.mean_lch or (0, 0, 0)
        gamut_note = " (out of gamut)" if cluster.out_of_gamut else ""
        text = (
            f"{cluster.label}: L*={lch[0]:.1f} C*={lch[1]:.1f} h={lch[2]:.1f}deg{gamut_note}\n"
            f"Lab=({cluster.mean_lab[0]:.1f}, {cluster.mean_lab[1]:.1f}, "
            f"{cluster.mean_lab[2]:.1f})  dE00 spread={cluster.delta_e00_spread:.2f}  "
            f"area={cluster.area_fraction * 100:.1f}%\n"
            f"Munsell: {cluster.munsell or 'n/a'}   {cluster.philatelic_name or 'n/a'}"
        )
        ax.text(1.15, 0.5, text, fontsize=7, va="center", transform=ax.transAxes)
        ax.set_xlim(0, 4)
        ax.axis("off")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
