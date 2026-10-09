"""SANE scanner wrapper around the `scanimage` CLI (SPEC §2, §4.1).

Everything here shells out to `scanimage` rather than linking against SANE,
so it has no compiled dependency and is easy to mock in tests (pass a fake
executable as `scanimage_cmd`). All automatic processing must be verified
off on the real device (auto exposure/gamma/colour-correction/sharpening/
descreening) -- this module captures and verifies the option set, it does
not decide which options are "correct" for the Epson 1200U.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: Option-name substrings that affect geometry or colour and therefore must
#: match between a reference and every sample scanned with it (SPEC §4.1).
#: Scan-area options (-l/-t/-x/-y) are deliberately excluded: the spec
#: allows the scan area to differ between reference and sample.
_GEOMETRY_COLOUR_KEYWORDS = (
    "resolution",
    "mode",
    "depth",
    "bit-depth",
    "gamma",
    "correction",
    "sharp",
    "descreen",
    "auto",
    "source",
    "brightness",
    "contrast",
)

_SCAN_AREA_OPTIONS = {"-l", "-t", "-x", "-y", "tl-x", "tl-y", "br-x", "br-y"}

_DEVICE_LINE_RE = re.compile(r"^device `([^']+)' is a (.+)$")
# e.g. "    --resolution 50..1200dpi (in steps of 1) [300]"
#      "    -l 0..216.069mm [0]"
#      "    --test [no]"
#      "    --color-correction None|... [None] [advanced]" -- a trailing
#      "[advanced]"/"[inactive]"-style tag is a SECOND bracket group after
#      the value, so `current` must take the FIRST bracket on the line, not
#      the last (an end-anchored match would grab "advanced" as the value).
_OPTION_LINE_RE = re.compile(
    r"^ {4}(?P<name>-{1,2}[\w-]+)(?:\s+(?P<constraint>.*?))?\s*\[(?P<current>[^\]]*)\]"
)


@dataclass
class ScannerDevice:
    device_name: str
    description: str


@dataclass
class ScannerOption:
    name: str  # e.g. "--resolution" or "-l"
    constraint: str  # raw constraint text, e.g. "50..1200dpi (in steps of 1)"
    current: str  # raw bracketed value, e.g. "300", "inactive", "auto"
    description: str = ""

    @property
    def is_geometry_or_colour(self) -> bool:
        key = self.name.lstrip("-").lower()
        return any(kw in key for kw in _GEOMETRY_COLOUR_KEYWORDS)

    @property
    def is_scan_area(self) -> bool:
        return self.name in _SCAN_AREA_OPTIONS


@dataclass
class ScannerOptions:
    device: str | None
    raw_text: str
    options: dict[str, ScannerOption] = field(default_factory=dict)

    def relevant_subset(self) -> dict[str, str]:
        """Geometry/colour-relevant option values that must match reference<->sample."""
        return {
            name: opt.current
            for name, opt in self.options.items()
            if opt.is_geometry_or_colour and not opt.is_scan_area
        }

    def scan_area_subset(self) -> dict[str, str]:
        return {name: opt.current for name, opt in self.options.items() if opt.is_scan_area}


class ScannerError(RuntimeError):
    pass


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def list_devices(scanimage_cmd: str = "scanimage") -> list[ScannerDevice]:
    """Parse `scanimage -L` into a list of devices."""
    proc = _run([scanimage_cmd, "-L"])
    devices = []
    for line in proc.stdout.splitlines():
        m = _DEVICE_LINE_RE.match(line.strip())
        if m:
            devices.append(ScannerDevice(device_name=m.group(1), description=m.group(2)))
    return devices


def _parse_options_text(text: str) -> dict[str, ScannerOption]:
    options: dict[str, ScannerOption] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _OPTION_LINE_RE.match(lines[i])
        if m:
            name = m.group("name")
            description_lines = []
            j = i + 1
            while (
                j < len(lines)
                and lines[j].startswith(" " * 8)
                and not lines[j].startswith(" " * 8 + "-")
            ):
                description_lines.append(lines[j].strip())
                j += 1
            options[name] = ScannerOption(
                name=name,
                constraint=(m.group("constraint") or "").strip(),
                current=(m.group("current") or "").strip(),
                description=" ".join(description_lines),
            )
            i = j
        else:
            i += 1
    return options


def dump_options(
    device: str | None = None,
    scanimage_cmd: str = "scanimage",
) -> ScannerOptions:
    """Run `scanimage -A [-d device]` and parse the full option list."""
    args = [scanimage_cmd, "-A"]
    if device:
        args += ["-d", device]
    proc = _run(args)
    if proc.returncode != 0 and not proc.stdout:
        raise ScannerError(f"scanimage -A failed: {proc.stderr.strip()}")
    options = _parse_options_text(proc.stdout)
    return ScannerOptions(device=device, raw_text=proc.stdout, options=options)


#: Options this backend accepts but never echoes a current value for in
#: `-A` output (confirmed: `--{red,green,blue}-gamma-table`, active or
#: not, print just the constraint line with no trailing "[...]") -- there
#: is nothing to read back and compare, so apply_and_verify can't apply
#: its normal all-or-nothing check to them without permanently refusing to
#: ever set them. They're still applied; the caller just can't get the
#: same hard guarantee SPEC §4.1 asks for on everything else.
UNVERIFIABLE_OPTION_SUFFIXES = ("-gamma-table",)


def apply_and_verify(
    options: dict[str, str],
    device: str | None = None,
    scanimage_cmd: str = "scanimage",
) -> dict[str, tuple[str, str]]:
    """Apply `options` (long-option-name -> value, without leading dashes) and
    read them back, in a single `scanimage` invocation.

    Setting options and dumping them must happen in the same process: a
    direct-USB SANE backend (confirmed on the Epson 1200U's `epson2`
    backend) does not persist option state across separate `scanimage`
    invocations, so a set-then-dump done as two calls always reads back
    the backend's power-on defaults rather than what was just requested.

    Returns a dict of mismatches: {option_name: (requested, actual)}. An
    empty dict means every *verifiable* option was applied and confirmed
    (see `UNVERIFIABLE_OPTION_SUFFIXES`). Per SPEC §4.1, the caller must
    refuse to scan (or warn loudly) on a non-empty result.
    """
    args = [scanimage_cmd]
    if device:
        args += ["-d", device]
    for name, value in options.items():
        flag = name if name.startswith("-") else f"--{name}"
        args += [f"{flag}={value}"]
    args += ["-n", "-A"]  # -n: set options, don't scan; -A: dump resulting state
    proc = _run(args)
    if proc.returncode != 0:
        raise ScannerError(f"applying scanner options failed: {proc.stderr.strip()}")

    verified = ScannerOptions(
        device=device, raw_text=proc.stdout, options=_parse_options_text(proc.stdout)
    )
    mismatches: dict[str, tuple[str, str]] = {}
    for name, requested in options.items():
        if name.endswith(UNVERIFIABLE_OPTION_SUFFIXES):
            continue
        flag = name if name.startswith("-") else f"--{name}"
        actual_opt = verified.options.get(flag)
        actual = actual_opt.current if actual_opt else "<option not found>"
        if actual.strip().lower() != str(requested).strip().lower():
            mismatches[name] = (str(requested), actual)
    return mismatches


@dataclass
class ScanAcquireResult:
    output_path: Path
    bit_depth: int  # actual depth of the written file, as read back
    warnings: list[str] = field(default_factory=list)


def acquire_scan(
    output_path: str | Path,
    device: str | None = None,
    resolution_dpi: int | None = None,
    mode: str = "Color",
    depth: int = 16,
    extra_options: dict[str, str] | None = None,
    scanimage_cmd: str = "scanimage",
) -> ScanAcquireResult:
    """Acquire a scan to a TIFF file, requesting 16-bit depth where supported.

    Verifies the depth actually written and records a warning if the
    backend silently fell back to 8-bit. Some backends (e.g. `epson2` on
    the Perfection 1200U: ``--depth`` is reported as the single fixed value
    "8bit", marked inactive) don't expose depth as a settable option at
    all -- passing ``--depth`` there makes `scanimage` abort outright
    ("attempted to set inactive option depth") rather than silently
    clamping it, so that case is detected up front and the flag is simply
    omitted, with a warning, instead of letting the scan fail.
    """
    output_path = Path(output_path)
    warnings: list[str] = []

    depth_option_active = True
    try:
        current_opts = dump_options(device=device, scanimage_cmd=scanimage_cmd)
        depth_opt = current_opts.options.get("--depth")
        if depth_opt is not None and depth_opt.current.strip().lower() == "inactive":
            depth_option_active = False
    except ScannerError:
        pass  # fall through and try with --depth; worst case scanimage reports the error itself

    args = [scanimage_cmd, "--format=tiff", "-o", str(output_path)]
    if device:
        args += ["-d", device]
    if resolution_dpi is not None:
        args += [f"--resolution={int(resolution_dpi)}"]
    if mode:
        args += [f"--mode={mode}"]
    if depth_option_active:
        args += [f"--depth={depth}"]
    else:
        warnings.append(
            f"backend does not support selecting bit depth (requested {depth}-bit); "
            "scanning at its fixed depth instead"
        )
    for name, value in (extra_options or {}).items():
        flag = name if name.startswith("-") else f"--{name}"
        args += [f"{flag}={value}"]

    proc = _run(args)
    if proc.returncode != 0:
        raise ScannerError(f"scan failed: {proc.stderr.strip()}")

    actual_depth = depth
    try:
        import tifffile

        with tifffile.TiffFile(str(output_path)) as tif:
            actual_depth = tif.pages[0].dtype.itemsize * 8
    except Exception as exc:  # pragma: no cover - defensive, depends on backend output
        warnings.append(f"could not verify written bit depth: {exc}")

    if actual_depth != depth:
        warnings.append(f"requested {depth}-bit depth, file is actually {actual_depth}-bit")

    return ScanAcquireResult(output_path=output_path, bit_depth=actual_depth, warnings=warnings)
