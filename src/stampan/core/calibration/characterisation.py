"""Scanner RGB -> CIE XYZ(D50) characterisation and validation (SPEC §4.3).

Models compared, all fit from the 288 IT8 patches:

  (a) a 3x3 matrix (``matrix3x3``) -- the baseline;
  (b) root-polynomial regression, degree 2 and 3 (``root_poly2``,
      ``root_poly3``), via Finlayson et al. (2015) as implemented in
      ``colour.characterisation``;
  (d) piecewise-linear (Delaunay) interpolation straight to Lab
      (``lut_delaunay``), via ``scipy.interpolate.LinearNDInterpolator`` --
      a reference point with no smoothing and undefined outside the convex
      hull of the training patches.

(c) an optional ArgyllCMS cross-check (``colprof``/``cctiff``) is a design
hook only: it is used when the `colprof`/`cctiff` binaries are on PATH, and
silently skipped (with a note, not an error) otherwise, since ArgyllCMS is
an optional system package per CLAUDE.md.

The legacy `epson1200u.icc` LUT profile (SPEC §13) can be scored for
comparison only via :func:`predict_lab_icc_profile` -- it is never a
candidate model, since its scan settings are unknown.

Degree-3 root-polynomial is only selected over degree-2 if it beats it
*clearly* on cross-validated mean dE00 (SPEC §4.3); "clearly" is defined
here as more than ``DEGREE3_MARGIN`` dE00, to avoid rewarding overfitting
noise.
"""

from __future__ import annotations

import io
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import colour
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from colour.characterisation import (
    apply_matrix_colour_correction_Finlayson2015,
    matrix_colour_correction_Finlayson2015,
)
from scipy.interpolate import LinearNDInterpolator, PchipInterpolator
from scipy.spatial import Delaunay

from stampan.core.cgats import lab_from_xyz_d50

DEGREE3_MARGIN = 0.1  # dE00; degree 3 must beat degree 2 by more than this

ModelKind = Literal["matrix3x3", "root_poly2", "root_poly3", "lut_delaunay", "icc_crosscheck"]


# --------------------------------------------------------------------------
# Grey-ramp linearisation
# --------------------------------------------------------------------------


@dataclass
class LinearisationCurve:
    """Per-channel monotone RGB -> linear-RGB mapping fit from the grey ramp."""

    channel_splines: list[PchipInterpolator]  # length 3, one per R/G/B

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        rgb = np.asarray(rgb, dtype=np.float64)
        out = np.empty_like(rgb)
        for c in range(3):
            out[..., c] = self.channel_splines[c](rgb[..., c])
        return out


def fit_linearisation(gs_rgb: np.ndarray, gs_y: np.ndarray) -> LinearisationCurve:
    """Fit a monotone spline per channel mapping raw grey-ramp RGB to a
    linear target derived from the patches' XYZ_Y (normalised to [0, 1]).

    ``gs_rgb`` is (N, 3) raw channel values for the GS0..GS23 patches (grey,
    so R~=G~=B), ``gs_y`` is their (N,) XYZ_Y.
    """
    order = np.argsort(gs_rgb.mean(axis=1))
    target = gs_y[order] / gs_y.max()
    splines = []
    for c in range(3):
        raw_sorted = gs_rgb[order, c]
        # PchipInterpolator requires strictly increasing x.
        raw_sorted, unique_idx = np.unique(raw_sorted, return_index=True)
        splines.append(PchipInterpolator(raw_sorted, target[unique_idx], extrapolate=True))
    return LinearisationCurve(channel_splines=splines)


# --------------------------------------------------------------------------
# Models (a), (b): matrix + root-polynomial, via colour-science Finlayson2015
# --------------------------------------------------------------------------


@dataclass
class MatrixModel:
    kind: ModelKind
    degree: int
    ccm: np.ndarray

    def predict_xyz(self, rgb: np.ndarray) -> np.ndarray:
        return apply_matrix_colour_correction_Finlayson2015(rgb, self.ccm, degree=self.degree)


def fit_matrix3x3(rgb: np.ndarray, xyz: np.ndarray) -> MatrixModel:
    ccm = matrix_colour_correction_Finlayson2015(rgb, xyz, degree=1)
    return MatrixModel(kind="matrix3x3", degree=1, ccm=ccm)


def fit_root_polynomial(rgb: np.ndarray, xyz: np.ndarray, degree: Literal[2, 3]) -> MatrixModel:
    ccm = matrix_colour_correction_Finlayson2015(
        rgb, xyz, degree=degree, root_polynomial_expansion=True
    )
    kind: ModelKind = "root_poly2" if degree == 2 else "root_poly3"
    return MatrixModel(kind=kind, degree=degree, ccm=ccm)


# --------------------------------------------------------------------------
# Model (d): LinearNDInterpolator RGB -> Lab
# --------------------------------------------------------------------------


@dataclass
class DelaunayLabModel:
    interpolator: LinearNDInterpolator
    hull: Delaunay

    def predict_lab(self, rgb: np.ndarray) -> np.ndarray:
        """Predict Lab; NaN rows mean ``rgb`` fell outside the training hull."""
        return self.interpolator(rgb)

    def in_hull(self, rgb: np.ndarray) -> np.ndarray:
        return self.hull.find_simplex(rgb) >= 0


def fit_linear_nd_interpolator(rgb: np.ndarray, lab: np.ndarray) -> DelaunayLabModel:
    hull = Delaunay(rgb)
    interpolator = LinearNDInterpolator(hull, lab)
    return DelaunayLabModel(interpolator=interpolator, hull=hull)


# --------------------------------------------------------------------------
# Cross-check (c): optional ArgyllCMS
# --------------------------------------------------------------------------


def argyllcms_available() -> bool:
    return shutil.which("colprof") is not None and shutil.which("cctiff") is not None


@dataclass
class ArgyllCrossCheck:
    available: bool
    delta_e00_stats: dict[str, float] | None = None
    note: str = ""


def run_argyllcms_crosscheck(*_args, **_kwargs) -> ArgyllCrossCheck:
    """Build an ICC input profile with `colprof` from the patch set and score
    it, as an external cross-check. Design hook: requires a `.ti3` measurement
    file and a working ArgyllCMS install, neither of which is set up yet.
    Returns ``available=False`` with a note when ArgyllCMS is not installed.
    """
    if not argyllcms_available():
        return ArgyllCrossCheck(
            available=False,
            note="ArgyllCMS (colprof/cctiff) not found on PATH; skipping cross-check.",
        )
    return ArgyllCrossCheck(
        available=False,
        note="ArgyllCMS is installed but the .ti3-building pipeline is not implemented yet.",
    )


# --------------------------------------------------------------------------
# Legacy ICC LUT profile (comparison only, never the reference)
# --------------------------------------------------------------------------


def predict_lab_icc_profile(rgb_uint8: np.ndarray, icc_path: str | Path) -> np.ndarray:
    """Apply an 8-bit RGB -> Lab ICC transform (e.g. the legacy
    epson1200u.icc LUT profile) via LittleCMS (through Pillow's ImageCms).

    ``rgb_uint8`` is (N, 3) in [0, 255], interpreted as the profile's own
    device RGB -- i.e. this is only meaningful if ``rgb_uint8`` was produced
    under the same (unknown) scan settings the profile was built from. For
    comparison only; this profile is never used as the actual reference
    model (SPEC §13).
    """
    from PIL import Image, ImageCms

    profile = ImageCms.ImageCmsProfile(str(icc_path))
    lab_profile = ImageCms.createProfile("LAB")
    transform = ImageCms.buildTransformFromOpenProfiles(profile, lab_profile, "RGB", "LAB")

    rgb_uint8 = np.clip(np.asarray(rgb_uint8), 0, 255).astype(np.uint8)
    n = rgb_uint8.shape[0]
    img = Image.frombuffer("RGB", (n, 1), rgb_uint8.tobytes(), "raw", "RGB", 0, 1)
    out = ImageCms.applyTransform(img, transform)
    lab_bytes = np.frombuffer(out.tobytes(), dtype=np.uint8).reshape(n, 3).astype(np.float64)

    lab = np.empty_like(lab_bytes)
    lab[:, 0] = lab_bytes[:, 0] * 100.0 / 255.0
    lab[:, 1] = lab_bytes[:, 1] - 128.0
    lab[:, 2] = lab_bytes[:, 2] - 128.0
    return lab


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def xyz_to_lab_array(xyz: np.ndarray) -> np.ndarray:
    out = np.empty_like(xyz)
    for i in range(xyz.shape[0]):
        out[i] = lab_from_xyz_d50(xyz[i, 0], xyz[i, 1], xyz[i, 2])
    return out


def delta_e00(lab_a: np.ndarray, lab_b: np.ndarray) -> np.ndarray:
    return colour.difference.delta_E_CIE2000(lab_a, lab_b)


@dataclass
class DeltaE00Stats:
    mean: float
    median: float
    p95: float
    max: float

    def to_dict(self) -> dict[str, float]:
        return {"mean": self.mean, "median": self.median, "p95": self.p95, "max": self.max}


def delta_e00_stats(lab_true: np.ndarray, lab_pred: np.ndarray) -> DeltaE00Stats:
    valid = np.all(np.isfinite(lab_pred), axis=-1)
    de = delta_e00(lab_true[valid], lab_pred[valid])
    return DeltaE00Stats(
        mean=float(np.mean(de)),
        median=float(np.median(de)),
        p95=float(np.percentile(de, 95)),
        max=float(np.max(de)),
    )


@dataclass
class ModelReport:
    kind: ModelKind
    train: DeltaE00Stats
    cv: DeltaE00Stats
    per_patch_cv_delta_e00: dict[str, float] = field(default_factory=dict)


def _fit_predict_lab(kind: ModelKind, rgb_train, xyz_train, rgb_test, degree: int = 1):
    if kind in ("matrix3x3", "root_poly2", "root_poly3"):
        model = (
            fit_matrix3x3(rgb_train, xyz_train)
            if kind == "matrix3x3"
            else fit_root_polynomial(rgb_train, xyz_train, degree=degree)
        )
        xyz_pred = model.predict_xyz(rgb_test)
        return xyz_to_lab_array(xyz_pred)
    if kind == "lut_delaunay":
        lab_train = xyz_to_lab_array(xyz_train)
        model = fit_linear_nd_interpolator(rgb_train, lab_train)
        return model.predict_lab(rgb_test)
    raise ValueError(f"unknown model kind: {kind}")


def leave_one_out_cross_validate(
    kind: ModelKind,
    rgb: np.ndarray,
    xyz: np.ndarray,
    patch_ids: list[str],
    degree: int = 1,
) -> tuple[DeltaE00Stats, dict[str, float]]:
    """Leave-one-out cross-validation: realistic because it never scores a
    patch using a model that was trained on it."""
    n = rgb.shape[0]
    lab_true_all = xyz_to_lab_array(xyz)
    lab_pred = np.full((n, 3), np.nan)

    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        pred = _fit_predict_lab(kind, rgb[mask], xyz[mask], rgb[i : i + 1], degree=degree)
        lab_pred[i] = pred[0]

    valid = np.all(np.isfinite(lab_pred), axis=-1)
    de = delta_e00(lab_true_all[valid], lab_pred[valid])
    per_patch: dict[str, float] = {}
    j = 0
    for i, pid in enumerate(patch_ids):
        if valid[i]:
            per_patch[pid] = float(de[j])
            j += 1

    stats = DeltaE00Stats(
        mean=float(np.mean(de)),
        median=float(np.median(de)),
        p95=float(np.percentile(de, 95)),
        max=float(np.max(de)),
    )
    return stats, per_patch


def evaluate_model(
    kind: ModelKind,
    rgb: np.ndarray,
    xyz: np.ndarray,
    patch_ids: list[str],
    degree: int = 1,
) -> ModelReport:
    lab_true = xyz_to_lab_array(xyz)
    lab_pred_train = _fit_predict_lab(kind, rgb, xyz, rgb, degree=degree)
    train_stats = delta_e00_stats(lab_true, lab_pred_train)
    cv_stats, per_patch = leave_one_out_cross_validate(kind, rgb, xyz, patch_ids, degree=degree)
    return ModelReport(kind=kind, train=train_stats, cv=cv_stats, per_patch_cv_delta_e00=per_patch)


def choose_best_model(reports: dict[ModelKind, ModelReport]) -> ModelKind:
    """Pick the model with the best cross-validated mean dE00, without
    rewarding degree-3 overfitting (SPEC §4.3): degree 3 only wins over
    degree 2 if it beats it by more than DEGREE3_MARGIN."""
    candidates = dict(reports)
    if "root_poly2" in candidates and "root_poly3" in candidates:
        improvement = candidates["root_poly2"].cv.mean - candidates["root_poly3"].cv.mean
        if improvement < DEGREE3_MARGIN:
            del candidates["root_poly3"]
    return min(candidates, key=lambda k: candidates[k].cv.mean)


def render_error_heatmap(
    per_patch_delta_e00: dict[str, float], rows: str, n_cols: int, n_gs: int
) -> bytes:
    grid = np.full((len(rows), n_cols), np.nan)
    for ri, row_letter in enumerate(rows):
        for ci in range(n_cols):
            grid[ri, ci] = per_patch_delta_e00.get(f"{row_letter}{ci + 1}", np.nan)
    gs = np.array([per_patch_delta_e00.get(f"GS{i}", np.nan) for i in range(n_gs)])

    fig, (ax_main, ax_gs) = plt.subplots(
        2, 1, figsize=(8, 5), gridspec_kw={"height_ratios": [10, 1.2]}
    )
    im = ax_main.imshow(grid, cmap="inferno", aspect="auto")
    ax_main.set_yticks(range(len(rows)), list(rows))
    ax_main.set_xticks(range(n_cols), [str(i + 1) for i in range(n_cols)])
    ax_main.set_title("per-patch cross-validated dE00")
    fig.colorbar(im, ax=ax_main, fraction=0.03)
    ax_gs.imshow(gs[None, :], cmap="inferno", aspect="auto")
    ax_gs.set_yticks([])
    ax_gs.set_xticks(range(n_gs), [str(i) for i in range(n_gs)])
    ax_gs.set_title("GS row")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    return buf.getvalue()
