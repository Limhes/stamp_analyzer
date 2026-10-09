import subprocess
import sys
from pathlib import Path

import pytest

import stampan.core.scanner as scanner_mod
from stampan.core.scanner import acquire_scan, apply_and_verify, dump_options, list_devices

FAKE_SCANIMAGE = [sys.executable, str(Path(__file__).parent / "fakes" / "fake_scanimage.py")]


@pytest.fixture
def patched_run(tmp_path, monkeypatch):
    """Point core.scanner._run at the fake scanimage script, with isolated state."""
    monkeypatch.setenv("FAKE_SCANNER_STATE", str(tmp_path / "state.json"))

    def fake_run(args):
        return subprocess.run(
            [*FAKE_SCANIMAGE, *args[1:]], capture_output=True, text=True, check=False
        )

    monkeypatch.setattr(scanner_mod, "_run", fake_run)
    return fake_run


def test_list_devices(patched_run):
    devices = list_devices(scanimage_cmd="scanimage")
    assert len(devices) == 1
    assert "Epson Perfection 1200U" in devices[0].description


def test_dump_options_parses_known_fields(patched_run):
    opts = dump_options(scanimage_cmd="scanimage")
    assert "--resolution" in opts.options
    assert opts.options["--resolution"].current == "300"
    assert "--depth" in opts.options

    relevant = opts.relevant_subset()
    assert "--resolution" in relevant
    assert "--mode" in relevant
    assert "--depth" in relevant
    # scan-area options must not leak into the must-match subset
    assert "-l" not in relevant
    area = opts.scan_area_subset()
    assert "-l" in area


def test_apply_and_verify_matches(patched_run):
    # apply_and_verify must set and read back options in one invocation --
    # a direct-USB backend (confirmed on the real Epson 1200U) does not
    # persist option state across separate scanimage processes, so a
    # separate dump_options() call afterwards is not expected to see it.
    mismatches = apply_and_verify({"resolution": "1200", "mode": "Color", "depth": "16"})
    assert mismatches == {}


def test_apply_and_verify_detects_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SCANNER_STATE", str(tmp_path / "state.json"))

    def fake_run(args):
        # Simulate a backend that clamps an out-of-range resolution, so the
        # applied value differs from what was requested.
        patched = ["--resolution=1200" if a == "--resolution=9999" else a for a in args]
        return subprocess.run(
            [*FAKE_SCANIMAGE, *patched[1:]], capture_output=True, text=True, check=False
        )

    monkeypatch.setattr(scanner_mod, "_run", fake_run)

    mismatches = apply_and_verify({"resolution": "9999"})
    assert mismatches == {"resolution": ("9999", "1200")}


def test_acquire_scan_writes_requested_depth(patched_run, tmp_path):
    out = tmp_path / "scan.tif"
    result = acquire_scan(out, resolution_dpi=1200, mode="Color", depth=16)
    assert result.bit_depth == 16
    assert result.warnings == []
    assert out.exists()


def test_acquire_scan_warns_on_depth_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SCANNER_STATE", str(tmp_path / "state.json"))

    def fake_run(args):
        # Simulate a backend that only supports 8-bit, ignoring --depth=16.
        patched = ["--depth=8" if a == "--depth=16" else a for a in args]
        return subprocess.run(
            [*FAKE_SCANIMAGE, *patched[1:]], capture_output=True, text=True, check=False
        )

    monkeypatch.setattr(scanner_mod, "_run", fake_run)

    out = tmp_path / "scan.tif"
    result = acquire_scan(out, depth=16)
    assert result.bit_depth == 8
    assert any("8-bit" in w for w in result.warnings)


def test_acquire_scan_omits_depth_flag_when_backend_reports_it_inactive(tmp_path, monkeypatch):
    # Real-world case found on the Epson 1200U via the epson2 backend:
    # --depth is reported as the single fixed value "8bit", marked
    # [inactive]. Passing --depth at all there makes scanimage abort
    # ("attempted to set inactive option depth"); acquire_scan must detect
    # this via -A first and simply not pass the flag, rather than crash.
    monkeypatch.setenv("FAKE_SCANNER_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("FAKE_SCANNER_DEPTH_INACTIVE", "1")

    def fake_run(args):
        return subprocess.run(
            [*FAKE_SCANIMAGE, *args[1:]], capture_output=True, text=True, check=False
        )

    monkeypatch.setattr(scanner_mod, "_run", fake_run)

    out = tmp_path / "scan.tif"
    result = acquire_scan(out, depth=16)
    assert result.bit_depth == 8
    assert any("does not support selecting bit depth" in w for w in result.warnings)
