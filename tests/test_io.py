import numpy as np

from stampan.core.io import ScanMetadata, read_tiff, write_tiff


def test_tiff_roundtrip_gray(tmp_path):
    img = (np.random.default_rng(0).random((64, 48)) * 65535).astype(np.uint16)
    meta = ScanMetadata(
        scan_time="2026-10-02T12:00:00",
        scanner_options={"resolution": 1200, "mode": "Color", "depth": 16},
        ref_id="ref_abc123",
        extra={"note": "test"},
    )
    path = tmp_path / "test.tif"
    write_tiff(path, img, meta)

    loaded_img, loaded_meta = read_tiff(path)
    np.testing.assert_array_equal(loaded_img, img)
    assert loaded_meta.scan_time == meta.scan_time
    assert loaded_meta.scanner_options == meta.scanner_options
    assert loaded_meta.ref_id == meta.ref_id
    assert loaded_meta.extra == meta.extra


def test_tiff_roundtrip_rgb(tmp_path):
    img = (np.random.default_rng(1).random((32, 32, 3)) * 65535).astype(np.uint16)
    path = tmp_path / "rgb.tif"
    write_tiff(path, img)

    loaded_img, loaded_meta = read_tiff(path)
    np.testing.assert_array_equal(loaded_img, img)
    assert loaded_meta.scan_time is None
    assert loaded_meta.ref_id is None


def test_write_tiff_rejects_non_uint16(tmp_path):
    img = np.zeros((4, 4), dtype=np.uint8)
    try:
        write_tiff(tmp_path / "bad.tif", img)
        raised = False
    except ValueError:
        raised = True
    assert raised
