"""Region detection, crops as a cache, and the published export, on a small
synthetic sheet: three bright "stamps" on a dark stock card."""

import json

import numpy as np
import pytest

from stampan.core import library, publish, workflow
from stampan.core import project as proj
from stampan.core.calibration import reference as ref_mod
from stampan.core.io import ScanMetadata
from stampan.core.models import Reference, ScaleCalibration

PX_PER_MM = 10.0
# (x0, y0) of each 20 x 24 mm stamp; the third one straddles x = 560.
STAMPS = [(40, 60), (300, 70), (520, 380)]


@pytest.fixture
def library_root(tmp_path):
    reference = Reference(
        ref_id="ref_t",
        name="test",
        created="2026-11-09",
        scale=ScaleCalibration(px_per_mm_x=PX_PER_MM, px_per_mm_y=PX_PER_MM),
    )
    (tmp_path / "references" / "ref_t").mkdir(parents=True)
    ref_mod.save_reference_metadata(tmp_path, reference)
    return tmp_path


def _sheet() -> np.ndarray:
    sheet = np.full((700, 900, 3), 3000, np.uint16)
    for x, y in STAMPS:
        sheet[y : y + 240, x : x + 200] = 42000
    return sheet


def _empty_sheet(root):
    project = proj.create_project(root, "Synthetic")
    result = workflow.ingest_sample(root, project, "ref_t", _sheet(), ScanMetadata(), detect=False)
    assert result.n_stamps == 0
    return project.project_id, result.sample.sample_id


def test_detect_in_region_skips_cut_and_existing_stamps(library_root):
    project_id, sample_id = _empty_sheet(library_root)
    region = (0, 0, 620, 700)  # the first two stamps whole, 10 mm of the third

    found = workflow.detect_stamps_in_region(library_root, project_id, sample_id, region)
    assert len(found.added) == 2 and found.n_cut == 1
    stamps = [proj.load_stamp(library_root, project_id, s) for s in found.added]
    assert [round(s.geometry.width_mm) for s in stamps] == [20, 20]
    xs = [s.geometry.sample_centre_px[0] for s in stamps]
    assert xs == sorted(xs)  # reading order
    assert (
        library_root / "projects" / project_id / "diagnostics" / f"{sample_id}_region.png"
    ).exists()

    again = workflow.detect_stamps_in_region(library_root, project_id, sample_id, region)
    assert again.added == [] and again.n_existing == 2

    workflow.remove_stamps(library_root, project_id, found.added)  # undo
    assert proj.load_sample(library_root, project_id, sample_id).stamp_ids == []


def test_region_smaller_than_a_stamp_is_refused(library_root):
    project_id, sample_id = _empty_sheet(library_root)
    with pytest.raises(workflow.WorkflowError):
        workflow.detect_stamps_in_region(library_root, project_id, sample_id, (0, 0, 30, 30))


def test_missing_crop_is_rebuilt_identically(library_root):
    project_id, sample_id = _empty_sheet(library_root)
    (stamp_id, *_) = workflow.detect_stamps_in_region(
        library_root, project_id, sample_id, (0, 0, 280, 400)
    ).added
    crop_path = library_root / "projects" / project_id / "stamps" / stamp_id / "crop.tif"
    original = proj.load_stamp_crop(library_root, project_id, stamp_id)
    crop_path.unlink()
    rebuilt = proj.load_stamp_crop(library_root, project_id, stamp_id)
    assert crop_path.exists()
    np.testing.assert_array_equal(rebuilt, original)


def test_publish_writes_stable_export_and_follows_changes(library_root):
    library.ensure_layout(library_root)
    assert "*.tif" in (library_root / ".gitignore").read_text()
    project_id, sample_id = _empty_sheet(library_root)
    added = workflow.detect_stamps_in_region(
        library_root, project_id, sample_id, (0, 0, 560, 700)
    ).added
    workflow.update_stamp_details(
        library_root, project_id, added[0], catalogue="Mi 23,  SG 4", cancellation_date="c. 1889-3"
    )
    workflow.set_perforation_text(library_root, project_id, added[0], "11½ × 12")

    result = publish.publish_project(library_root, project_id)
    assert result.n_stamps == 2 and result.n_images_rendered == 2
    doc = json.loads((result.path / "project.json").read_text())
    assert doc["schema"] == publish.SCHEMA
    first = doc["stamps"][0]
    assert first["catalogue"] == ["Mi 23", "SG 4"]
    assert first["cancellation"] == {
        "type": None,
        "date": "1889-03",
        "estimated": True,
        "place": None,
    }
    assert first["perforation"]["notation"] == "11 1/2 x 12"
    assert first["perforation"]["sides"]["top"]["by_hand"] is True
    assert first["image_calibrated"] is False  # no colour model: not a calibrated picture
    assert (result.path / first["image"]).exists() and (result.path / first["thumbnail"]).exists()
    index = json.loads((library_root / publish.INDEX_FILE).read_text())
    assert [p["id"] for p in index["projects"]] == [project_id]

    # Unchanged pictures are not rendered again; a removed stamp's are deleted.
    assert publish.publish_project(library_root, project_id).n_images_rendered == 0
    workflow.remove_stamps(library_root, project_id, [added[1]])
    publish.publish_project(library_root, project_id)
    assert not (result.path / "stamps" / f"{added[1]}.webp").exists()

    proj.delete_project(library_root, project_id)
    index = json.loads((library_root / publish.INDEX_FILE).read_text())
    assert index["projects"] == []


def test_rotate_stamp_turns_crop_sides_box_and_masks(library_root):
    from stampan.core.models import ColourCluster, PerforationSide

    project_id, sample_id = _empty_sheet(library_root)
    (stamp_id,) = workflow.detect_stamps_in_region(
        library_root, project_id, sample_id, (0, 0, 280, 400)
    ).added
    crop = proj.load_stamp_crop(library_root, project_id, stamp_id)
    h, w = crop.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    mask[10:20, 30:40] = 255  # near the top-left corner
    mask_path = proj.save_stamp_roi_mask(library_root, project_id, stamp_id, "ink", mask)
    stamp = proj.load_stamp(library_root, project_id, stamp_id)
    stamp.colour.clusters = [ColourCluster(label="ink", mask_path=mask_path)]
    stamp.perforation.auto = {"top": PerforationSide(gauge=11.0, notation="11")}
    stamp.perforation.manual = {"left": PerforationSide(kind="imperf")}
    stamp.perforation.box_override_px = [
        (5.0, 6.0),
        (w - 5.0, 6.0),
        (w - 5.0, h - 6.0),
        (5.0, h - 6.0),
    ]
    proj.save_stamp_results(library_root, project_id, stamp)

    turned = workflow.rotate_stamp(library_root, project_id, stamp_id, clockwise=True)
    new_crop = proj.load_stamp_crop(library_root, project_id, stamp_id)
    assert new_crop.shape[:2] == (w, h)
    expected = np.rot90(crop, -1).astype(int)
    assert np.mean(np.abs(new_crop.astype(int) - expected)) < 0.01 * 42000
    assert set(turned.perforation.auto) == {"right"}
    assert set(turned.perforation.manual) == {"top"}
    assert (turned.geometry.width_mm, turned.geometry.height_mm) == (
        stamp.geometry.height_mm,
        stamp.geometry.width_mm,
    )
    new_mask = proj.load_stamp_roi_mask(library_root, project_id, mask_path)
    ys, xs = np.nonzero(new_mask)
    assert xs.min() > w * 0.8 and ys.max() < h * 0.2  # now near the top-right corner
    assert turned.perforation.box_override_px[0] == (h - 6.0, 5.0)

    back = workflow.rotate_stamp(library_root, project_id, stamp_id, clockwise=False)
    assert back.geometry.rotation_deg == pytest.approx(stamp.geometry.rotation_deg)
    assert back.perforation.box_override_px[0] == pytest.approx((5.0, 6.0))
    np.testing.assert_array_equal(proj.load_stamp_crop(library_root, project_id, stamp_id), crop)
    np.testing.assert_array_equal(
        proj.load_stamp_roi_mask(library_root, project_id, mask_path), mask
    )
