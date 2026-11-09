"""The published form of a project: a stable, documented export for other
software -- a website, a notebook -- that never has to read the library's
internal files (SPEC §3.2; format in ``docs/published-format.md``).

```
<library_root>/published.json                 index of all published projects
<library_root>/projects/<project_id>/published/
  project.json                                everything about every stamp
  table.csv                                   the overview table
  stamps/<stamp_id>.webp                      full-scale stamp picture
  stamps/<stamp_id>_thumb.webp                thumbnail
```

Rewritten after every change to a project. The JSON is cheap to rebuild;
stamp pictures are re-rendered only when what they depend on (the stamp's
geometry, its sheet's reference and colour model) changed, tracked in
``published/.image_sources.json``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

from stampan import __version__
from stampan.core import colour as colour_mod
from stampan.core import overview as overview_mod
from stampan.core import perforation as perforation_mod
from stampan.core import project as project_mod
from stampan.core.calibration import reference as ref_mod
from stampan.core.models import ColourCluster, PerforationSide, Stamp

SCHEMA = "stampan.published/1"
PUBLISHED_DIR = "published"
INDEX_FILE = "published.json"
THUMBNAIL_PX = 400
WEBP_QUALITY = 92
#: Bump to re-render every published picture (e.g. after changing how they
#: are rendered).
_RENDER_VERSION = 1
_IMAGE_SOURCES = ".image_sources.json"


@dataclass
class PublishResult:
    path: Path  # the project's published/ directory
    n_stamps: int
    n_images_rendered: int


def published_dir(library_root: str | Path, project_id: str) -> Path:
    return project_mod.project_dir(library_root, project_id) / PUBLISHED_DIR


def _round(values, digits: int = 2):
    if values is None:
        return None
    if isinstance(values, (int, float)):
        return round(float(values), digits)
    return [round(float(v), digits) for v in values]


# -- records ------------------------------------------------------------------


def _side_record(side: PerforationSide | None) -> dict | None:
    if side is None:
        return None
    record = {"kind": side.kind, "gauge": _round(side.gauge), "notation": side.notation}
    if side.uncertainty is not None:
        record["uncertainty"] = _round(side.uncertainty)
    if side.hole_count is not None:
        record["holes"] = side.hole_count
    return record


def _perforation_record(stamp: Stamp) -> dict | None:
    perf = stamp.perforation
    effective = perf.effective()
    if not effective:
        return None
    sides = {}
    for name in perforation_mod.SIDES:
        if name not in effective:
            continue
        sides[name] = {
            **_side_record(effective[name]),
            "by_hand": name in perf.manual,
            "measured": _side_record(perf.auto.get(name)),
        }
    return {
        "notation": perforation_mod.perforation_result_notation(effective),
        "by_hand": bool(perf.manual),
        "reason": next(iter(perf.manual_reason.values()), "") or None,
        "confidence": _round(perf.meta.confidence),
        "warnings": list(perf.meta.warnings or []),
        "sides": sides,
    }


def _colour_record(c: ColourCluster) -> dict:
    return {
        "label": c.label,
        "display_label": c.overview_label,
        "is_paper": c.label.strip().lower() == "paper",
        "in_overview": c.overview_included,
        "lab": _round(c.mean_lab),
        "lab_std": _round(c.std_lab),
        "lch": _round(c.mean_lch),
        "delta_e00_spread": _round(c.delta_e00_spread),
        "pixel_count": c.pixel_count,
        "philatelic_name": c.philatelic_name,
        "philatelic_match_delta_e00": _round(c.philatelic_match_delta_e00),
        "munsell": c.munsell,
        "srgb": "#{:02x}{:02x}{:02x}".format(*c.srgb_swatch) if c.srgb_swatch else None,
        "out_of_gamut": c.out_of_gamut,
    }


def _stamp_record(stamp: Stamp, sample_id: str, number: int, image: dict | None) -> dict:
    g = stamp.geometry
    c = stamp.cancellation
    return {
        "id": stamp.stamp_id,
        "sheet": sample_id,
        "number": number,
        "label": project_mod.stamp_label(stamp.stamp_id),
        "catalogue": list(stamp.catalogue_numbers),
        "country_issue": stamp.country_issue,
        "notes": stamp.notes or None,
        "condition": stamp.condition,
        "multiplicity": stamp.multiplicity,
        "multiplicity_count": stamp.multiplicity_count,
        "size_mm": [_round(g.width_mm), _round(g.height_mm)] if g.width_mm else None,
        "cancellation": {"type": c.type, "date": c.date, "estimated": c.date_estimated},
        "perforation": _perforation_record(stamp),
        "colours": [_colour_record(cl) for cl in stamp.colour.clusters if cl.mean_lab is not None],
        **(image or {"image": None, "thumbnail": None, "image_calibrated": None}),
    }


def _reference_record(reference) -> dict:
    model = reference.colour_model
    record = {
        "id": reference.ref_id,
        "name": reference.name,
        "created": reference.created,
        "resolution_dpi": int(float(reference.scanner_options["resolution"]))
        if reference.scanner_options.get("resolution")
        else None,
        "px_per_mm": [
            _round(reference.scale.px_per_mm_x, 3),
            _round(reference.scale.px_per_mm_y, 3),
        ]
        if reference.scale
        else None,
        "colour_model": None,
    }
    if model is not None:
        record["colour_model"] = {
            "kind": model.kind,
            "cv_delta_e00_mean": _round(model.cv_delta_e00.get("mean")),
            "cv_delta_e00_p95": _round(model.cv_delta_e00.get("p95")),
        }
    return record


# -- pictures -----------------------------------------------------------------


def _image_fingerprint(library_root, stamp: Stamp, ref_id: str | None) -> str:
    model_path = Path(library_root) / "references" / str(ref_id) / "model.npz"
    source = {
        "render": _RENDER_VERSION,
        "geometry": asdict(stamp.geometry),
        "ref_id": ref_id,
        "model": model_path.stat().st_mtime_ns if model_path.exists() else None,
    }
    return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()[:16]


def _write_webp(path: Path, rgb: np.ndarray) -> None:
    ok, buf = cv2.imencode(
        ".webp", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_WEBP_QUALITY, WEBP_QUALITY]
    )
    if not ok:
        raise RuntimeError(f"could not encode {path.name}")
    partial = path.with_name(path.name + ".partial")
    partial.write_bytes(buf.tobytes())
    partial.replace(path)


def render_stamp_picture(crop: np.ndarray, model) -> tuple[np.ndarray, bool]:
    """Display sRGB of a stamp crop, through the reference's colour model
    (scanner RGB -> Lab D50 -> sRGB) when there is one. Without a model it
    is only a per-channel stretch, and the second value says so."""
    if model is None:
        return cv2.normalize(crop, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U), False
    lab = colour_mod.rgb_to_lab_d50(crop, model.predict_lab)
    return colour_mod.lab_image_to_display_rgb(lab), True


# -- publishing ---------------------------------------------------------------


def publish_project(
    library_root: str | Path,
    project_id: str,
    progress: Callable[[str], None] | None = None,
) -> PublishResult:
    """(Re)write a project's published/ directory and the library index."""
    root = Path(library_root)
    project = project_mod.load_project(root, project_id)
    out = published_dir(root, project_id)
    (out / "stamps").mkdir(parents=True, exist_ok=True)

    sources_path = out / _IMAGE_SOURCES
    try:
        old_sources = json.loads(sources_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        old_sources = {}
    sources: dict[str, dict] = {}

    references, models = {}, {}
    sheets, stamps = [], []
    n_rendered = 0
    for sample_id in project.sample_ids:
        sample = project_mod.load_sample(root, project_id, sample_id)
        if sample.ref_id not in references:
            reference = ref_mod.load_reference(root, sample.ref_id)
            references[sample.ref_id] = _reference_record(reference)
            models[sample.ref_id] = (
                ref_mod.load_colour_model(root, sample.ref_id) if reference.colour_model else None
            )
        sheet_stamps = []
        for stamp_id in sample.stamp_ids:
            stamp = project_mod.load_stamp(root, project_id, stamp_id)
            number = int(project_mod.stamp_label(stamp_id).rsplit(" ", 1)[-1])
            fingerprint = _image_fingerprint(root, stamp, sample.ref_id)
            image_path = out / "stamps" / f"{stamp_id}.webp"
            thumb_path = out / "stamps" / f"{stamp_id}_thumb.webp"
            previous = old_sources.get(stamp_id, {})
            if (
                previous.get("fingerprint") != fingerprint
                or not image_path.exists()
                or not thumb_path.exists()
            ):
                if progress:
                    progress(f"Rendering picture of {project_mod.stamp_label(stamp_id)}…")
                crop = project_mod.load_stamp_crop(root, project_id, stamp_id)
                rgb, calibrated = render_stamp_picture(crop, models[sample.ref_id])
                _write_webp(image_path, rgb)
                scale = THUMBNAIL_PX / max(rgb.shape[:2])
                thumb = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                _write_webp(thumb_path, thumb)
                previous = {
                    "fingerprint": fingerprint,
                    "calibrated": calibrated,
                    "size_px": [int(rgb.shape[1]), int(rgb.shape[0])],
                }
                n_rendered += 1
            sources[stamp_id] = previous
            image = {
                "image": f"stamps/{stamp_id}.webp",
                "thumbnail": f"stamps/{stamp_id}_thumb.webp",
                "image_size_px": previous.get("size_px"),
                "image_calibrated": previous.get("calibrated"),
            }
            stamps.append(_stamp_record(stamp, sample_id, number, image))
            sheet_stamps.append(stamp_id)
        sheets.append(
            {
                "id": sample_id,
                "title": project_mod.sheet_title(sample),
                "scanned": sample.scan_time,
                "reference": sample.ref_id,
                "stamps": sheet_stamps,
            }
        )

    # Pictures of stamps that no longer exist.
    for path in (out / "stamps").glob("*.webp"):
        if path.stem.removesuffix("_thumb") not in sources:
            path.unlink()

    now = datetime.now(UTC).isoformat(timespec="seconds")
    document = {
        "schema": SCHEMA,
        "generator": f"Stamp Analyzer {__version__}",
        "project": {
            "id": project.project_id,
            "name": project.name,
            "created": project.created,
            "notes": project.notes or None,
            "updated": now,
        },
        "references": list(references.values()),
        "sheets": sheets,
        "stamps": stamps,
        "table": "table.csv",
    }
    project_mod.write_text_atomic(
        out / "project.json", json.dumps(document, indent=2, ensure_ascii=False)
    )
    overview_mod.write_csv(out / "table.csv", overview_mod.project_entries(root, project))
    project_mod.write_text_atomic(sources_path, json.dumps(sources, indent=2, sort_keys=True))
    _update_index(root, project.project_id, project.name, now, len(stamps))
    return PublishResult(path=out, n_stamps=len(stamps), n_images_rendered=n_rendered)


def _update_index(root: Path, project_id: str, name: str, updated: str, n_stamps: int) -> None:
    """``published.json``: every project that has a published/ directory."""
    path = root / INDEX_FILE
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        index = {}
    entries = {e["id"]: e for e in index.get("projects", [])}
    entries[project_id] = {
        "id": project_id,
        "name": name,
        "path": f"projects/{project_id}/{PUBLISHED_DIR}/project.json",
        "updated": updated,
        "stamps": n_stamps,
    }
    existing = {e for e in entries if (project_mod.project_dir(root, e) / PUBLISHED_DIR).is_dir()}
    projects = sorted((entries[e] for e in existing), key=lambda e: e["name"].lower())
    project_mod.write_text_atomic(
        path, json.dumps({"schema": SCHEMA, "projects": projects}, indent=2, ensure_ascii=False)
    )


def remove_from_index(library_root: str | Path, project_id: str) -> None:
    """Drop a deleted project from ``published.json``."""
    path = Path(library_root) / INDEX_FILE
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return
    index["projects"] = [e for e in index.get("projects", []) if e["id"] != project_id]
    project_mod.write_text_atomic(path, json.dumps(index, indent=2, ensure_ascii=False))
