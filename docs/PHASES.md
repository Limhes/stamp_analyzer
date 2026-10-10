# Development phases: prompts for Claude Code

Paste one prompt per session, in order. Each phase ends with a review by René.
The full design is in `docs/SPEC.md`, and the working rules are in `CLAUDE.md`.

General acceptance for every phase:

- `uv run pytest` passes and `ruff` is clean.
- A script in `scripts/` runs the new module over the test data (`STAMPAN_TESTDATA`)
  and writes diagnostic images plus a summary to `out/<phase>/`.
- The phase ends with a short report: what was built, the numbers, the weak spots,
  and any proposed spec changes.

---

## Phase 0: Scaffold, data model and IT8 data

```
Read CLAUDE.md and docs/SPEC.md completely, including §13 (prior work in the
parent folder: skim it so you know what exists, but don't build on it). Then do phase 0:

1. Create the project skeleton from SPEC §3 with uv (Python 3.12), the console entry
   points `stampan` and `stampan-gui` (the GUI is a stub for now), ruff and pytest
   configuration, and a .gitignore (out/, .venv, *.tif, testdata).
2. Implement core/cgats.py: a robust CGATS/IT8.7 parser. It handles keywords, the data
   format and data blocks, trailing whitespace, and comments. Parse
   reference_data/R240103.txt into a table indexed by SAMPLE_ID. Tests: 288 sets,
   A1 and GS23 values, and Lab recomputed from XYZ (D50) matches the file within 0.05.
3. Implement core/io.py: 16-bit TIFF read/write with tifffile, with embedded JSON
   metadata (scan time, scanner options, ref_id).
4. Define the result dataclasses and JSON (de)serialisation for every module in
   SPEC §3 (Reference, Sample, Stamp, PerforationResult, CancellationResult,
   ColourResult), keeping auto results and manual edits as separate fields. Add
   tests for the round trip.
5. Write the synthetic stamp generator (tests/synth.py) from SPEC §6.4. It takes a
   gauge per side (or imperf), size, rotation, noise, blur, hole-edge roughness, a
   dark background with haze, and an optional glassine strip-edge line. It returns
   the image plus its ground truth. Write a few example PNGs to out/phase0/.

Stop after this and report.
```

## Phase 1: Scanner capture and calibration (geometry + colour)

```
Phase 1, following SPEC §4 (and §2 for the scanner).

1. core/scanner.py: a wrapper around scanimage. It lists devices, dumps all options
   (-A) into a structured dict, selects the colour/geometry-relevant subset, applies
   options, verifies them by reading back, and acquires a scan to a 16-bit TIFF (or
   8-bit if that's all the backend can do; record it). Add a CLI command:
   `stampan scan --options-from <reference.json|options.json> -o file.tif`.
   Also add `stampan scanner-info`, which prints the backend options so René can check
   that bit depth, gamma and auto settings can be controlled on the Epson 1200U.
   Make the code mockable; tests use a fake scanimage.
2. calibration/geometry.py: ruler tick detection and the x/y scale fit, following
   SPEC §4.2. Since the ruler isn't chosen yet, take the tick spacing as a parameter
   and build the region selection so it works with a rough user-given rectangle and
   angle. Diagnostic: tick detections and a residual plot. Test on synthetic rulers
   (known scale, blur, noise, rotation).
3. calibration/it8.py: four-corner-click grid model for the Wolf Faust IT8.7/2 layout
   (A–L × 1–22 plus the GS0–GS23 row), with trimmed-mean patch RGB and per-patch
   std. Provide a CLI that takes the corner coordinates as arguments (the GUI comes
   later). Diagnostic: the scan with all sampled patch areas drawn and labelled.
4. calibration/characterisation.py: grey-ramp linearisation if needed, then models
   (a) 3x3, (b) root-polynomial degree 2 and 3, (c) an optional ArgyllCMS cross-check
   if `colprof` is installed, and (d) LinearNDInterpolator as a reference point. Also
   predict the patches with the old LUT profile
   ../ColorIdentification/color_detect_gui/icc_profiles/epson1200u.icc (LittleCMS) and
   report its ΔE00 for comparison only. Report training and k-fold or
   leave-one-out ΔE00 (mean, median, P95, max) and a per-patch error heat map on the
   target layout. Choose the model automatically as SPEC §4.3 describes, but report all.
5. calibration/reference.py: build, save and load the Reference (SPEC §3.1 / §4.4),
   with the CLI `stampan reference create --scan ... --name ... --ruler ... --it8-corners ...`
   and `stampan reference list/show`.

If René's real reference scan is not yet in testdata, develop on synthetic data and
say clearly which numbers come from synthetic data. Stop and report, including the
real cross-validated ΔE00 if the data is there.
```

**Status (2026-10-09): geometric calibration and IT8 patch location both replaced**,
once a real Epson 1200U was available to test against (see SPEC §4.2/§4.3 for the
full rationale and the concrete evidence from each change):

- `calibration/geometry.py` (ruler tick detection) is **deleted**. Geometric
  calibration now comes directly from the scanner's own verified resolution
  setting (`dpi / 25.4 = px/mm`), not a ruler fit.
- `calibration/it8.py`'s four-corner-click grid model is **deleted**.
  `calibration/scanin.py` (new) shells out to ArgyllCMS's `scanin` for fully
  automatic patch location -- no manual corners, no GUI point-marking at all.
  ArgyllCMS (`argyll` + `argyll-ref`) changed from an optional cross-check
  dependency to a required one as a direct result.
- Two real, hardware-specific bugs were found and fixed during this work, both now
  documented in SPEC §2/§4.1: `apply_and_verify` needing a single `scanimage`
  invocation (state doesn't persist across separate processes on this backend), and
  `--gamma-correction=Default` not actually being linear (an identity
  user-defined gamma table is the real linear/off equivalent -- this alone was the
  difference between a cross-validated ΔE00 of ~6-8 and ~2).
- `calibration/reference.py`'s `build_reference()` signature changed accordingly:
  no more `it8_corners`; `dpi_x`/`dpi_y`/`dpi_source` replace the ruler axis specs.

## Phase 2: Stamp detection and extraction

```
Phase 2, following SPEC §5.

Implement core/detection.py: automatic detection on the black stock-card background,
with suppression of glassine strip-edge lines, watershed splitting of touching
stamps, size and fill filtering, and minAreaRect. Produce upright 16-bit crops with
a ≥ 3 mm black margin and the stored back-transform. Add the CLI
`stampan detect <sample.tif> --reference <ref_id> --project <name>`, which writes the
crops and stamp.json files.

Diagnostic: the sample overview with numbered rotated rectangles, flagged regions in
a different colour, and a contact sheet of all crops.
Validate on all samples in testdata. Report detected vs expected counts (from
ground_truth.yaml), false positives and misses, and the rotation estimates.
Stop and report.
```

## Phase 3: Perforation

```
Phase 3, following SPEC §6. This is the core metrology. Be rigorous.

1. Implement core/perforation.py with both estimators (spectral and hole-position
   regression), tip and bottom envelope fits, corner exclusion, per-axis calibrated
   scale, the confidence logic, imperf and straight-edge detection, hole diameter,
   spacing regularity and tooth roughness. Manual mode: a function that takes
   user-clicked points per side and returns the regression result. Overrides stay
   separate.
2. Diagnostic images as in SPEC §6.3 (annotated stamp plus per-side profile and
   spectrum plots).
3. Synthetic validation (SPEC §6.4): a sweep over gauges 8–16 in 0.25 steps,
   rotations, noise and blur levels. Produce an error table and plot. Requirement:
   |error| < 0.05 for sides with ≥ 10 holes. Investigate any failure; don't tune to
   the test.
4. Real validation against ground_truth.yaml: a table of measured vs hand-gauged
   values per side, with the confidence.
5. CLI: `stampan perf <project> [--stamp id]`.

Discuss the error budget in the report: scale uncertainty, sub-pixel edge precision,
the number of holes, and paper shrinkage (which is real and philatelically relevant,
so don't "correct" it). Stop and report.
```

## Phase 4: Colour and ISCC–NBS naming

```
Phase 4, following SPEC §8 and §9.

1. core/colour.py: the interior mask (from the perforation hole-bottom lines),
   exclusions (cancel mask if present, user zones, near-saturation pixels,
   strip-edge lines), conversion to Lab via the reference model, the GMM with
   k-means initialisation, paper/ink labelling, posterior threshold τ with mixed-pixel
   handling, the core-ink estimate, all statistics from §8.5 (circular hue mean!),
   and fixed-means re-assignment for user-moved centroids. Results frozen with
   parameters.
2. core/naming.py: Lab D50 → illuminant C (document the CAT) → xyY → Munsell
   (colour-science) → ISCC–NBS via Centore's block logic. Start from René's port at
   Port it from Centore's MATLAB original in reference_data/iscc_nbs/ (SPEC §9). Two
   existing Python ports disagree, so don't trust either without checking it against
   the .m file. Wrap the result in a tested module, and use
   ../rnd_projects/rgb_to_munsell/sRGBToMunsellAndISCCNBS.csv as a regression test (report the
   agreement rate and analyse the disagreements; the Munsell conversion
   implementation differs slightly from Centore's, so a small disagreement near
   block boundaries is expected).
3. Diagnostics: the stamp with cluster overlays, the a*b* and C*L* scatter plots with
   ellipses, a static 3D scatter render, and a swatch card with all the numbers and
   names.
4. CLI: `stampan colour <project> [--stamp id] [--inks N] [--tau 0.9]`.

Report the repeatability: analyse the same stamp from two separate scans (if the
test data has them) and give the ΔE00 between runs. Stop and report.
```

## Phase 5: Cancellation -- built, then dropped (2026-10-03)

```
Phase 5, following SPEC §7. Build the classical pipeline: the colour-model-based
outlier map, cancel-colour priors (black / blue / violet / red), the optional
ellipse hint, Hough circle and arc and line evidence, probability-map fusion,
hysteresis threshold, and faint-cancel enhancement. Provide the mask edit API
(add/remove polygons or brush strokes) with storage of auto vs manual masks, and
save corrected masks so a training set accumulates.

Outputs: the mask, the cancel colour (Lab/LCh and class), and the "light stamp +
dark cancel" visualisation plus the isolated cancel image. Then wire it into
colour.py, so that the colour analysis automatically excludes the cancel mask.

Validate on the cancelled stamps in ground_truth.yaml. There is no pixel ground
truth, so produce a review sheet (original | probability map | mask | visualisation)
per stamp for René to judge, and report honestly where it fails (e.g. a black
cancel on dark ink). Stop and report.
```

This was built exactly as specified and run against the real stamps. René's review:
it didn't work well enough to trust -- most real stamps landed in the pipeline's own
"ill-posed" bucket, and the confident-looking results weren't reliable either.
Decision: drop automatic cancellation detection entirely (SPEC §7) rather than ship
something unreliable. `core/cancellation.py` and its tests were deleted; `colour.py`'s
mandatory inclusion-ROI workflow (SPEC §8.1) replaces it -- the user selects a
cancellation-free area directly instead of relying on auto-exclusion. See SPEC §7
for the full record. Phases 6/7 below are adjusted accordingly (no cancel step, no
Cancellation GUI panel).

## Phase 6: Report (headless pipeline complete)

```
Phase 6, following SPEC §10. Jinja2 + WeasyPrint, A4: overview page(s) plus one page
per stamp with everything listed in §10. Use a clean, sober, scientific layout:
consistent typography, numbers aligned with units, swatches with their numerical
values, small plots generated with matplotlib as SVG. Also add the CSV/JSON export.

Add `stampan run <sample.tif> --reference <id> --project <name>`, which runs
detect → perf → colour → report fully automatically, and `stampan report
<project>`. Generate a report from the test data for review. Stop and report.
```

(No `--cancel` step: cancellation detection was dropped, see Phase 5 above.)

## Phase 7: GUI

```
Phase 7, following SPEC §12. Build the PySide6 application on top of the existing
core. Look at the GUI patterns in ../rnd_projects/absolute_color_kiro/main.py
(SPEC §13) for zoom/pan/selection views, the thumbnail table and the a*b* plot with the
gamut boundary. Re-implement them cleanly instead of copying its colour code:

1. The main window: library tree, image viewer (QGraphicsView, smooth zoom/pan on
   large 16-bit images via a display-scaled 8-bit pyramid, a pixel readout in px / mm
   / RGB / Lab) and the context panel.
2. Viewer tools: rotated rectangle, ellipse, polygon, points, brush/lasso, and
   inclusion/exclusion ROIs, all with undo/redo.
3. Panels: Detection (edit/add/delete rectangles, re-extract), Perforation (a
   dedicated view, click-to-mark teeth/holes with snapping, per-side overrides with a
   reason), Colour (a pyqtgraph.opengl 3D cloud with ellipsoids and centroids, a
   linked 2D overlay, N/τ controls, draggable centroids, Accept, and the inclusion
   ROI tool for selecting a cancellation-free area -- no separate Cancellation panel,
   see Phase 5 above), and Notes (catalogue numbers etc.).
4. A workflow step bar with per-stamp status, worker threads with progress/cancel,
   and Generate report.

Keep all logic in core. If you need logic in the GUI, move it into core first.
Stop and report with screenshots.
```

## Phase 8: Scanner and reference management in the GUI

```
Phase 8: GUI integration of the scanner and references (SPEC §4.1, §4.4):

- Reference wizard: scan or load → ruler region(s) → click the 4 IT8 corners →
  fit → review the validation (ΔE00 table, heat map, residuals) → name and save.
- Reference manager: list, details, rename, archive, compare two references (ΔE00 of
  model predictions over the patch set, scale differences).
- Sample scan: load a reference → apply and verify the options (block on mismatch)
  → warm-up handling → preview scan → full scan → automatic detection.
- Also the drift check-patch hook from §4.4 (design only, unless trivial).
Stop and report.
```

**Status (2026-10-09): reference wizard simplified to match Phase 1's status
note above.** With both the ruler and the corner-click grid gone, the actual flow
is: scan the IT8 target live (device selection, option apply-and-verify per §4.1,
acquire) or load an existing TIFF → **Build & review** directly, no point-marking
step of any kind → name and save. `scanin` runs automatically inside
`build_reference()`. The wizard's resolution field is still pre-filled from the
scan's verified/embedded dpi as originally planned. Validated end to end against a
real 1200dpi scan: ΔE00 mean 1.99, matching (slightly beating) the best
ruler-and-corner-click-era reference (2.36) -- and robust to where the target sits
on the bed, confirmed across three separate physical placements/rotations during
testing, none requiring any manual input. Reference manager (list/rename/archive/
compare) and the sample-scan flow are implemented as originally specced; the drift
check-patch hook remains a design-only stub.

**Refactor (2026-10-09), no new features.** Steps duplicated between CLI and GUI
moved into `core/workflow.py`. The 2300-line `gui/panels.py` was replaced by pages,
dialogs and shared widgets. The GUI got a menu bar, toolbars, docks, a theme, SVG
icons, a single status-bar busy indicator, readable error dialogs and persistent
settings (SPEC §12). Every test passes; there is a new offscreen GUI smoke test.

## Phase 9: Hardening

```
Phase 9: hardening. Profile performance on full 1200-dpi sheets (memory, speed).
Add error handling and logging, settings (library root, defaults for τ, margins and
tick spacing), and a user guide in docs/USER_GUIDE.md (workflow, with the
measurement-uncertainty discussion). Run a full end-to-end test on the complete test
data set and list the remaining issues by priority.
```
