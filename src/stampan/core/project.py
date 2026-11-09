"""Project/sample/stamp persistence (SPEC §3.1).

```
<library_root>/projects/<project_id>/
  project.json
  sample_<n>.tif
  sample_<n>.json
  stamps/<stamp_id>/
    crop.tif            cache: re-cut from the sheet scan when missing
    stamp.json
    colour_rois/*.png
    diagnostics/*.png
  published/            see core.publish
```

What is the record, what is raw data and what is a rebuildable cache is
spelled out in ``LIBRARY_GITIGNORE`` (SPEC §3.1).
"""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from stampan.core.io import ScanMetadata, read_tiff, write_tiff
from stampan.core.models import Project, Sample, Stamp


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "project"


def sheet_label(sample_id: str) -> str:
    """'sample_003' -> 'Sheet 3' (falls back to the id itself)."""
    m = re.fullmatch(r"sample_(\d+)", sample_id)
    return f"Sheet {int(m.group(1))}" if m else sample_id


def sheet_title(sample) -> str:
    """The sheet's own name if the user gave it one, else "Sheet <n>"."""
    return sample.name or sheet_label(sample.sample_id)


def stamp_label(stamp_id: str) -> str:
    """'sample_003_stamp007' -> 'Stamp 8' (1-based within its sheet)."""
    m = re.search(r"_stamp(\d+)$", stamp_id)
    return f"Stamp {int(m.group(1)) + 1}" if m else stamp_id


def write_text_atomic(path: Path, text: str) -> None:
    """Write via a temporary file and rename, so a reader (the background
    publisher, another program, git) never sees a half-written record."""
    partial = path.with_name(path.name + ".partial")
    partial.write_text(text, encoding="utf-8")
    partial.replace(path)


def project_dir(library_root: str | Path, project_id: str) -> Path:
    return Path(library_root) / "projects" / project_id


def create_project(library_root: str | Path, name: str, notes: str = "") -> Project:
    project_id = f"{_slugify(name)}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    project = Project(
        project_id=project_id,
        name=name,
        created=datetime.now(UTC).date().isoformat(),
        notes=notes,
    )
    save_project(library_root, project)
    return project


def save_project(library_root: str | Path, project: Project) -> Path:
    p_dir = project_dir(library_root, project.project_id)
    p_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(p_dir / "project.json", project.to_json())
    return p_dir


def load_project(library_root: str | Path, project_id: str) -> Project:
    p_dir = project_dir(library_root, project_id)
    return Project.from_json((p_dir / "project.json").read_text(encoding="utf-8"))


def list_projects(library_root: str | Path) -> list[Project]:
    projects_root = Path(library_root) / "projects"
    if not projects_root.exists():
        return []
    projects = []
    for p_dir in sorted(projects_root.iterdir()):
        project_json = p_dir / "project.json"
        if project_json.exists():
            projects.append(Project.from_json(project_json.read_text(encoding="utf-8")))
    return projects


def find_project_by_name(library_root: str | Path, name: str) -> Project | None:
    for project in list_projects(library_root):
        if project.name == name:
            return project
    return None


def get_or_create_project(library_root: str | Path, name: str, notes: str = "") -> Project:
    existing = find_project_by_name(library_root, name)
    return existing if existing is not None else create_project(library_root, name, notes)


def add_sample(
    library_root: str | Path,
    project: Project,
    scan: np.ndarray,
    scan_metadata: ScanMetadata,
    ref_id: str,
) -> Sample:
    """Copy a sample scan into the project directory and register it."""
    p_dir = project_dir(library_root, project.project_id)
    p_dir.mkdir(parents=True, exist_ok=True)

    scan_metadata.ref_id = ref_id

    sample_id = next_sample_id(library_root, project)
    scan_path = f"{sample_id}.tif"
    write_tiff(p_dir / scan_path, scan.astype(np.uint16), scan_metadata)

    sample = Sample(
        sample_id=sample_id,
        project_id=project.project_id,
        scan_path=scan_path,
        ref_id=ref_id,
        scan_time=scan_metadata.scan_time,
    )
    save_sample(library_root, sample)

    project.sample_ids.append(sample_id)
    save_project(library_root, project)
    return sample


def save_sample(library_root: str | Path, sample: Sample) -> Path:
    p_dir = project_dir(library_root, sample.project_id)
    path = p_dir / f"{sample.sample_id}.json"
    write_text_atomic(path, sample.to_json())
    return path


def load_sample(library_root: str | Path, project_id: str, sample_id: str) -> Sample:
    p_dir = project_dir(library_root, project_id)
    return Sample.from_json((p_dir / f"{sample_id}.json").read_text(encoding="utf-8"))


def load_sample_scan(
    library_root: str | Path, project_id: str, sample: Sample
) -> tuple[np.ndarray, ScanMetadata]:
    p_dir = project_dir(library_root, project_id)
    return read_tiff(p_dir / sample.scan_path)


def save_stamp(
    library_root: str | Path,
    sample: Sample,
    stamp: Stamp,
    crop: np.ndarray,
    diagnostics: dict[str, bytes] | None = None,
) -> Path:
    """Write a stamp's crop.tif, stamp.json and diagnostics, and register it
    on ``sample`` (caller must still persist the updated sample)."""
    stamp_dir = project_dir(library_root, sample.project_id) / "stamps" / stamp.stamp_id
    (stamp_dir / "diagnostics").mkdir(parents=True, exist_ok=True)

    _write_crop(stamp_dir, crop)
    stamp.crop_path = f"stamps/{stamp.stamp_id}/crop.tif"
    write_text_atomic(stamp_dir / "stamp.json", stamp.to_json())

    for filename, data in (diagnostics or {}).items():
        (stamp_dir / "diagnostics" / filename).write_bytes(data)

    if stamp.stamp_id not in sample.stamp_ids:
        sample.stamp_ids.append(stamp.stamp_id)
    return stamp_dir


def save_stamp_results(
    library_root: str | Path,
    project_id: str,
    stamp: Stamp,
    diagnostics: dict[str, bytes] | None = None,
) -> Path:
    """Update an existing stamp's stamp.json (and diagnostics) in place,
    without touching its crop.tif -- for analysis steps (perforation,
    colour, ...) that run on an already-extracted crop."""
    stamp_dir = project_dir(library_root, project_id) / "stamps" / stamp.stamp_id
    (stamp_dir / "diagnostics").mkdir(parents=True, exist_ok=True)
    write_text_atomic(stamp_dir / "stamp.json", stamp.to_json())
    for filename, data in (diagnostics or {}).items():
        (stamp_dir / "diagnostics" / filename).write_bytes(data)
    return stamp_dir


def load_stamp(library_root: str | Path, project_id: str, stamp_id: str) -> Stamp:
    stamp_dir = project_dir(library_root, project_id) / "stamps" / stamp_id
    return Stamp.from_json((stamp_dir / "stamp.json").read_text(encoding="utf-8"))


def _write_crop(stamp_dir: Path, crop: np.ndarray) -> None:
    # Written to a temporary name and renamed: a crop being rebuilt by a
    # background task must never be read half-written.
    partial = stamp_dir / "crop.partial.tif"
    write_tiff(partial, crop.astype(np.uint16))
    partial.replace(stamp_dir / "crop.tif")


def load_stamp_crop(library_root: str | Path, project_id: str, stamp_id: str) -> np.ndarray:
    """The stamp's 16-bit crop. crop.tif is a cache: when it is missing (a
    fresh clone of the library, or a cleaned-up disk) it is re-cut from the
    sheet scan with the stored geometry."""
    path = project_dir(library_root, project_id) / "stamps" / stamp_id / "crop.tif"
    if not path.exists():
        return rebuild_stamp_crop(library_root, project_id, stamp_id)
    image, _ = read_tiff(path)
    return image


def rebuild_stamp_crop(library_root: str | Path, project_id: str, stamp_id: str) -> np.ndarray:
    """Re-cut a stamp's crop from its sheet scan exactly as it was first
    extracted: same centre, size, rotation and margin."""
    from stampan.core import detection
    from stampan.core.calibration import reference as ref_mod

    stamp = load_stamp(library_root, project_id, stamp_id)
    sample = load_sample(
        library_root, project_id, find_sample_for_stamp(library_root, project_id, stamp_id)
    )
    g = stamp.geometry
    reference = ref_mod.load_reference(library_root, sample.ref_id)
    if g.sample_centre_px is None or not g.width_mm or not g.height_mm or reference.scale is None:
        raise FileNotFoundError(
            f"{stamp_id}: crop.tif is missing and cannot be rebuilt (no stored geometry or scale)"
        )
    sx, sy = reference.scale.px_per_mm_x, reference.scale.px_per_mm_y
    scan, _ = load_sample_scan(library_root, project_id, sample)
    size = (g.width_mm * sx, g.height_mm * sy)
    placed = detection.DetectedStamp(
        index=0,
        centre_px=tuple(g.sample_centre_px),
        size_px=size,
        angle_deg=g.rotation_deg or 0.0,
        area_px=size[0] * size[1],
        fill_ratio=1.0,
        confidence=1.0,
    )
    crop, _ = detection.extract_crop(
        scan, placed, sx, sy, background_level=detection.background_level(scan)
    )
    stamp_dir = project_dir(library_root, project_id) / "stamps" / stamp_id
    stamp_dir.mkdir(parents=True, exist_ok=True)
    _write_crop(stamp_dir, crop)
    return crop


def _trailing_number(identifier: str) -> int:
    m = re.search(r"(\d+)$", identifier)
    return int(m.group(1)) if m else -1


def next_sample_id(library_root: str | Path, project: Project) -> str:
    """One past the highest sheet number ever used in this project. Counting
    the sheets instead would reuse a deleted sheet's id and overwrite another
    sheet's files."""
    p_dir = project_dir(library_root, project.project_id)
    used = [_trailing_number(s) for s in project.sample_ids]
    used += [_trailing_number(p.stem) for p in p_dir.glob("sample_*.json")]
    return f"sample_{max(used, default=0) + 1:03d}"


def next_stamp_id(sample: Sample, index: int | None = None) -> str:
    """``index`` for a fresh sheet's n-th stamp; omitted, one past the
    highest stamp number on the sheet (so a removed stamp's id is never
    reused)."""
    if index is None:
        index = max((_trailing_number(s) for s in sample.stamp_ids), default=-1) + 1
    return f"{sample.sample_id}_stamp{index:03d}"


def rename_project(library_root: str | Path, project_id: str, name: str) -> Project:
    project = load_project(library_root, project_id)
    project.name = name
    save_project(library_root, project)
    return project


def delete_project(library_root: str | Path, project_id: str) -> None:
    """Permanently remove a project with all its sheets, stamps and reports."""
    from stampan.core import publish  # publish builds on this module

    shutil.rmtree(project_dir(library_root, project_id))
    publish.remove_from_index(library_root, project_id)


def rename_sample(
    library_root: str | Path, project_id: str, sample_id: str, name: str | None
) -> Sample:
    sample = load_sample(library_root, project_id, sample_id)
    sample.name = name or None
    save_sample(library_root, sample)
    return sample


def delete_stamp(library_root: str | Path, project_id: str, stamp_id: str) -> None:
    """Permanently remove a stamp (crop, results, diagnostics) from its sheet."""
    sample = load_sample(
        library_root, project_id, find_sample_for_stamp(library_root, project_id, stamp_id)
    )
    sample.stamp_ids.remove(stamp_id)
    save_sample(library_root, sample)
    shutil.rmtree(project_dir(library_root, project_id) / "stamps" / stamp_id, ignore_errors=True)


def delete_sample(library_root: str | Path, project_id: str, sample_id: str) -> None:
    """Permanently remove a sheet: its scan, its record and all its stamps."""
    p_dir = project_dir(library_root, project_id)
    sample = load_sample(library_root, project_id, sample_id)
    for stamp_id in sample.stamp_ids:
        shutil.rmtree(p_dir / "stamps" / stamp_id, ignore_errors=True)
    project = load_project(library_root, project_id)
    project.sample_ids = [s for s in project.sample_ids if s != sample_id]
    save_project(library_root, project)
    (p_dir / sample.scan_path).unlink(missing_ok=True)
    (p_dir / f"{sample_id}.json").unlink(missing_ok=True)
    for extra in (p_dir / "diagnostics").glob(f"{sample_id}_*.png"):  # overview, contact sheet
        extra.unlink()


def stamp_index(library_root: str | Path, project: Project) -> dict[str, str]:
    """stamp_id -> sample_id for every stamp in ``project``, in sheet order."""
    index: dict[str, str] = {}
    for sample_id in project.sample_ids:
        for stamp_id in load_sample(library_root, project.project_id, sample_id).stamp_ids:
            index[stamp_id] = sample_id
    return index


def find_sample_for_stamp(library_root: str | Path, project_id: str, stamp_id: str) -> str:
    sample_id = stamp_index(library_root, load_project(library_root, project_id)).get(stamp_id)
    if sample_id is None:
        raise ValueError(f"stamp {stamp_id} not found in project {project_id}")
    return sample_id


def save_stamp_roi_mask(
    library_root: str | Path, project_id: str, stamp_id: str, roi_name: str, mask: np.ndarray
) -> str:
    """Write a single-channel ROI mask PNG next to stamp.json (the GUI's
    per-colour inclusion ROIs, SPEC §8.1). Returns the path relative to the
    project directory, for storing on the owning ``ColourCluster.mask_path``."""
    import cv2

    stamp_dir = project_dir(library_root, project_id) / "stamps" / stamp_id / "colour_rois"
    stamp_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_slugify(roi_name)}.png"
    cv2.imwrite(str(stamp_dir / filename), mask)
    return f"stamps/{stamp_id}/colour_rois/{filename}"


def rename_stamp_colour(
    library_root: str | Path, project_id: str, stamp_id: str, old: str, new: str
) -> Stamp:
    """Rename one of a stamp's colours, moving its area mask with it."""
    stamp = load_stamp(library_root, project_id, stamp_id)
    if any(c.label == new for c in stamp.colour.clusters):
        raise ValueError(f"this stamp already has a colour named '{new}'")
    for cluster in stamp.colour.clusters:
        if cluster.label != old:
            continue
        cluster.label = new
        if cluster.mask_path:
            p_dir = project_dir(library_root, project_id)
            old_path = p_dir / cluster.mask_path
            new_rel = f"stamps/{stamp_id}/colour_rois/{_slugify(new)}.png"
            if old_path.exists():
                old_path.rename(p_dir / new_rel)
            cluster.mask_path = new_rel
        save_stamp_results(library_root, project_id, stamp)
        return stamp
    raise ValueError(f"no colour named '{old}' on {stamp_id}")


def load_stamp_roi_mask(library_root: str | Path, project_id: str, path: str) -> np.ndarray | None:
    """Load a mask PNG saved by :func:`save_stamp_roi_mask`, given the
    project-relative path stored on ``ColourCluster.mask_path``."""
    import cv2

    if not path:
        return None
    full_path = project_dir(library_root, project_id) / path
    if not full_path.exists():
        return None
    return cv2.imread(str(full_path), cv2.IMREAD_GRAYSCALE)
