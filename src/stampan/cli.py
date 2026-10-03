"""`stampan` command-line entry point. Commands are added phase by phase."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from stampan import __version__
from stampan.core import detection as detection_mod
from stampan.core import perforation as perforation_mod
from stampan.core import project as project_mod
from stampan.core import scanner as scanner_mod
from stampan.core.calibration import it8 as it8_mod
from stampan.core.calibration import reference as ref_mod
from stampan.core.io import ScanMetadata, read_tiff
from stampan.core.models import GeometryResult, Stamp

app = typer.Typer(
    name="stampan",
    help="Calibrated measurement of postage stamps from flatbed scans.",
    no_args_is_help=True,
)
reference_app = typer.Typer(help="Manage calibration references (SPEC §4.4).")
app.add_typer(reference_app, name="reference")

DEFAULT_LIBRARY_ROOT = Path.home() / "StampAnalyzer"
DEFAULT_CGATS_PATH = Path("reference_data") / "R240103.txt"


@app.command()
def version() -> None:
    """Print the stampan version."""
    typer.echo(__version__)


@app.command("scanner-info")
def scanner_info(
    device: str | None = typer.Option(None, help="SANE device name, e.g. from --list-devices."),
    scanimage_cmd: str = typer.Option("scanimage", help="scanimage executable to use."),
) -> None:
    """List scanners and dump the full backend option set (SPEC §4.1)."""
    devices = scanner_mod.list_devices(scanimage_cmd=scanimage_cmd)
    if not devices:
        typer.echo("No SANE devices found (scanimage -L returned none).")
    for d in devices:
        typer.echo(f"  {d.device_name}: {d.description}")

    opts = scanner_mod.dump_options(device=device, scanimage_cmd=scanimage_cmd)
    typer.echo("\nGeometry/colour-relevant options (must match reference <-> sample):")
    for name, value in opts.relevant_subset().items():
        typer.echo(f"  {name} = {value}")
    typer.echo("\nScan-area options (allowed to differ):")
    for name, value in opts.scan_area_subset().items():
        typer.echo(f"  {name} = {value}")
    typer.echo("\nFull option dump:")
    typer.echo(opts.raw_text)


@app.command()
def scan(
    output: Path = typer.Option(..., "-o", "--output", help="Output TIFF path."),
    options_from: Path = typer.Option(
        ..., help="A reference.json (uses its scanner_options) or a plain options JSON file."
    ),
    device: str | None = typer.Option(None),
    ref_id: str | None = typer.Option(None, help="Recorded in the scan's metadata."),
    scanimage_cmd: str = typer.Option("scanimage"),
) -> None:
    """Apply a reference's scanner options, verify them, and acquire a scan."""
    data = json.loads(options_from.read_text(encoding="utf-8"))
    scanner_options = data.get("scanner_options", data)  # accept reference.json or raw options

    mismatches = scanner_mod.apply_and_verify(
        scanner_options, device=device, scanimage_cmd=scanimage_cmd
    )
    if mismatches:
        typer.echo("Refusing to scan: scanner options did not verify:", err=True)
        for name, (requested, actual) in mismatches.items():
            typer.echo(f"  {name}: requested {requested!r}, actual {actual!r}", err=True)
        raise typer.Exit(code=1)

    depth = int(scanner_options.get("depth", 16))
    result = scanner_mod.acquire_scan(
        output,
        device=device,
        resolution_dpi=int(scanner_options.get("resolution"))
        if "resolution" in scanner_options
        else None,
        mode=scanner_options.get("mode", "Color"),
        depth=depth,
        scanimage_cmd=scanimage_cmd,
    )
    for w in result.warnings:
        typer.echo(f"warning: {w}", err=True)

    # Re-read and re-write with embedded metadata (the scanimage call itself
    # doesn't know about stampan's ScanMetadata convention).
    from datetime import UTC, datetime

    from stampan.core.io import write_tiff

    image, _ = read_tiff(output)
    write_tiff(
        output,
        image,
        ScanMetadata(
            scan_time=datetime.now(UTC).isoformat(),
            scanner_options=scanner_options,
            ref_id=ref_id,
        ),
    )
    typer.echo(f"wrote {output} ({result.bit_depth}-bit)")


def _parse_floats(s: str) -> list[float]:
    return [float(x) for x in s.split(",")]


@reference_app.command("create")
def reference_create(
    scan_path: Path = typer.Option(..., "--scan", help="Reference scan TIFF."),
    name: str = typer.Option(...),
    it8_corners: str = typer.Option(
        ..., help="a1x,a1y,a22x,a22y,l1x,l1y,l22x,l22y (patch centres, in px)"
    ),
    it8_gs_corners: str | None = typer.Option(
        None,
        help="gs0x,gs0y,gs23x,gs23y (grey-ramp row centres, in px). Strongly recommended: "
        "the GS row is a separate strip, not a simple extrapolation below row L -- see "
        "core/calibration/it8.py. Omitting it falls back to that unreliable extrapolation.",
    ),
    ruler_x: str | None = typer.Option(
        None,
        help="cx,cy,length_px,width_px,angle_deg,tick_spacing_mm,px_per_mm_hint. "
        "Omit if the scan has no ruler -- the reference is then colour-only, with no scale.",
    ),
    ruler_y: str | None = typer.Option(
        None, help="Same format as --ruler-x; omit to assume y scale = x scale."
    ),
    cgats: Path = typer.Option(DEFAULT_CGATS_PATH, help="CGATS/IT8 reference data file."),
    library_root: Path = typer.Option(DEFAULT_LIBRARY_ROOT),
    notes: str = typer.Option(""),
) -> None:
    """Build and save a Reference from a scan, IT8 corners, and (optionally) ruler region(s)."""
    scan_image, scan_metadata = read_tiff(scan_path)

    x_axis = None
    if ruler_x:
        rx = _parse_floats(ruler_x)
        x_axis = ref_mod.RulerAxisSpec(
            centre_xy=(rx[0], rx[1]),
            length_px=rx[2],
            width_px=rx[3],
            angle_deg=rx[4],
            tick_spacing_mm=rx[5],
            px_per_mm_hint=rx[6],
        )
    y_axis = None
    if ruler_y:
        ry = _parse_floats(ruler_y)
        y_axis = ref_mod.RulerAxisSpec(
            centre_xy=(ry[0], ry[1]),
            length_px=ry[2],
            width_px=ry[3],
            angle_deg=ry[4],
            tick_spacing_mm=ry[5],
            px_per_mm_hint=ry[6],
        )

    c = _parse_floats(it8_corners)
    gs0 = gs23 = None
    if it8_gs_corners:
        g = _parse_floats(it8_gs_corners)
        gs0, gs23 = (g[0], g[1]), (g[2], g[3])
    corners = it8_mod.IT8Corners(
        a1=(c[0], c[1]), a22=(c[2], c[3]), l1=(c[4], c[5]), l22=(c[6], c[7]), gs0=gs0, gs23=gs23
    )

    result = ref_mod.build_reference(
        name=name,
        scan=scan_image,
        scan_metadata=scan_metadata,
        x_axis=x_axis,
        it8_corners=corners,
        cgats_reference_path=cgats,
        y_axis=y_axis,
        notes=notes,
    )
    ref_dir = ref_mod.save_reference(result, library_root)
    typer.echo(f"saved reference {result.reference.ref_id} to {ref_dir}")
    typer.echo(json.dumps(ref_mod.reference_summary(result.reference), indent=2))
    if result.reference.notes:
        typer.echo(result.reference.notes)


@reference_app.command("list")
def reference_list(library_root: Path = typer.Option(DEFAULT_LIBRARY_ROOT)) -> None:
    refs = ref_mod.list_references(library_root)
    if not refs:
        typer.echo("no references found.")
        return
    for r in refs:
        typer.echo(f"{r.ref_id}  {r.name}  ({r.created})")


@reference_app.command("show")
def reference_show(ref_id: str, library_root: Path = typer.Option(DEFAULT_LIBRARY_ROOT)) -> None:
    reference = ref_mod.load_reference(library_root, ref_id)
    typer.echo(json.dumps(ref_mod.reference_summary(reference), indent=2))


@app.command()
def detect(
    sample_path: Path = typer.Argument(..., help="Sample-sheet scan TIFF."),
    reference: str = typer.Option(..., "--reference", help="Reference id (for px/mm scale)."),
    project: str = typer.Option(..., "--project", help="Project name (reused if it exists)."),
    library_root: Path = typer.Option(DEFAULT_LIBRARY_ROOT),
    margin_mm: float = typer.Option(3.0, help="Black margin around tooth tips, in mm."),
) -> None:
    """Detect stamps on a sample sheet and write crops + stamp.json (SPEC §5)."""
    ref = ref_mod.load_reference(library_root, reference)
    if ref.scale is None:
        typer.echo(f"reference {reference} has no scale calibration.", err=True)
        raise typer.Exit(code=1)

    scan_image, scan_metadata = read_tiff(sample_path)
    scan_metadata.ref_id = reference

    proj = project_mod.get_or_create_project(library_root, project)
    sample = project_mod.add_sample(library_root, proj, scan_image, scan_metadata, reference)

    result = detection_mod.detect_stamps(scan_image, ref.scale.px_per_mm_x, ref.scale.px_per_mm_y)
    overview_png = detection_mod.render_overview(scan_image, result)

    crops = []
    for i, detected in enumerate(result.stamps):
        crop, transform = detection_mod.extract_crop(
            scan_image, detected, ref.scale.px_per_mm_x, ref.scale.px_per_mm_y, margin_mm
        )
        crops.append(crop)
        width_mm = detected.size_px[0] / ref.scale.px_per_mm_x
        height_mm = detected.size_px[1] / ref.scale.px_per_mm_y
        stamp = Stamp(
            stamp_id=project_mod.next_stamp_id(sample, i),
            geometry=GeometryResult(
                width_mm=width_mm,
                height_mm=height_mm,
                rotation_deg=transform.angle_deg,
                sample_centre_px=transform.sample_centre_px,
                crop_size_px=transform.crop_size_px,
            ),
        )
        project_mod.save_stamp(library_root, sample, stamp, crop)

    contact_sheet_png = detection_mod.render_contact_sheet(crops)
    project_dir = project_mod.project_dir(library_root, proj.project_id)
    (project_dir / "diagnostics").mkdir(parents=True, exist_ok=True)
    (project_dir / "diagnostics" / f"{sample.sample_id}_overview.png").write_bytes(overview_png)
    (project_dir / "diagnostics" / f"{sample.sample_id}_contact_sheet.png").write_bytes(
        contact_sheet_png
    )

    project_mod.save_sample(library_root, sample)

    typer.echo(f"detected {len(result.stamps)} stamp(s) in {sample_path}")
    flagged = sum(1 for s in result.stamps if s.flagged_for_manual_review)
    if flagged:
        typer.echo(f"  {flagged} flagged for manual review")
    for w in result.warnings:
        typer.echo(f"warning: {w}", err=True)
    typer.echo(
        f"wrote sample {sample.sample_id} and {len(result.stamps)} stamp(s) to {project_dir}"
    )


def _resolve_project(library_root: Path, project: str) -> project_mod.Project:
    by_name = project_mod.find_project_by_name(library_root, project)
    if by_name is not None:
        return by_name
    return project_mod.load_project(library_root, project)


@app.command()
def perf(
    project: str = typer.Argument(..., help="Project name or id."),
    stamp: str | None = typer.Option(None, "--stamp", help="Only this stamp id."),
    library_root: Path = typer.Option(DEFAULT_LIBRARY_ROOT),
) -> None:
    """Measure perforation gauge on all (or one) stamp(s) in a project (SPEC §6)."""
    proj = _resolve_project(library_root, project)

    scale_cache: dict[str, tuple[float, float]] = {}
    stamp_to_sample: dict[str, str] = {}
    for sample_id in proj.sample_ids:
        sample = project_mod.load_sample(library_root, proj.project_id, sample_id)
        for stamp_id in sample.stamp_ids:
            stamp_to_sample[stamp_id] = sample_id
        if sample.ref_id not in scale_cache:
            ref = ref_mod.load_reference(library_root, sample.ref_id)
            if ref.scale is None:
                typer.echo(f"reference {sample.ref_id} has no scale; skipping its stamps", err=True)
                continue
            scale_cache[sample.ref_id] = (ref.scale.px_per_mm_x, ref.scale.px_per_mm_y)

    stamp_ids = [stamp] if stamp else list(stamp_to_sample.keys())
    if not stamp_ids:
        typer.echo("no stamps found in this project.", err=True)
        raise typer.Exit(code=1)

    n_ok = 0
    for stamp_id in stamp_ids:
        sample_id = stamp_to_sample.get(stamp_id)
        if sample_id is None:
            typer.echo(f"warning: stamp {stamp_id} not found in project", err=True)
            continue
        sample = project_mod.load_sample(library_root, proj.project_id, sample_id)
        scale = scale_cache.get(sample.ref_id)
        if scale is None:
            typer.echo(f"warning: no scale for stamp {stamp_id}'s reference; skipping", err=True)
            continue
        px_per_mm_x, px_per_mm_y = scale

        crop = project_mod.load_stamp_crop(library_root, proj.project_id, stamp_id)
        analysis = perforation_mod.analyse_perforation(crop, px_per_mm_x, px_per_mm_y)
        diagnostic_png = perforation_mod.render_diagnostic(crop, analysis)
        notation = perforation_mod.perforation_result_notation(analysis.sides)

        stamp_obj = project_mod.load_stamp(library_root, proj.project_id, stamp_id)
        stamp_obj.perforation.auto = {
            name: side.to_persisted() for name, side in analysis.sides.items()
        }
        stamp_obj.perforation.meta.confidence = analysis.confidence
        stamp_obj.perforation.meta.warnings = analysis.warnings
        stamp_obj.perforation.meta.diagnostic_image = "diagnostics/perforation.png"
        project_mod.save_stamp_results(
            library_root,
            proj.project_id,
            stamp_obj,
            diagnostics={"perforation.png": diagnostic_png},
        )
        n_ok += 1
        typer.echo(f"{stamp_id}: {notation} (confidence {analysis.confidence:.2f})")
        for w in analysis.warnings:
            typer.echo(f"  warning: {w}", err=True)

    typer.echo(f"measured perforation on {n_ok}/{len(stamp_ids)} stamp(s)")


if __name__ == "__main__":
    app()
