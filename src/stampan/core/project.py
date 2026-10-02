"""Project/sample/stamp persistence (SPEC §3.1).

```
<library_root>/projects/<project_id>/
  project.json
  sample_<n>.tif
  sample_<n>.json
  stamps/<stamp_id>/
    crop.tif
    stamp.json
    diagnostics/*.png
```
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from stampan.core.io import ScanMetadata, read_tiff, write_tiff
from stampan.core.models import Project, Sample, Stamp


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "project"


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
    (p_dir / "project.json").write_text(project.to_json(), encoding="utf-8")
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

    n = len(project.sample_ids) + 1
    sample_id = f"sample_{n:03d}"
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
    path.write_text(sample.to_json(), encoding="utf-8")
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

    write_tiff(stamp_dir / "crop.tif", crop.astype(np.uint16))
    stamp.crop_path = f"stamps/{stamp.stamp_id}/crop.tif"
    (stamp_dir / "stamp.json").write_text(stamp.to_json(), encoding="utf-8")

    for filename, data in (diagnostics or {}).items():
        (stamp_dir / "diagnostics" / filename).write_bytes(data)

    if stamp.stamp_id not in sample.stamp_ids:
        sample.stamp_ids.append(stamp.stamp_id)
    return stamp_dir


def load_stamp(library_root: str | Path, project_id: str, stamp_id: str) -> Stamp:
    stamp_dir = project_dir(library_root, project_id) / "stamps" / stamp_id
    return Stamp.from_json((stamp_dir / "stamp.json").read_text(encoding="utf-8"))


def load_stamp_crop(library_root: str | Path, project_id: str, stamp_id: str) -> np.ndarray:
    stamp_dir = project_dir(library_root, project_id) / "stamps" / stamp_id
    image, _ = read_tiff(stamp_dir / "crop.tif")
    return image


def next_stamp_id(sample: Sample, index: int) -> str:
    return f"{sample.sample_id}_stamp{index:03d}"
