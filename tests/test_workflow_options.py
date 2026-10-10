from stampan.core import workflow


def test_reference_scan_options_overrides():
    base = workflow.reference_scan_options(1200)
    assert base["resolution"] == "1200" and base["sharpness"] == "0"
    opts = workflow.reference_scan_options(
        600, {"--sharpness": "1", "color-correction": "", "resolution": "9", "brightness": "0"}
    )
    assert opts["resolution"] == "600"
    assert opts["sharpness"] == "1"
    assert "color-correction" not in opts
    assert opts["brightness"] == "0"
