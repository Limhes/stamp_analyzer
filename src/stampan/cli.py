"""`stampan` command-line entry point. Commands are added phase by phase."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import typer

from stampan import __version__
from stampan.core import colour as colour_mod
from stampan.core import philatelic_colours as philatelic_colours_mod
from stampan.core import project as project_mod
from stampan.core import scanner as scanner_mod
from stampan.core import workflow
from stampan.core.calibration import reference as ref_mod
from stampan.core.io import read_tiff, write_tiff
from stampan.core.workflow import DEFAULT_CGATS_PATH, DEFAULT_LIBRARY_ROOT

app = typer.Typer(
    name="stampan",
    help="Calibrated measurement of postage stamps from flatbed scans.",
    no_args_is_help=True,
)
reference_app = typer.Typer(help="Manage calibration references (SPEC §4.4).")
app.add_typer(reference_app, name="reference")

DEFAULT_PHILATELIC_TABLE_PATH = philatelic_colours_mod.DEFAULT_TABLE_PATH
DEFAULT_INTERIOR_MARGIN_MM = 0.6  # typical perforation depth (~0.3-0.5mm) + 0.3mm safety


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
    try:
        captured = workflow.capture_scan(
            scanner_options, device=device, ref_id=ref_id, scanimage_cmd=scanimage_cmd
        )
    except scanner_mod.ScannerError as exc:
        typer.echo(f"Refusing to scan: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    for w in captured.warnings:
        typer.echo(f"warning: {w}", err=True)
    write_tiff(output, captured.image, captured.metadata)
    note = ", upscaled to 16-bit" if captured.metadata.extra.get("upscaled_from_8bit") else ""
    typer.echo(f"wrote {output} ({captured.source_bit_depth}-bit{note})")


def _parse_floats(s: str) -> list[float]:
    return [float(x) for x in s.split(",")]


@reference_app.command("create")
def reference_create(
    scan_path: Path = typer.Option(..., "--scan", help="Reference scan TIFF."),
    name: str = typer.Option(...),
    dpi: float | None = typer.Option(
        None,
        help="Scan resolution (dpi), for px/mm (SPEC §4.1/§4.2 -- no ruler). Defaults to the "
        "scan's own embedded scanner_options['resolution'] if present (a verified setting, "
        "for a scan this app itself captured); otherwise required.",
    ),
    dpi_y: float | None = typer.Option(
        None, help="Y resolution, if it differs from --dpi. Omit to assume isotropic."
    ),
    cgats: Path = typer.Option(DEFAULT_CGATS_PATH, help="CGATS/IT8 reference data file."),
    library_root: Path = typer.Option(DEFAULT_LIBRARY_ROOT),
    notes: str = typer.Option(""),
) -> None:
    """Build and save a Reference from a scan. IT8 patch location is fully
    automatic via ArgyllCMS's `scanin` (SPEC §4.3) -- no corners needed."""
    scan_image, scan_metadata = read_tiff(scan_path)

    dpi_source = "typed by hand (--dpi)"
    if dpi is None:
        embedded = scan_metadata.scanner_options.get("resolution")
        if embedded is not None:
            dpi = float(embedded)
            dpi_source = "verified scanner setting (embedded scan metadata)"

    result = ref_mod.build_reference(
        name=name,
        scan=scan_image,
        scan_metadata=scan_metadata,
        dpi_x=dpi,
        dpi_y=dpi_y,
        dpi_source=dpi_source,
        cgats_reference_path=cgats,
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
    scan_image, scan_metadata = read_tiff(sample_path)
    proj = project_mod.get_or_create_project(library_root, project)
    try:
        result = workflow.ingest_sample(
            library_root, proj, reference, scan_image, scan_metadata, margin_mm=margin_mm
        )
    except workflow.WorkflowError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"detected {result.n_stamps} stamp(s) in {sample_path}")
    if result.n_flagged:
        typer.echo(f"  {result.n_flagged} flagged for manual review")
    for w in result.warnings:
        typer.echo(f"warning: {w}", err=True)
    project_dir = project_mod.project_dir(library_root, proj.project_id)
    typer.echo(
        f"wrote sample {result.sample.sample_id} and {result.n_stamps} stamp(s) to {project_dir}"
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
    """Measure perforation gauge on all (or one) stamp(s) in a project (SPEC §6).

    Each stamp keeps its own saved box and gauge range (e.g. from the GUI).
    """
    proj = _resolve_project(library_root, project)
    if not project_mod.stamp_index(library_root, proj):
        typer.echo("no stamps found in this project.", err=True)
        raise typer.Exit(code=1)

    items = workflow.measure_project_perforation(
        library_root, proj, stamp_ids=[stamp] if stamp else None
    )
    n_ok = 0
    for item in items:
        if item.measurement is None:
            typer.echo(f"warning: skipping {item.stamp_id}: {item.skipped}", err=True)
            continue
        n_ok += 1
        analysis = item.measurement.analysis
        typer.echo(
            f"{item.stamp_id}: {item.measurement.notation} (confidence {analysis.confidence:.2f})"
        )
        for w in analysis.warnings:
            typer.echo(f"  warning: {w}", err=True)
    typer.echo(f"measured perforation on {n_ok}/{len(items)} stamp(s)")


@app.command()
def colour(
    project: str = typer.Argument(..., help="Project name or id."),
    stamp: str | None = typer.Option(None, "--stamp", help="Only this stamp id."),
    inks: int = typer.Option(1, "--inks", help="Number of ink clusters (N), SPEC §8.3."),
    tau: float = typer.Option(0.9, "--tau", help="Posterior threshold for mixed pixels."),
    margin_mm: float = typer.Option(
        DEFAULT_INTERIOR_MARGIN_MM,
        help="Interior-mask margin past the stamp edge, in mm (SPEC §8.1).",
    ),
    philatelic_table_path: Path = typer.Option(
        DEFAULT_PHILATELIC_TABLE_PATH, help="Philatelic colour-name reference (SPEC §9)."
    ),
    include_rect: str | None = typer.Option(
        None,
        "--include-rect",
        help=(
            "Restrict analysis to this crop-px rectangle 'x0,y0,x1,y1' -- e.g. a clean "
            "patch of paper or design ink, away from any cancellation (SPEC §8.1's "
            "inclusion ROI; there is no automatic cancellation exclusion, see colour.py)."
        ),
    ),
    include_mask: Path | None = typer.Option(
        None,
        "--include-mask",
        help=(
            "Restrict analysis to this single-channel mask PNG (same px size as the "
            "crop; nonzero = included) -- for an arbitrary (non-rectangular) inclusion "
            "ROI, e.g. one drawn in the GUI. Mutually exclusive with --include-rect."
        ),
    ),
    library_root: Path = typer.Option(DEFAULT_LIBRARY_ROOT),
) -> None:
    """Measure paper/ink colour on all (or one) stamp(s) in a project (SPEC §8-9)."""
    if include_rect is not None and include_mask is not None:
        typer.echo("--include-rect and --include-mask are mutually exclusive", err=True)
        raise typer.Exit(code=1)

    proj = _resolve_project(library_root, project)
    philatelic_table = philatelic_colours_mod.load_philatelic_colours(philatelic_table_path)

    inclusion_rect: tuple[int, int, int, int] | None = None
    if include_rect is not None:
        values = _parse_floats(include_rect)
        if len(values) != 4:
            typer.echo("--include-rect must be 'x0,y0,x1,y1'", err=True)
            raise typer.Exit(code=1)
        inclusion_rect = tuple(int(v) for v in values)

    inclusion_mask_from_file = None
    if include_mask is not None:
        import cv2

        inclusion_mask_from_file = cv2.imread(str(include_mask), cv2.IMREAD_GRAYSCALE)
        if inclusion_mask_from_file is None:
            typer.echo(f"could not read --include-mask {include_mask}", err=True)
            raise typer.Exit(code=1)

    model_cache: dict[str, ref_mod.ColourModel] = {}
    scale_cache: dict[str, tuple[float, float]] = {}
    cv_delta_e00_cache: dict[str, float] = {}
    stamp_to_sample: dict[str, str] = {}
    for sample_id in proj.sample_ids:
        sample = project_mod.load_sample(library_root, proj.project_id, sample_id)
        for stamp_id in sample.stamp_ids:
            stamp_to_sample[stamp_id] = sample_id
        if sample.ref_id not in model_cache:
            ref = ref_mod.load_reference(library_root, sample.ref_id)
            if ref.scale is None or ref.colour_model is None:
                typer.echo(
                    f"reference {sample.ref_id} missing scale or colour model; skipping its stamps",
                    err=True,
                )
                continue
            model_cache[sample.ref_id] = ref_mod.load_colour_model(library_root, sample.ref_id)
            scale_cache[sample.ref_id] = (ref.scale.px_per_mm_x, ref.scale.px_per_mm_y)
            cv_delta_e00_cache[sample.ref_id] = ref.colour_model.cv_delta_e00.get("mean")

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
        if sample.ref_id not in model_cache:
            typer.echo(f"warning: no usable reference for stamp {stamp_id}; skipping", err=True)
            continue
        px_per_mm_x, px_per_mm_y = scale_cache[sample.ref_id]
        model = model_cache[sample.ref_id]

        crop = project_mod.load_stamp_crop(library_root, proj.project_id, stamp_id)

        inclusion_mask = None
        if inclusion_rect is not None:
            x0, y0, x1, y1 = inclusion_rect
            inclusion_mask = np.zeros(crop.shape[:2], dtype=np.uint8)
            inclusion_mask[y0:y1, x0:x1] = 255
        elif inclusion_mask_from_file is not None:
            inclusion_mask = inclusion_mask_from_file

        analysis = colour_mod.analyse_colour(
            crop,
            model.predict_lab,
            px_per_mm_x,
            px_per_mm_y,
            perforation_margin_mm=margin_mm,
            n_inks=inks,
            tau=tau,
            user_inclusion_mask=inclusion_mask,
            philatelic_table=philatelic_table,
            ref_id=sample.ref_id,
            colour_model_cv_delta_e00=cv_delta_e00_cache[sample.ref_id],
        )

        diagnostics = {
            "colour_clusters.png": colour_mod.render_cluster_overlay(crop, analysis),
            "colour_scatter.png": colour_mod.render_scatter_plots(analysis),
            "colour_swatches.png": colour_mod.render_swatch_card(analysis),
        }

        stamp_obj = project_mod.load_stamp(library_root, proj.project_id, stamp_id)
        stamp_obj.colour = analysis.result
        stamp_obj.colour.meta.diagnostic_image = "diagnostics/colour_swatches.png"
        project_mod.save_stamp_results(
            library_root, proj.project_id, stamp_obj, diagnostics=diagnostics
        )

        n_ok += 1
        summary = ", ".join(
            f"{c.label}={c.munsell or '?'} ({c.philatelic_name or '?'})"
            for c in analysis.result.clusters
        )
        typer.echo(f"{stamp_id}: {summary or 'no clusters'}")
        for w in analysis.warnings:
            typer.echo(f"  warning: {w}", err=True)

    typer.echo(f"measured colour on {n_ok}/{len(stamp_ids)} stamp(s)")


@app.command()
def report(
    project: str = typer.Argument(..., help="Project name or id."),
    output: Path | None = typer.Option(
        None, "-o", "--output", help="PDF path (default: <project>/report.pdf)."
    ),
    library_root: Path = typer.Option(DEFAULT_LIBRARY_ROOT),
) -> None:
    """Generate the PDF report plus CSV/JSON export for a project (SPEC §10)."""
    proj = _resolve_project(library_root, project)
    try:
        files = workflow.export_report(library_root, proj, pdf_path=output)
    except workflow.WorkflowError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    for note in files.notes:
        typer.echo(f"warning: {note}", err=True)
    overview_note = f", {files.colour_overview.name}" if files.colour_overview else ""
    typer.echo(
        f"wrote {files.pdf}, {files.csv.name}, {files.json.name}{overview_note} "
        f"({files.n_stamps} stamp(s))"
    )


@app.command()
def run(
    sample_path: Path = typer.Argument(..., help="Sample-sheet scan TIFF."),
    reference: str = typer.Option(..., "--reference", help="Reference id (for px/mm scale)."),
    project: str = typer.Option(..., "--project", help="Project name (reused if it exists)."),
    inks: int = typer.Option(1, "--inks", help="Number of ink clusters (N), SPEC §8.3."),
    library_root: Path = typer.Option(DEFAULT_LIBRARY_ROOT),
) -> None:
    """Run detect -> perf -> colour -> report fully automatically (SPEC Phase 6).

    Each step is the same command `stampan detect|perf|colour|report` would
    run, invoked in-process (so typer's own option defaults resolve
    correctly) rather than duplicated here. `colour` runs without an
    inclusion ROI, since this is unattended -- see its own warning about
    cancellation ink then counting as design ink.
    """
    steps = [
        ["detect", str(sample_path), "--reference", reference, "--project", project],
        ["perf", project],
        ["colour", project, "--inks", str(inks)],
        ["report", project],
    ]
    for step in steps:
        step_args = [*step, "--library-root", str(library_root)]
        typer.echo(f"$ stampan {' '.join(step_args)}")
        try:
            app(step_args, standalone_mode=False)
        except SystemExit as exc:
            if exc.code:
                typer.echo(f"'{step[0]}' failed (exit {exc.code}); stopping.", err=True)
                raise typer.Exit(code=exc.code) from exc
        except Exception as exc:  # noqa: BLE001 -- surface any step's failure and stop the chain
            typer.echo(f"'{step[0]}' failed: {exc}; stopping.", err=True)
            raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
