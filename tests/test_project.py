import numpy as np
import pytest

from stampan.core import project as proj
from stampan.core.io import ScanMetadata
from stampan.core.models import GeometryResult, Stamp


def test_create_and_load_project_roundtrip(tmp_path):
    p = proj.create_project(tmp_path, "My Test Project", notes="hello")
    loaded = proj.load_project(tmp_path, p.project_id)
    assert loaded == p

    projects = proj.list_projects(tmp_path)
    assert len(projects) == 1
    assert projects[0].project_id == p.project_id


def test_get_or_create_project_reuses_existing(tmp_path):
    p1 = proj.get_or_create_project(tmp_path, "Reused Project")
    p2 = proj.get_or_create_project(tmp_path, "Reused Project")
    assert p1.project_id == p2.project_id
    assert len(proj.list_projects(tmp_path)) == 1


def test_add_sample_writes_tiff_and_updates_project(tmp_path):
    p = proj.create_project(tmp_path, "Sample Project")
    scan = (np.random.default_rng(0).random((20, 20)) * 65535).astype(np.uint16)
    sample = proj.add_sample(
        tmp_path, p, scan, ScanMetadata(scan_time="2026-01-01T00:00:00"), ref_id="ref_abc"
    )
    assert sample.sample_id == "sample_001"
    assert sample.ref_id == "ref_abc"

    reloaded_project = proj.load_project(tmp_path, p.project_id)
    assert reloaded_project.sample_ids == ["sample_001"]

    loaded_scan, meta = proj.load_sample_scan(tmp_path, p.project_id, sample)
    np.testing.assert_array_equal(loaded_scan, scan)
    assert meta.ref_id == "ref_abc"


def test_add_sample_increments_sample_id(tmp_path):
    p = proj.create_project(tmp_path, "Multi Sample Project")
    scan = np.zeros((10, 10), dtype=np.uint16)
    s1 = proj.add_sample(tmp_path, p, scan, ScanMetadata(), ref_id="r")
    s2 = proj.add_sample(tmp_path, p, scan, ScanMetadata(), ref_id="r")
    assert s1.sample_id == "sample_001"
    assert s2.sample_id == "sample_002"


def test_save_and_load_stamp_roundtrip(tmp_path):
    p = proj.create_project(tmp_path, "Stamp Project")
    scan = np.zeros((10, 10), dtype=np.uint16)
    sample = proj.add_sample(tmp_path, p, scan, ScanMetadata(), ref_id="r")

    stamp_id = proj.next_stamp_id(sample, 0)
    stamp = Stamp(stamp_id=stamp_id, geometry=GeometryResult(width_mm=20.0, height_mm=25.0))
    crop = (np.random.default_rng(1).random((30, 25)) * 65535).astype(np.uint16)
    proj.save_stamp(tmp_path, sample, stamp, crop, diagnostics={"overview.png": b"\x89PNG"})
    proj.save_sample(tmp_path, sample)

    reloaded_sample = proj.load_sample(tmp_path, p.project_id, sample.sample_id)
    assert reloaded_sample.stamp_ids == [stamp_id]

    loaded_stamp = proj.load_stamp(tmp_path, p.project_id, stamp_id)
    assert loaded_stamp.geometry.width_mm == pytest.approx(20.0)
    assert loaded_stamp.crop_path == f"stamps/{stamp_id}/crop.tif"

    loaded_crop = proj.load_stamp_crop(tmp_path, p.project_id, stamp_id)
    np.testing.assert_array_equal(loaded_crop, crop)

    diag_path = (
        proj.project_dir(tmp_path, p.project_id)
        / "stamps"
        / stamp_id
        / "diagnostics"
        / "overview.png"
    )
    assert diag_path.exists()


def test_delete_and_rename_keep_ids_unique(tmp_path):
    from stampan.core.models import ColourCluster, Stamp

    p = proj.create_project(tmp_path, "P")
    scan = np.zeros((10, 10, 3), dtype=np.uint16)
    s1 = proj.add_sample(tmp_path, p, scan, ScanMetadata(), ref_id="r")
    s2 = proj.add_sample(tmp_path, p, scan, ScanMetadata(), ref_id="r")
    crop = np.zeros((4, 4, 3), dtype=np.uint16)
    for i in range(3):
        proj.save_stamp(tmp_path, s2, Stamp(stamp_id=proj.next_stamp_id(s2, i)), crop)
    proj.save_sample(tmp_path, s2)

    proj.delete_stamp(tmp_path, p.project_id, s2.stamp_ids[1])
    s2 = proj.load_sample(tmp_path, p.project_id, s2.sample_id)
    assert len(s2.stamp_ids) == 2
    assert proj.next_stamp_id(s2) == f"{s2.sample_id}_stamp003"

    proj.delete_sample(tmp_path, p.project_id, s1.sample_id)
    p = proj.load_project(tmp_path, p.project_id)
    assert p.sample_ids == [s2.sample_id]
    assert proj.next_sample_id(tmp_path, p) == "sample_003"

    assert proj.rename_sample(tmp_path, p.project_id, s2.sample_id, "Finland 1875").name == (
        "Finland 1875"
    )
    assert proj.rename_project(tmp_path, p.project_id, "Q").name == "Q"

    stamp_id = s2.stamp_ids[0]
    stamp = proj.load_stamp(tmp_path, p.project_id, stamp_id)
    mask_rel = proj.save_stamp_roi_mask(
        tmp_path, p.project_id, stamp_id, "ink", np.full((4, 4), 255, np.uint8)
    )
    stamp.colour.clusters = [ColourCluster(label="ink", mask_path=mask_rel)]
    proj.save_stamp_results(tmp_path, p.project_id, stamp)
    renamed = proj.rename_stamp_colour(tmp_path, p.project_id, stamp_id, "ink", "frame")
    assert renamed.colour.clusters[0].label == "frame"
    assert (
        proj.load_stamp_roi_mask(tmp_path, p.project_id, renamed.colour.clusters[0].mask_path)
        is not None
    )

    proj.delete_project(tmp_path, p.project_id)
    assert proj.list_projects(tmp_path) == []
