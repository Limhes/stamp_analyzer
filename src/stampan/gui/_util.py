"""Small helpers shared by the GUI panels/main window -- no analysis logic,
just project-tree lookups that ``core.project`` doesn't itself index."""

from __future__ import annotations

from stampan.core import project as project_mod


def find_sample_for_stamp(library_root, project_id: str, stamp_id: str) -> str:
    project = project_mod.load_project(library_root, project_id)
    for sample_id in project.sample_ids:
        sample = project_mod.load_sample(library_root, project_id, sample_id)
        if stamp_id in sample.stamp_ids:
            return sample_id
    raise ValueError(f"stamp {stamp_id} not found in project {project_id}")
